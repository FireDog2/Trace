from __future__ import annotations

import json
import sqlite3

import pytest
from handoff_samples import blocked_handoff
from pydantic import ValidationError

from data_analysis_agent.domain import ArtifactType
from data_analysis_agent.handoffs import (
    HandoffEvidenceError,
    HandoffValidationService,
    KnowledgeHandoff,
    ReportHandoff,
    SqlHandoff,
    WorkOrder,
    handoff_schema,
    parse_handoff,
)
from data_analysis_agent.knowledge import BusinessKnowledgeRepository
from data_analysis_agent.providers import ModelResponse, ScriptedProvider
from data_analysis_agent.team import AgentRole, AgentRunStatus, TeammateRunner, TeamRepository
from data_analysis_agent.tools import ToolCall, ToolContext
from data_analysis_agent.workspace import WorkspaceService


def sql_result(artifact_id: str) -> str:
    return SqlHandoff(
        protocol_version="1",
        status="ready",
        summary="Query evidence",
        limitations=(),
        output_artifact_ids=(artifact_id,),
    ).model_dump_json()


def test_role_schema_is_shared_with_parser_and_forbids_model_verified_flag() -> None:
    schema = json.loads(handoff_schema(AgentRole.SQL))
    assert schema == SqlHandoff.model_json_schema()
    value = json.loads(blocked_handoff("sql"))
    value["verified"] = True
    with pytest.raises(ValidationError):
        parse_handoff(AgentRole.SQL, json.dumps(value))
    with pytest.raises(ValidationError):
        parse_handoff(AgentRole.REPORT, blocked_handoff("sql"))


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_reject_nonfinite_even_in_unknown_fields(constant) -> None:
    with pytest.raises(ValueError):
        parse_handoff(AgentRole.SQL, '{"unexpected":' + constant + "}")


@pytest.mark.parametrize("count", [0, 1, 200, 201])
def test_query_always_produces_task_owned_evidence(
    count, tool_registry, tool_context, artifact_repository, db_connection
) -> None:
    result = tool_registry.execute(
        ToolCall(
            "query",
            "query_data",
            {
                "sql": f"SELECT * FROM range({count})",
                "bindings": {},
            },
        ),
        tool_context,
    )
    assert result.success
    artifact = artifact_repository.get(result.output["artifact_id"])
    assert artifact.task_id == tool_context.task_id
    assert artifact.producer_execution_id
    assert result.output["row_count"] == count
    assert db_connection.execute(
        "SELECT content_hash FROM artifact_execution_provenance WHERE artifact_id=?",
        (artifact.id,),
    ).fetchone()[0]


def test_query_result_row_and_byte_limits(workspace, artifact_repository, project) -> None:
    from data_analysis_agent.execution import DatasetQueryError, DuckDBExecutor

    executor = DuckDBExecutor(workspace, artifact_repository, max_rows=2)
    with pytest.raises(DatasetQueryError) as exc:
        executor.query(project.id, "SELECT * FROM range(3)", {})
    assert exc.value.category == "result_limit"
    executor = DuckDBExecutor(workspace, artifact_repository, max_result_bytes=1)
    with pytest.raises(DatasetQueryError) as exc:
        executor.query(project.id, "SELECT 1", {})
    assert exc.value.category == "result_limit"
    assert artifact_repository.list_for_project(project.id) == ()
    executor = DuckDBExecutor(workspace, artifact_repository, max_result_bytes=1000)
    with pytest.raises(DatasetQueryError) as exc:
        executor.query(project.id, "SELECT repeat('x', 100000)", {})
    assert exc.value.category == "result_limit"


def test_real_query_handoff_has_server_receipt_and_provenance(
    settings, db_connection, task, tool_registry
) -> None:
    repository = TeamRepository(db_connection)
    run = repository.create_run(task.id, AgentRole.SQL, "Count rows")

    class Provider:
        requests = []

        def complete(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                return ModelResponse(
                    "",
                    (
                        ToolCall(
                            "query",
                            "query_data",
                            {
                                "sql": "SELECT 42 AS answer",
                                "bindings": {},
                            },
                        ),
                    ),
                )
            result = json.loads(request.messages[-1].content)
            return ModelResponse(sql_result(result["output"]["artifact_id"]), ())

    provider = Provider()
    completed = TeammateRunner(
        settings=settings, provider=provider, tools=tool_registry, repository=repository
    ).run(run.id)
    assert completed.status.value == "completed"
    receipt = json.loads(completed.handoff_validation_json or "{}")
    assert receipt["status"] == "validated" and receipt["level"] == "reference"
    assert receipt["artifact_hashes"]
    assert '"output_artifact_ids"' in provider.requests[0].system
    assert "Work order" in provider.requests[0].system


def test_fabricated_artifact_is_not_repaired_into_success(
    settings, db_connection, task, tool_registry
) -> None:
    repository = TeamRepository(db_connection)
    run = repository.create_run(task.id, AgentRole.SQL, "Read data")
    provider = ScriptedProvider([ModelResponse(sql_result("invented-artifact"), ())])
    result = TeammateRunner(
        settings=settings, provider=provider, tools=tool_registry, repository=repository
    ).run(run.id)
    assert result.error_category == "invalid_handoff_evidence"
    assert len(provider.requests) == 1
    assert result.handoff_validation_json is None


def test_format_repair_exposes_no_tools_and_cannot_execute(
    settings, db_connection, task, tool_registry
) -> None:
    repository = TeamRepository(db_connection)
    run = repository.create_run(task.id, AgentRole.SQL, "Return evidence")
    provider = ScriptedProvider(
        [
            ModelResponse("invalid", ()),
            ModelResponse(
                "",
                (
                    ToolCall(
                        "forbidden",
                        "query_data",
                        {
                            "sql": "SELECT 1",
                            "bindings": {},
                        },
                    ),
                ),
            ),
        ]
    )
    result = TeammateRunner(
        settings=settings, provider=provider, tools=tool_registry, repository=repository
    ).run(run.id)
    assert result.error_category == "invalid_handoff"
    assert provider.requests[1].tools == ()
    assert db_connection.execute("SELECT COUNT(*) FROM tool_executions").fetchone()[0] == 0
    assert repository.get_run(run.id).handoff_repair_attempted


def test_repair_budget_survives_worker_reentry(
    settings, db_connection, task, tool_registry
) -> None:
    repository = TeamRepository(db_connection)
    run = repository.create_run(task.id, AgentRole.SQL, "Repair once")
    repository.transition_run(run.id, AgentRunStatus.WORKING)
    assert repository.claim_handoff_repair(run.id)
    assert not repository.claim_handoff_repair(run.id)
    provider = ScriptedProvider([ModelResponse("invalid", ())])
    result = TeammateRunner(
        settings=settings, provider=provider, tools=tool_registry, repository=repository
    ).run(run.id)
    assert result.error_category == "invalid_handoff"
    assert provider.requests[0].tools == ()


def test_input_snapshot_reuse_tamper_and_other_task_isolation(
    db_connection,
    workspace,
    task,
    dataset_artifact,
    tool_registry,
    tool_context,
    artifact_repository,
    task_repository,
) -> None:
    validator = HandoffValidationService(db_connection, workspace)
    result = tool_registry.execute(
        ToolCall(
            "q",
            "query_data",
            {
                "sql": "SELECT 1 AS answer",
                "bindings": {},
            },
        ),
        tool_context,
    )
    evidence_id = result.output["artifact_id"]
    second_task = task_repository.create(task.project_id, "Another analysis")
    default = validator.prepare_work_order(second_task.id, AgentRole.SQL)
    with pytest.raises(HandoffEvidenceError):
        validator.validate(
            agent_id="other",
            task_id=second_task.id,
            order=default,
            handoff=parse_handoff(AgentRole.SQL, sql_result(evidence_id)),
        )
    explicit = validator.prepare_work_order(
        second_task.id, AgentRole.SQL, WorkOrder(input_artifact_ids=(evidence_id,))
    )
    receipt = validator.validate(
        agent_id="other",
        task_id=second_task.id,
        order=explicit,
        handoff=parse_handoff(AgentRole.SQL, sql_result(evidence_id)),
    )
    assert evidence_id in receipt["artifact_hashes"]
    artifact_repository.get(evidence_id).path.write_bytes(b"changed")
    with pytest.raises(HandoffEvidenceError, match="artifact_changed"):
        validator.validate(
            agent_id="other",
            task_id=second_task.id,
            order=explicit,
            handoff=parse_handoff(AgentRole.SQL, sql_result(evidence_id)),
        )
    assert dataset_artifact.id in default["input_artifact_ids"]


def test_knowledge_reference_requires_stored_version_and_definition(
    db_connection, workspace, task
) -> None:
    knowledge = BusinessKnowledgeRepository(db_connection)
    entry = knowledge.create(
        task.project_id, kind="metric", name="Profit", definition="Revenue - cost"
    )
    validator = HandoffValidationService(db_connection, workspace)
    order = validator.prepare_work_order(task.id, AgentRole.KNOWLEDGE)
    handoff = KnowledgeHandoff(
        protocol_version="1",
        status="ready",
        summary="Profit definition",
        limitations=(),
        definitions=({"knowledge_id": entry.id, "version": 1, "definition": entry.definition},),
        unresolved_questions=(),
    )
    validator.validate(agent_id="knowledge", task_id=task.id, order=order, handoff=handoff)
    with pytest.raises(HandoffEvidenceError, match="knowledge_reference_invalid"):
        validator.validate(
            agent_id="knowledge",
            task_id=task.id,
            order=order,
            handoff=handoff.model_copy(
                update={"definitions": (handoff.definitions[0].model_copy(update={"version": 2}),)}
            ),
        )


def test_work_order_rejects_foreign_inputs_wrong_role_and_stale_plan(
    db_connection, workspace, task, project_repository, artifact_repository, task_repository
) -> None:
    from data_analysis_agent.domain import PlanStepDraft

    other = project_repository.create("Other project")
    workspace.create_project(other.id)
    path = workspace.resolve_inside(other.id, "derived/data.csv")
    path.write_text("value\n1\n")
    artifact = artifact_repository.register(
        other.id, None, ArtifactType.DERIVED_DATASET, path, "Other data", "", None, ()
    )
    validator = HandoffValidationService(db_connection, workspace)
    with pytest.raises(HandoffEvidenceError):
        validator.prepare_work_order(
            task.id, AgentRole.SQL, WorkOrder(input_artifact_ids=(artifact.id,))
        )
    with pytest.raises(HandoffEvidenceError):
        validator.prepare_work_order(
            task.id, AgentRole.SQL, WorkOrder(expected_output_kind="report")
        )
    old = task_repository.revise_plan(task.id, (PlanStepDraft("Old step"),), "Initial")
    task_repository.revise_plan(task.id, (PlanStepDraft("New step"),), "Revised")
    with pytest.raises(HandoffEvidenceError):
        validator.prepare_work_order(
            task.id, AgentRole.SQL, WorkOrder(plan_step_id=old.steps[0].id)
        )


@pytest.mark.parametrize("mismatch", [False, True])
def test_real_report_handoff_checks_registered_evidence_links(
    mismatch, settings, db_connection, task, dataset_artifact, tool_registry
) -> None:
    repository = TeamRepository(db_connection)
    run = repository.create_run(task.id, AgentRole.REPORT, "Publish an evidence-linked report")

    class Provider:
        requests = []

        def complete(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                return ModelResponse(
                    "",
                    (
                        ToolCall(
                            "report",
                            "write_report",
                            {
                                "markdown": f"# Report\n\n## Key Findings\n\nartifact://{dataset_artifact.id}",
                            },
                        ),
                    ),
                )
            result = json.loads(request.messages[-1].content)
            return ModelResponse(
                ReportHandoff(
                    protocol_version="1",
                    status="ready",
                    summary="Registered report",
                    limitations=(),
                    report_artifact_id=result["output"]["artifact_id"],
                    chart_artifact_ids=(),
                    evidence_artifact_ids=() if mismatch else (dataset_artifact.id,),
                ).model_dump_json(),
                (),
            )

    completed = TeammateRunner(
        settings=settings, provider=Provider(), tools=tool_registry, repository=repository
    ).run(run.id)
    if mismatch:
        assert completed.error_category == "invalid_handoff_evidence"
    else:
        assert completed.status is AgentRunStatus.COMPLETED
        assert json.loads(completed.handoff_validation_json or "{}")["status"] == "validated"


def test_same_task_other_agent_cannot_claim_new_output_without_input_snapshot(
    db_connection, workspace, task, tool_registry, tool_context
) -> None:
    validator = HandoffValidationService(db_connection, workspace)
    order = validator.prepare_work_order(task.id, AgentRole.SQL)
    repository = TeamRepository(db_connection)
    owner = repository.create_run(task.id, AgentRole.SQL, "Produce evidence")
    result = tool_registry.execute(
        ToolCall(
            "q",
            "query_data",
            {
                "sql": "SELECT 1",
                "bindings": {},
            },
        ),
        ToolContext(project_id=task.project_id, task_id=task.id, agent_id=owner.id),
    )
    assert result.success
    handoff = parse_handoff(AgentRole.SQL, sql_result(result.output["artifact_id"]))
    validator.validate(agent_id=owner.id, task_id=task.id, order=order, handoff=handoff)
    with pytest.raises(HandoffEvidenceError, match="artifact_provenance_invalid"):
        validator.validate(agent_id="other", task_id=task.id, order=order, handoff=handoff)


def test_migration_preserves_legacy_handoff_without_inventing_validation(
    db_connection, task
) -> None:
    from data_analysis_agent.db import migrate
    from data_analysis_agent.tools import _safe_run

    repository = TeamRepository(db_connection)
    run = repository.create_run(task.id, AgentRole.SQL, "Legacy run")
    repository.transition_run(run.id, AgentRunStatus.WORKING)
    repository.complete_run(run.id, '{"summary":"legacy"}')
    db_connection.execute("DROP TABLE artifact_execution_provenance")
    for column in ("work_order_json", "handoff_validation_json", "handoff_repair_attempted"):
        db_connection.execute(f"ALTER TABLE agent_runs DROP COLUMN {column}")
    db_connection.execute("DELETE FROM schema_migrations WHERE version=20")
    db_connection.commit()
    migrate(db_connection)
    migrate(db_connection)
    recovered = repository.get_run(run.id)
    assert recovered.result_json == '{"summary":"legacy"}'
    assert recovered.work_order_json is None
    assert _safe_run(recovered)["handoff_validation"] == {"status": "legacy_unvalidated"}


def test_work_order_and_receipt_survive_worker_recreation(
    settings, db_connection, task, dataset_artifact
) -> None:
    repository = TeamRepository(db_connection)
    order = HandoffValidationService(db_connection, WorkspaceService(settings)).prepare_work_order(
        task.id, AgentRole.REPORT, WorkOrder(input_artifact_ids=(dataset_artifact.id,))
    )
    run = repository.create_run(task.id, AgentRole.REPORT, "Read assigned inputs", work_order=order)
    fresh = TeamRepository(db_connection).get_run(run.id)
    assert json.loads(fresh.work_order_json or "{}") == order
    assert dataset_artifact.id in order["input_hashes"]


def test_contract_migration_rolls_back_all_ddl_on_failure(db_connection, monkeypatch) -> None:
    from data_analysis_agent import db

    db_connection.execute("DROP TABLE artifact_execution_provenance")
    for column in ("work_order_json", "handoff_validation_json", "handoff_repair_attempted"):
        db_connection.execute(f"ALTER TABLE agent_runs DROP COLUMN {column}")
    db_connection.execute("DELETE FROM schema_migrations WHERE version=20")
    db_connection.commit()
    original = db.MIGRATION_20_SQL
    monkeypatch.setattr(
        db, "MIGRATION_20_SQL", original + "; INSERT INTO missing_table VALUES (1);"
    )
    with pytest.raises(sqlite3.OperationalError):
        db.migrate(db_connection)
    assert "work_order_json" not in {
        row[1] for row in db_connection.execute("PRAGMA table_info(agent_runs)")
    }
    assert (
        db_connection.execute("SELECT 1 FROM schema_migrations WHERE version=20").fetchone() is None
    )
    monkeypatch.setattr(db, "MIGRATION_20_SQL", original)
    db.migrate(db_connection)


def test_knowledge_agent_returns_a_real_versioned_retrieval_handoff(settings) -> None:
    from data_analysis_agent.api import build_services

    services = build_services(settings, with_team_manager=False)
    try:
        project = services.projects.create("Knowledge integration")
        services.workspace.create_project(project.id)
        task = services.tasks.create(project.id, "Resolve profit definition")
        entry = services.knowledge.create(
            project.id, kind="metric", name="profit", definition="Revenue minus cost"
        )

        class Provider:
            requests = []

            def complete(self, request):
                self.requests.append(request)
                if len(self.requests) == 1:
                    return ModelResponse(
                        "",
                        (
                            ToolCall(
                                "knowledge",
                                "search_business_knowledge",
                                {
                                    "query": "profit",
                                },
                            ),
                        ),
                    )
                observed = json.loads(request.messages[-1].content)["output"]["entries"][0]
                return ModelResponse(
                    KnowledgeHandoff(
                        protocol_version="1",
                        status="ready",
                        summary="Resolved profit definition",
                        limitations=(),
                        unresolved_questions=(),
                        definitions=(
                            {
                                "knowledge_id": observed["id"],
                                "version": observed["version"],
                                "definition": observed["definition"],
                            },
                        ),
                    ).model_dump_json(),
                    (),
                )

        run = services.team_repository.create_run(task.id, AgentRole.KNOWLEDGE, "Find profit")
        completed = TeammateRunner(
            settings=settings,
            provider=Provider(),
            tools=services.tools,
            repository=services.team_repository,
        ).run(run.id)
        assert completed.status is AgentRunStatus.COMPLETED
        assert json.loads(completed.handoff_validation_json or "{}")["knowledge_refs"] == [
            {"knowledge_id": entry.id, "version": 1}
        ]
    finally:
        services.connection.close()
