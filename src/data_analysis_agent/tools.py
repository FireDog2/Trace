from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from data_analysis_agent.datasets import DatasetService
from data_analysis_agent.domain import ArtifactType, PlanStepDraft, TaskStatus
from data_analysis_agent.execution import DuckDBExecutor, PythonExecutor
from data_analysis_agent.permissions import (
    ApprovalRepository,
    PermissionLevel,
    PermissionPolicy,
    ToolAction,
)
from data_analysis_agent.postgres import SqlSafetyGateway
from data_analysis_agent.reporting import ReportService
from data_analysis_agent.repositories import (
    ArtifactRepository,
    RecordNotFoundError,
    TaskRepository,
)
from data_analysis_agent.workspace import WorkspaceService


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolSchema:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolContext:
    project_id: str
    task_id: str
    agent_id: str | None = None
    run_id: str | None = None


@dataclass(frozen=True, slots=True)
class ToolResult:
    call_id: str
    success: bool
    output: dict[str, Any]
    error_category: str | None = None
    message: str = ""
    retryable: bool = False
    diagnostics: dict[str, Any] | None = None
    pauses_task: bool = False


@dataclass(frozen=True, slots=True)
class HandlerResult:
    output: dict[str, Any]
    artifact_ids: tuple[str, ...] = ()
    pauses_task: bool = False


class ToolHandlerError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        category: str = "tool_error",
        retryable: bool = True,
        diagnostics: dict[str, Any] | None = None,
        pauses_task: bool = False,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.retryable = retryable
        self.diagnostics = diagnostics
        self.pauses_task = pauses_task


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InspectWorkspaceInput(ToolInput):
    pass


class InspectDatasetInput(ToolInput):
    artifact_id: str
    sample_rows: int = Field(default=20, ge=0, le=100)


class QueryDataInput(ToolInput):
    sql: str = Field(min_length=1)
    bindings: dict[str, str]


class QueryDatabaseInput(ToolInput):
    data_source_id: str
    sql: str = Field(min_length=1, max_length=50_000)


class RunPythonInput(ToolInput):
    code: str = Field(min_length=1)
    declared_outputs: tuple[str, ...] = ()


class SaveArtifactInput(ToolInput):
    path: str
    artifact_type: ArtifactType
    title: str = Field(min_length=1)
    description: str = ""
    input_artifact_ids: tuple[str, ...] = ()


class WriteReportInput(ToolInput):
    markdown: str = Field(min_length=1)


class UpdatePlanInput(ToolInput):
    steps: tuple[str, ...] = Field(min_length=1)
    reason: str = Field(min_length=1)


class RequestUserInputInput(ToolInput):
    question: str = Field(min_length=1)


Handler = Callable[[ToolInput, ToolContext], HandlerResult]


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    input_model: type[ToolInput]
    handler: Handler


class ToolRegistry:
    def __init__(
        self,
        definitions: tuple[ToolDefinition, ...],
        permission_policy: PermissionPolicy,
        approvals: ApprovalRepository,
        tasks: TaskRepository,
        artifacts: ArtifactRepository,
    ) -> None:
        self._definitions = {definition.name: definition for definition in definitions}
        self._permission_policy = permission_policy
        self._approvals = approvals
        self._tasks = tasks
        self._artifacts = artifacts

    def schemas(self) -> tuple[ToolSchema, ...]:
        return tuple(
            ToolSchema(
                name=definition.name,
                description=definition.description,
                input_schema=definition.input_model.model_json_schema(),
            )
            for definition in self._definitions.values()
        )

    def restrict(self, allowed_names: frozenset[str] | set[str]) -> "ToolRegistry":
        """Return an independently scoped view sharing the same backing services."""
        allowed = frozenset(allowed_names)
        return ToolRegistry(
            tuple(definition for name, definition in self._definitions.items() if name in allowed),
            self._permission_policy,
            self._approvals,
            self._tasks,
            self._artifacts,
        )

    def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        definition = self._definitions.get(call.name)
        if definition is None:
            return ToolResult(
                call_id=call.id,
                success=False,
                output={},
                error_category="unknown_tool",
                message=f"Unknown tool: {call.name}",
                retryable=False,
            )
        try:
            arguments = definition.input_model.model_validate(call.arguments)
        except ValidationError as exc:
            return ToolResult(
                call_id=call.id,
                success=False,
                output={},
                error_category="validation_error",
                message="Tool arguments did not match the schema",
                retryable=True,
                diagnostics={
                    "error_count": len(exc.errors()),
                    "types": ["validation_error"],
                },
            )

        try:
            task = self._tasks.get(context.task_id)
        except RecordNotFoundError:
            task = None
        if task is None or task.project_id != context.project_id:
            return ToolResult(
                call_id=call.id,
                success=False,
                output={},
                error_category="project_scope_denied",
                message="Task does not belong to the requested project",
                retryable=False,
            )

        normalized_arguments = arguments.model_dump(mode="json")
        decision = self._permission_policy.classify(
            ToolAction(call.name, normalized_arguments)
        )
        if decision.level is PermissionLevel.DENY:
            return ToolResult(
                call_id=call.id,
                success=False,
                output={},
                error_category="permission_denied",
                message=decision.reason,
                retryable=False,
            )
        if decision.level is PermissionLevel.REQUIRE_APPROVAL:
            action = ToolAction(call.name, normalized_arguments)
            if not self._approvals.consume_approved(context.task_id, action):
                approval = self._approvals.request(
                    context.task_id,
                    action,
                    decision.reason,
                )
                self._pause_task(context.task_id)
                self._tasks.append_event(
                    context.task_id,
                    "approval.requested",
                    {
                        "approval_id": approval.id,
                        "tool_name": call.name,
                        "reason": decision.reason,
                    },
                )
                return ToolResult(
                    call_id=call.id,
                    success=False,
                    output={"approval_id": approval.id},
                    error_category="approval_required",
                    message=decision.reason,
                    retryable=False,
                    pauses_task=True,
                )

        execution_id = self._tasks.start_tool_execution(
            context.task_id,
            call.name,
            _audit_arguments(call.name, normalized_arguments),
        )
        self._tasks.append_event(
            context.task_id,
            "tool.started",
            {"execution_id": execution_id, "tool_name": call.name},
        )
        try:
            handled = definition.handler(arguments, context)
            self._artifacts.set_producer(handled.artifact_ids, execution_id)
            self._tasks.finish_tool_execution(execution_id, "succeeded")
            self._tasks.append_event(
                context.task_id,
                "tool.completed",
                {
                    "execution_id": execution_id,
                    "tool_name": call.name,
                    "artifact_ids": list(handled.artifact_ids),
                },
            )
            return ToolResult(
                call_id=call.id,
                success=True,
                output=handled.output,
                pauses_task=handled.pauses_task,
            )
        except ToolHandlerError as exc:
            category = _safe_tool_category(exc.category)
            message = _public_tool_message(category)
            self._tasks.finish_tool_execution(
                execution_id,
                "failed",
                category,
                message,
            )
            self._tasks.append_event(
                context.task_id,
                "tool.failed",
                {
                    "execution_id": execution_id,
                    "tool_name": call.name,
                    "error_category": category,
                },
            )
            return ToolResult(
                call_id=call.id,
                success=False,
                output={},
                error_category=category,
                message=message,
                retryable=exc.retryable,
                diagnostics=_safe_tool_diagnostics(category),
                pauses_task=exc.pauses_task,
            )
        except Exception:
            self._tasks.finish_tool_execution(
                execution_id,
                "failed",
                "internal_tool_error",
                "The tool could not be completed safely",
            )
            self._tasks.append_event(
                context.task_id,
                "tool.failed",
                {
                    "execution_id": execution_id,
                    "tool_name": call.name,
                    "error_category": "internal_tool_error",
                },
            )
            return ToolResult(
                call_id=call.id,
                success=False,
                output={},
                error_category="internal_tool_error",
                message="The tool could not be completed safely",
                retryable=True,
            )

    def _pause_task(self, task_id: str) -> None:
        task = self._tasks.get(task_id)
        if task.status is not TaskStatus.NEEDS_INPUT:
            self._tasks.transition(task_id, TaskStatus.NEEDS_INPUT)


def build_tool_registry(
    *,
    datasets: DatasetService,
    duckdb_executor: DuckDBExecutor,
    python_executor: PythonExecutor,
    workspace: WorkspaceService,
    artifacts: ArtifactRepository,
    reports: ReportService,
    tasks: TaskRepository,
    permission_policy: PermissionPolicy,
    approvals: ApprovalRepository,
    sql_gateway: SqlSafetyGateway | None = None,
) -> ToolRegistry:
    def inspect_workspace(
        arguments: ToolInput,
        context: ToolContext,
    ) -> HandlerResult:
        del arguments
        project_artifacts = artifacts.list_for_project(context.project_id)
        return HandlerResult(
            {
                "artifacts": [
                    {
                        "id": artifact.id,
                        "type": artifact.artifact_type.value,
                        "title": artifact.title,
                        "task_id": artifact.task_id,
                    }
                    for artifact in project_artifacts
                ],
                "tasks": [
                    {
                        "id": task.id,
                        "objective": task.objective,
                        "status": task.status.value,
                    }
                    for task in tasks.list_for_project(context.project_id)
                ],
            }
        )

    def inspect_dataset(
        arguments: ToolInput,
        context: ToolContext,
    ) -> HandlerResult:
        del context
        assert isinstance(arguments, InspectDatasetInput)
        inspection = datasets.inspect(arguments.artifact_id, arguments.sample_rows)
        return HandlerResult(asdict(inspection))

    def query_data(arguments: ToolInput, context: ToolContext) -> HandlerResult:
        assert isinstance(arguments, QueryDataInput)
        _ensure_running(tasks, context.task_id)
        result = duckdb_executor.query(
            context.project_id,
            arguments.sql,
            arguments.bindings,
        )
        artifact_ids = (result.artifact.id,) if result.artifact else ()
        return HandlerResult(
            {
                "columns": list(result.columns),
                "preview": list(result.preview),
                "row_count": result.row_count,
                "artifact_id": result.artifact.id if result.artifact else None,
            },
            artifact_ids,
        )

    def query_database(arguments: ToolInput, context: ToolContext) -> HandlerResult:
        assert isinstance(arguments, QueryDatabaseInput)
        if sql_gateway is None:
            raise ToolHandlerError(
                "PostgreSQL analysis is not configured",
                category="database_unavailable",
                retryable=False,
            )
        _ensure_running(tasks, context.task_id)
        outcome = sql_gateway.execute(
            context.task_id,
            context.project_id,
            arguments.data_source_id,
            arguments.sql,
        )
        if outcome.status == "succeeded":
            return HandlerResult(
                {
                    "status": outcome.status,
                    "preview": list(outcome.preview),
                    "row_count": outcome.row_count,
                    "risk": outcome.risk.level if outcome.risk else None,
                    "query_hash": outcome.query_hash,
                    "artifact_id": outcome.artifact_id,
                },
                (outcome.artifact_id,) if outcome.artifact_id else (),
            )
        if outcome.status == "approval_required":
            raise ToolHandlerError(
                outcome.message or "PostgreSQL query requires approval",
                category="approval_required",
                retryable=False,
                pauses_task=True,
            )
        if outcome.status == "denied":
            raise ToolHandlerError(
                outcome.message or "PostgreSQL query was denied",
                category=outcome.error_category or "database_denied",
                retryable=False,
            )
        raise ToolHandlerError(
            outcome.message or "PostgreSQL query could not be completed",
            category=outcome.error_category or "database_error",
            retryable=outcome.retryable,
        )

    def run_python(arguments: ToolInput, context: ToolContext) -> HandlerResult:
        assert isinstance(arguments, RunPythonInput)
        _ensure_running(tasks, context.task_id)
        result = python_executor.run(
            context.task_id,
            arguments.code,
            arguments.declared_outputs,
        )
        artifact_ids = (
            result.code_artifact.id,
            *(artifact.id for artifact in result.output_artifacts),
        )
        if result.status.value != "succeeded":
            raise ToolHandlerError(
                result.error_message,
                category=result.status.value,
                retryable=result.status.value in {"failed", "timed_out"},
                diagnostics={
                    "stderr": result.stderr,
                    "code_artifact_id": result.code_artifact.id,
                },
            )
        return HandlerResult(
            {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "duration_seconds": result.duration_seconds,
                "artifact_ids": list(artifact_ids),
            },
            artifact_ids,
        )

    def save_artifact(arguments: ToolInput, context: ToolContext) -> HandlerResult:
        assert isinstance(arguments, SaveArtifactInput)
        _ensure_running(tasks, context.task_id)
        path = workspace.resolve_inside(context.project_id, Path(arguments.path))
        artifact = artifacts.register(
            project_id=context.project_id,
            task_id=context.task_id,
            artifact_type=arguments.artifact_type,
            path=path,
            title=arguments.title,
            description=arguments.description,
            producer_execution_id=None,
            input_artifact_ids=arguments.input_artifact_ids,
        )
        return HandlerResult({"artifact_id": artifact.id}, (artifact.id,))

    def write_report(arguments: ToolInput, context: ToolContext) -> HandlerResult:
        assert isinstance(arguments, WriteReportInput)
        _ensure_synthesizing(tasks, context.task_id)
        report = reports.publish(context.task_id, arguments.markdown)
        return HandlerResult({"artifact_id": report.id}, (report.id,))

    def update_plan(arguments: ToolInput, context: ToolContext) -> HandlerResult:
        assert isinstance(arguments, UpdatePlanInput)
        task = tasks.get(context.task_id)
        if task.status is TaskStatus.UNDERSTANDING:
            tasks.transition(task.id, TaskStatus.PLANNING)
        revision = tasks.revise_plan(
            context.task_id,
            tuple(PlanStepDraft(step) for step in arguments.steps),
            arguments.reason,
        )
        tasks.append_event(
            context.task_id,
            "plan.updated",
            {"revision_id": revision.id, "revision": revision.revision},
        )
        return HandlerResult(
            {
                "revision_id": revision.id,
                "revision": revision.revision,
                "steps": [step.title for step in revision.steps],
            }
        )

    def request_user_input(
        arguments: ToolInput,
        context: ToolContext,
    ) -> HandlerResult:
        assert isinstance(arguments, RequestUserInputInput)
        message = tasks.add_message(context.task_id, "assistant", arguments.question)
        task = tasks.get(context.task_id)
        if task.status is not TaskStatus.NEEDS_INPUT:
            tasks.transition(task.id, TaskStatus.NEEDS_INPUT)
        tasks.append_event(
            context.task_id,
            "message.created",
            {"message_id": message.id, "role": message.role},
        )
        return HandlerResult(
            {"question": arguments.question},
            pauses_task=True,
        )

    definitions = (
        ToolDefinition(
            "inspect_workspace",
            "List datasets, tasks, and artifacts available in the current project.",
            InspectWorkspaceInput,
            inspect_workspace,
        ),
        ToolDefinition(
            "inspect_dataset",
            "Inspect a dataset schema, sample, row count, and basic quality metrics.",
            InspectDatasetInput,
            inspect_dataset,
        ),
        ToolDefinition(
            "query_data",
            "Run one read-only DuckDB SQL query against registered dataset bindings.",
            QueryDataInput,
            query_data,
        ),
        ToolDefinition(
            "query_database",
            "Run a governed, read-only PostgreSQL query against a configured data source.",
            QueryDatabaseInput,
            query_database,
        ),
        ToolDefinition(
            "run_python",
            "Run contained Python analysis and register declared output files.",
            RunPythonInput,
            run_python,
        ),
        ToolDefinition(
            "save_artifact",
            "Register an existing project-local analysis output as a durable artifact.",
            SaveArtifactInput,
            save_artifact,
        ),
        ToolDefinition(
            "write_report",
            "Publish an evidence-linked Markdown report for the current task.",
            WriteReportInput,
            write_report,
        ),
        ToolDefinition(
            "update_plan",
            "Create a new persisted revision of the analysis plan.",
            UpdatePlanInput,
            update_plan,
        ),
        ToolDefinition(
            "request_user_input",
            "Pause analysis and ask one focused business or data question.",
            RequestUserInputInput,
            request_user_input,
        ),
    )
    return ToolRegistry(definitions, permission_policy, approvals, tasks, artifacts)


_TOOL_PUBLIC_MESSAGES = {
    "approval_required": "Approval is required",
    "credential_error": "Data source credentials are unavailable",
    "policy_denied": "Database query was denied",
    "resource_not_found": "Database query was denied",
    "project_scope_denied": "Database query was denied",
    "unsupported_database": "Database query was denied",
    "unsafe_statement": "Database query was denied",
    "read_only_required": "Database query was denied",
    "parse_error": "Database query was denied",
    "query_timeout": "Database query could not be completed",
    "connection_error": "Database query could not be completed",
}

_TOOL_CATEGORIES = frozenset(
    {
        "approval_required",
        "approval_rejected",
        "credential_error",
        "policy_denied",
        "resource_not_found",
        "project_scope_denied",
        "unsupported_database",
        "unsafe_statement",
        "read_only_required",
        "parse_error",
        "exactly_one_statement",
        "unresolved_table",
        "unresolved_column",
        "ambiguous_column",
        "unresolved_star",
        "qualified_table_not_allowed",
        "unsafe_function",
        "unsafe_cast",
        "extreme_query_risk",
        "query_budget_exhausted",
        "query_timeout",
        "connection_error",
        "explain_error",
        "query_error",
        "result_limit",
        "result_serialization_error",
        "artifact_write_error",
        "artifact_registration_error",
        "policy_changed",
        "permission_denied",
        "validation_error",
        "internal_tool_error",
        "failed",
        "timed_out",
    }
)

_DATABASE_REASON_CODES = frozenset(
    {
        "credential_error",
        "policy_denied",
        "resource_not_found",
        "project_scope_denied",
        "unsupported_database",
        "unsafe_statement",
        "read_only_required",
        "parse_error",
        "query_timeout",
        "connection_error",
    }
)


def _public_tool_message(category: str) -> str:
    return _TOOL_PUBLIC_MESSAGES.get(category, "Tool failed")


def _safe_tool_category(category: str) -> str:
    return category if category in _TOOL_CATEGORIES else "internal_tool_error"


def _safe_tool_diagnostics(category: str) -> dict[str, str] | None:
    if category in _DATABASE_REASON_CODES:
        return {"reason_code": category}
    return None


def _audit_arguments(tool_name: str, arguments: dict[str, Any]) -> dict[str, object]:
    if tool_name != "query_database":
        return arguments
    return {
        "data_source_id_hash": _sha256(str(arguments["data_source_id"])),
        "sql_hash": _sha256(str(arguments["sql"])),
    }


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _ensure_running(tasks: TaskRepository, task_id: str) -> None:
    task = tasks.get(task_id)
    if task.status is TaskStatus.UNDERSTANDING:
        task = tasks.transition(task.id, TaskStatus.PLANNING)
    if task.status is TaskStatus.PLANNING:
        tasks.transition(task.id, TaskStatus.RUNNING)


def _ensure_synthesizing(tasks: TaskRepository, task_id: str) -> None:
    _ensure_running(tasks, task_id)
    task = tasks.get(task_id)
    if task.status is TaskStatus.RUNNING:
        tasks.transition(task.id, TaskStatus.SYNTHESIZING)
