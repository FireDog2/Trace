"""Role contracts and application-owned evidence checks for teammate handoffs."""

from __future__ import annotations

import json
import sqlite3
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from data_analysis_agent.db import sqlite_connection_guarded, sqlite_connection_lock, utc_now
from data_analysis_agent.scope import GLOBAL_PROJECT_ID
from data_analysis_agent.team_types import AgentRole
from data_analysis_agent.workspace import WorkspaceService

Identifier = Annotated[str, Field(min_length=1, max_length=128)]
ShortText = Annotated[str, Field(min_length=1, max_length=1000)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class KnowledgeReference(Contract):
    knowledge_id: Identifier
    version: int = Field(ge=1, strict=True)


class Definition(KnowledgeReference):
    definition: ShortText


class WorkOrder(Contract):
    # None means snapshot the current project's datasets and this task's outputs.
    # An explicitly empty list means no artifact inputs.
    input_artifact_ids: tuple[Identifier, ...] | None = Field(default=None, max_length=64)
    knowledge_refs: tuple[KnowledgeReference, ...] = Field(default=(), max_length=32)
    expected_output_kind: Literal["knowledge", "query_result", "report"] | None = None
    plan_step_id: Identifier | None = None


class Handoff(Contract):
    protocol_version: Literal["1"]
    status: Literal["ready", "partial", "blocked"]
    summary: ShortText
    limitations: tuple[ShortText, ...] = Field(max_length=16)

    @model_validator(mode="after")
    def explain_incomplete(self) -> Self:
        if self.status != "ready" and not self.limitations:
            raise ValueError("Incomplete handoffs require limitations")
        return self


class KnowledgeHandoff(Handoff):
    definitions: tuple[Definition, ...] = Field(max_length=32)
    unresolved_questions: tuple[ShortText, ...] = Field(max_length=16)

    @model_validator(mode="after")
    def require_definitions(self) -> Self:
        if self.status in {"ready", "partial"} and not self.definitions:
            raise ValueError("Ready or partial knowledge handoffs require definitions")
        return self


class SqlHandoff(Handoff):
    output_artifact_ids: tuple[Identifier, ...] = Field(max_length=32)

    @model_validator(mode="after")
    def require_evidence(self) -> Self:
        if self.status in {"ready", "partial"} and not self.output_artifact_ids:
            raise ValueError("Ready or partial SQL handoffs require evidence")
        return self


class ReportHandoff(Handoff):
    report_artifact_id: Identifier | None
    chart_artifact_ids: tuple[Identifier, ...] = Field(max_length=16)
    evidence_artifact_ids: tuple[Identifier, ...] = Field(max_length=32)

    @model_validator(mode="after")
    def require_report(self) -> Self:
        if self.status in {"ready", "partial"} and self.report_artifact_id is None:
            raise ValueError("Ready or partial report handoffs require a report")
        return self


HANDOFF_TYPES: dict[AgentRole, type[Handoff]] = {
    AgentRole.KNOWLEDGE: KnowledgeHandoff,
    AgentRole.SQL: SqlHandoff,
    AgentRole.REPORT: ReportHandoff,
}
OUTPUT_KINDS = {
    AgentRole.KNOWLEDGE: "knowledge",
    AgentRole.SQL: "query_result",
    AgentRole.REPORT: "report",
}


class HandoffEvidenceError(ValueError):
    """A safe error code, never a raw path, foreign record, or query result."""


def handoff_schema(role: AgentRole | str) -> str:
    model = HANDOFF_TYPES.get(AgentRole(role))
    return json.dumps(model.model_json_schema(), ensure_ascii=False) if model else ""


def parse_handoff(role: AgentRole, text: str) -> Handoff:
    if not text or len(text.encode("utf-8")) > 64_000:
        raise ValueError("handoff_size_invalid")

    # Pydantic accepts NaN/Infinity in some numeric fields; prohibit all JSON constants.
    def reject_constant(_: str) -> None:
        raise ValueError("non_finite_json")

    json.loads(text, parse_constant=reject_constant)
    return HANDOFF_TYPES[role].model_validate_json(text)


def repair_feedback(role: AgentRole, error: ValueError) -> str:
    errors: list[dict[str, object]] = []
    if isinstance(error, ValidationError):
        errors = [
            {"field": list(item["loc"]), "reason": item["type"]}
            for item in error.errors(include_input=False, include_context=False)[:16]
        ]
    return json.dumps(
        {
            "code": "HANDOFF_SCHEMA_INVALID",
            "errors": errors,
            "schema": json.loads(handoff_schema(role)),
            "instruction": "Return only the corrected handoff JSON. No tools are available.",
            "repair_remaining": 0,
        },
        ensure_ascii=False,
    )


def file_digest(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validation_metadata(
    work_order_json: str | None, receipt_json: str | None, error_category: str | None = None
) -> dict[str, object]:
    if receipt_json:
        return json.loads(receipt_json)
    if work_order_json is None:
        return {"status": "legacy_unvalidated"}
    rejected = error_category in {"invalid_handoff", "invalid_handoff_evidence"}
    return {"status": "rejected" if rejected else "pending"}


class HandoffValidationService:
    def __init__(self, connection: sqlite3.Connection, workspace: WorkspaceService) -> None:
        self._connection = connection
        self._connection.row_factory = sqlite3.Row
        self._lock = sqlite_connection_lock(connection)
        self._workspace = workspace

    def _project(self, task_id: str) -> str:
        row = self._connection.execute(
            "SELECT project_id FROM tasks WHERE id=?", (task_id,)
        ).fetchone()
        if row is None:
            raise HandoffEvidenceError("task_not_found")
        return str(row["project_id"])

    def _artifact(self, artifact_id: str, project_id: str) -> tuple[sqlite3.Row, str]:
        row = self._connection.execute(
            "SELECT * FROM artifacts WHERE id=? AND project_id=?", (artifact_id, project_id)
        ).fetchone()
        if row is None:
            raise HandoffEvidenceError("artifact_scope_invalid")
        try:
            path = self._workspace.resolve_inside(project_id, row["path"])
            digest = file_digest(path)
        except (OSError, ValueError, RuntimeError) as exc:
            raise HandoffEvidenceError("artifact_unreadable") from exc
        return row, digest

    def _knowledge(self, ref: KnowledgeReference, project_id: str) -> sqlite3.Row:
        row = self._connection.execute(
            "SELECT * FROM business_knowledge_entries WHERE id=? AND project_id IN (?,?)",
            (ref.knowledge_id, project_id, GLOBAL_PROJECT_ID),
        ).fetchone()
        if row is None or row["version"] != ref.version:
            raise HandoffEvidenceError("knowledge_reference_invalid")
        return row

    @sqlite_connection_guarded
    def prepare_work_order(
        self, task_id: str, role: AgentRole, order: WorkOrder | None = None
    ) -> dict[str, object]:
        order = order or WorkOrder()
        project_id = self._project(task_id)
        if role not in OUTPUT_KINDS:
            raise HandoffEvidenceError("role_not_delegatable")
        kind = OUTPUT_KINDS[role]
        if order.expected_output_kind not in {None, kind}:
            raise HandoffEvidenceError("output_kind_invalid")
        if order.plan_step_id is not None:
            step = self._connection.execute(
                "SELECT 1 FROM plan_steps s JOIN plan_revisions p ON p.id=s.plan_revision_id "
                "WHERE s.id=? AND p.task_id=? AND p.revision="
                "(SELECT MAX(revision) FROM plan_revisions WHERE task_id=?)",
                (order.plan_step_id, task_id, task_id),
            ).fetchone()
            if step is None:
                raise HandoffEvidenceError("plan_step_invalid")
        ids = order.input_artifact_ids
        if ids is None:
            ids = tuple(
                row["id"]
                for row in self._connection.execute(
                    "SELECT id FROM artifacts WHERE project_id=? "
                    "AND (artifact_type='source_dataset' OR (task_id=? AND "
                    "producer_execution_id IN (SELECT id FROM tool_executions "
                    "WHERE status='succeeded'))) ORDER BY rowid",
                    (project_id, task_id),
                )
            )
        if len(ids) > 64:
            raise HandoffEvidenceError("too_many_inputs")
        inputs = {}
        for artifact_id in dict.fromkeys(ids):
            artifact, digest = self._artifact(artifact_id, project_id)
            if artifact["producer_execution_id"] is not None:
                execution = self._connection.execute(
                    "SELECT status FROM tool_executions WHERE id=?",
                    (artifact["producer_execution_id"],),
                ).fetchone()
                if execution is None or execution["status"] != "succeeded":
                    raise HandoffEvidenceError("input_execution_invalid")
            provenance = self._connection.execute(
                "SELECT content_hash FROM artifact_execution_provenance WHERE artifact_id=?",
                (artifact_id,),
            ).fetchone()
            if provenance is not None and provenance["content_hash"] != digest:
                raise HandoffEvidenceError("artifact_changed")
            inputs[artifact_id] = digest
        for ref in order.knowledge_refs:
            self._knowledge(ref, project_id)
        return {
            **order.model_dump(mode="json"),
            "protocol_version": "1",
            "input_artifact_ids": list(inputs),
            "input_hashes": inputs,
            "expected_output_kind": kind,
        }

    @sqlite_connection_guarded
    def record_outputs(
        self,
        artifact_ids: tuple[str, ...],
        *,
        task_id: str,
        execution_id: str,
        agent_id: str | None,
        run_id: str | None,
    ) -> None:
        project_id = self._project(task_id)
        records = []
        for artifact_id in dict.fromkeys(artifact_ids):
            row, digest = self._artifact(artifact_id, project_id)
            if row["producer_execution_id"] != execution_id or row["task_id"] != task_id:
                raise HandoffEvidenceError("producer_invalid")
            records.append((artifact_id, execution_id, agent_id, run_id, digest))
        with self._connection:
            self._connection.executemany(
                "INSERT INTO artifact_execution_provenance"
                "(artifact_id,execution_id,agent_id,run_id,content_hash) VALUES (?,?,?,?,?)",
                records,
            )

    @sqlite_connection_guarded
    def validate(
        self, *, agent_id: str, task_id: str, order: dict[str, object], handoff: Handoff
    ) -> dict[str, object]:
        project_id = self._project(task_id)
        input_hashes = order.get("input_hashes", {})
        checked: dict[str, str] = {}
        visiting: set[str] = set()

        def check_artifact(artifact_id: str, expected: set[str] | None = None) -> None:
            row, digest = self._artifact(artifact_id, project_id)
            if expected is not None and row["artifact_type"] not in expected:
                raise HandoffEvidenceError("artifact_type_invalid")
            if artifact_id in checked:
                return
            if artifact_id in visiting or len(visiting) + len(checked) >= 128:
                raise HandoffEvidenceError("artifact_lineage_invalid")
            if isinstance(input_hashes, dict) and artifact_id in input_hashes:
                if input_hashes[artifact_id] != digest:
                    raise HandoffEvidenceError("artifact_changed")
            else:
                provenance = self._connection.execute(
                    "SELECT p.*,e.status,e.task_id FROM artifact_execution_provenance p "
                    "JOIN tool_executions e ON e.id=p.execution_id WHERE p.artifact_id=?",
                    (artifact_id,),
                ).fetchone()
                if (
                    provenance is None
                    or provenance["agent_id"] != agent_id
                    or provenance["task_id"] != task_id
                    or provenance["status"] != "succeeded"
                    or row["task_id"] != task_id
                    or provenance["execution_id"] != row["producer_execution_id"]
                    or provenance["content_hash"] != digest
                ):
                    raise HandoffEvidenceError("artifact_provenance_invalid")
                visiting.add(artifact_id)
                for parent in self._connection.execute(
                    "SELECT input_artifact_id FROM artifact_inputs WHERE artifact_id=?",
                    (artifact_id,),
                ).fetchall():
                    check_artifact(parent[0])
                visiting.remove(artifact_id)
            checked[artifact_id] = digest

        knowledge = []
        raw_refs = order.get("knowledge_refs", [])
        if not isinstance(raw_refs, list):
            raise HandoffEvidenceError("work_order_invalid")
        for raw in raw_refs:
            ref = KnowledgeReference.model_validate(raw)
            self._knowledge(ref, project_id)
        if isinstance(handoff, KnowledgeHandoff):
            for ref in handoff.definitions:
                row = self._knowledge(ref, project_id)
                if ref.definition != row["definition"]:
                    raise HandoffEvidenceError("knowledge_definition_mismatch")
                knowledge.append({"knowledge_id": ref.knowledge_id, "version": ref.version})
        elif isinstance(handoff, SqlHandoff):
            for artifact_id in handoff.output_artifact_ids:
                check_artifact(artifact_id, {"derived_dataset", "table_preview"})
        elif isinstance(handoff, ReportHandoff):
            if handoff.report_artifact_id is not None:
                check_artifact(handoff.report_artifact_id, {"markdown_report"})
                actual = {
                    row[0]
                    for row in self._connection.execute(
                        "SELECT evidence_artifact_id FROM evidence_links WHERE report_artifact_id=?",
                        (handoff.report_artifact_id,),
                    )
                }
                if actual != set(handoff.evidence_artifact_ids):
                    raise HandoffEvidenceError("report_evidence_mismatch")
            for artifact_id in handoff.chart_artifact_ids:
                check_artifact(artifact_id, {"chart"})
            for artifact_id in handoff.evidence_artifact_ids:
                check_artifact(artifact_id)
        return {
            "protocol_version": "1",
            "status": "validated",
            "level": "reference",
            "checked_at": utc_now(),
            "artifact_hashes": checked,
            "knowledge_refs": knowledge,
        }
