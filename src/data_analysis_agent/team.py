from __future__ import annotations

import sqlite3
import json
from dataclasses import dataclass, replace
from enum import StrEnum
from hashlib import sha256
from threading import Event
from time import monotonic
from uuid import uuid4

from data_analysis_agent.db import (
    sqlite_connection_guarded,
    sqlite_connection_lock,
    utc_now,
)
from data_analysis_agent.model_config import SecretProtector
from data_analysis_agent.providers import ModelMessage, ModelProvider, ModelRequest, ProviderError
from data_analysis_agent.repositories import ProtectedMessageError
from data_analysis_agent.settings import Settings
from data_analysis_agent.tools import ToolCall, ToolContext, ToolRegistry, ToolResult


class AgentRole(StrEnum):
    KNOWLEDGE = "knowledge"
    SQL = "sql"
    REPORT = "report"


class AgentRunStatus(StrEnum):
    CREATED = "created"
    WORKING = "working"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class InvalidAgentRunTransition(RuntimeError):
    """Raised when a teammate run state change violates its lifecycle."""


@dataclass(frozen=True, slots=True)
class AgentRun:
    id: str
    task_id: str
    role: AgentRole
    objective: str
    status: AgentRunStatus
    result_json: str | None
    error_category: str | None
    error_message: str | None
    cancel_requested: bool
    created_at: str
    started_at: str | None
    completed_at: str | None


@dataclass(frozen=True, slots=True)
class AgentMessage:
    id: str
    agent_run_id: str
    role: str
    content: str
    created_at: str


RUN_TRANSITIONS: dict[AgentRunStatus, frozenset[AgentRunStatus]] = {
    AgentRunStatus.CREATED: frozenset({AgentRunStatus.WORKING, AgentRunStatus.CANCELLED}),
    AgentRunStatus.WORKING: frozenset(
        {
            AgentRunStatus.WAITING,
            AgentRunStatus.COMPLETED,
            AgentRunStatus.FAILED,
            AgentRunStatus.CANCELLED,
        }
    ),
    AgentRunStatus.WAITING: frozenset(
        {
            AgentRunStatus.WORKING,
            AgentRunStatus.FAILED,
            AgentRunStatus.CANCELLED,
        }
    ),
    AgentRunStatus.COMPLETED: frozenset(),
    AgentRunStatus.FAILED: frozenset(),
    AgentRunStatus.CANCELLED: frozenset(),
}

_ACTIVE_STATUSES = frozenset(
    {AgentRunStatus.CREATED, AgentRunStatus.WORKING, AgentRunStatus.WAITING}
)
_MESSAGE_ROLES = frozenset({"user", "assistant", "assistant_tool_call", "system", "tool"})
_SAFE_ERROR_MESSAGES = {
    "interrupted": "Teammate interrupted by service restart",
    "agent_timeout": "Teammate deadline reached",
    "agent_turn_limit": "Teammate turn limit reached",
    "invalid_handoff": "Teammate returned an invalid handoff",
    "provider_error": "The model provider failed",
    "tool_error": "A teammate tool failed unexpectedly",
    "message_protection_error": "Protected teammate message storage is unavailable",
}

ROLE_TOOLS: dict[AgentRole, frozenset[str]] = {
    AgentRole.KNOWLEDGE: frozenset({"inspect_workspace", "inspect_dataset"}),
    AgentRole.SQL: frozenset({"inspect_workspace", "inspect_dataset", "query_data", "query_database"}),
    AgentRole.REPORT: frozenset({"inspect_workspace", "run_python", "save_artifact", "write_report"}),
}

ROLE_PROMPTS: dict[AgentRole, str] = {
    AgentRole.KNOWLEDGE: "Resolve metric definitions and return a JSON handoff. Do not execute SQL.",
    AgentRole.SQL: "Produce evidence with governed query tools and return artifact identifiers.",
    AgentRole.REPORT: "Use registered artifacts only and cite every important claim.",
}

_MAX_HANDOFF_BYTES = 64_000


class TeamRepository:
    """Durable, connection-serialized teammate run persistence."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        connection.row_factory = sqlite3.Row
        self._connection = connection
        self._lock = sqlite_connection_lock(connection)

    @sqlite_connection_guarded
    def create_run(self, task_id: str, role: AgentRole, objective: str) -> AgentRun:
        normalized_objective = objective.strip()
        if not normalized_objective:
            raise ValueError("Agent objective must not be empty")
        run = AgentRun(
            id=str(uuid4()), task_id=task_id, role=AgentRole(role), objective=normalized_objective,
            status=AgentRunStatus.CREATED, result_json=None, error_category=None,
            error_message=None, cancel_requested=False, created_at=utc_now(),
            started_at=None, completed_at=None,
        )
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            self._connection.execute(
                "INSERT INTO agent_runs("
                "id, task_id, role, objective, status, result_json, error_category, "
                "error_message, cancel_requested, created_at, started_at, completed_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run.id, run.task_id, run.role, run.objective, run.status, None, None,
                    None, 0, run.created_at, None, None,
                ),
            )
            self._connection.commit()
            return run
        except Exception:
            self._connection.rollback()
            raise

    @sqlite_connection_guarded
    def get_run(self, agent_run_id: str) -> AgentRun:
        try:
            self._connection.execute("BEGIN")
            run = self._read_run(agent_run_id)
            self._connection.commit()
            return run
        except Exception:
            self._connection.rollback()
            raise

    @sqlite_connection_guarded
    def list_runs(self, task_id: str) -> tuple[AgentRun, ...]:
        try:
            self._connection.execute("BEGIN")
            rows = self._connection.execute(
                "SELECT * FROM agent_runs WHERE task_id = ? ORDER BY created_at, rowid", (task_id,)
            ).fetchall()
            self._connection.commit()
            return tuple(_run_from_row(row) for row in rows)
        except Exception:
            self._connection.rollback()
            raise

    @sqlite_connection_guarded
    def active_count(self, task_id: str) -> int:
        try:
            self._connection.execute("BEGIN")
            count = int(self._connection.execute(
                "SELECT COUNT(*) FROM agent_runs WHERE task_id = ? "
                "AND status IN ('created', 'working', 'waiting')", (task_id,)
            ).fetchone()[0])
            self._connection.commit()
            return count
        except Exception:
            self._connection.rollback()
            raise

    @sqlite_connection_guarded
    def project_id_for_run(self, agent_run_id: str) -> str:
        row = self._connection.execute(
            "SELECT tasks.project_id FROM agent_runs JOIN tasks ON tasks.id = agent_runs.task_id WHERE agent_runs.id = ?",
            (agent_run_id,),
        ).fetchone()
        if row is None:
            raise KeyError("Agent run not found")
        return str(row["project_id"])

    @sqlite_connection_guarded
    def transition_run(self, agent_run_id: str, target: AgentRunStatus) -> AgentRun:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            run = self._transition_in_transaction(agent_run_id, AgentRunStatus(target))
            self._connection.commit()
            return run
        except Exception:
            self._connection.rollback()
            raise

    @sqlite_connection_guarded
    def append_message(self, agent_run_id: str, role: str, content: str) -> AgentMessage:
        if role not in _MESSAGE_ROLES:
            raise ValueError("Unsupported agent message role")
        normalized_content = content.strip()
        if not normalized_content:
            raise ValueError("Agent message content must not be empty")
        message = AgentMessage(str(uuid4()), agent_run_id, role, normalized_content, utc_now())
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            self._get_row(agent_run_id)
            self._connection.execute(
                "INSERT INTO agent_messages(id, agent_run_id, role, content, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (message.id, message.agent_run_id, message.role, message.content, message.created_at),
            )
            self._connection.commit()
            return message
        except Exception:
            self._connection.rollback()
            raise

    @sqlite_connection_guarded
    def append_protected_message(
        self, agent_run_id: str, role: str, safe_content: str, protected_content: bytes
    ) -> AgentMessage:
        """Atomically save a redacted protocol message and its encrypted replay payload."""
        if role not in {"assistant_tool_call", "tool"}:
            raise ValueError("Only tool protocol messages may have protected payloads")
        normalized = safe_content.strip()
        if not normalized:
            raise ValueError("Agent message content must not be empty")
        message = AgentMessage(str(uuid4()), agent_run_id, role, normalized, utc_now())
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            self._get_row(agent_run_id)
            self._connection.execute(
                "INSERT INTO agent_messages(id, agent_run_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
                (message.id, message.agent_run_id, message.role, message.content, message.created_at),
            )
            self._connection.execute(
                "INSERT INTO protected_agent_message_payloads(agent_message_id, protected_content, created_at) VALUES (?, ?, ?)",
                (message.id, sqlite3.Binary(protected_content), message.created_at),
            )
            self._connection.commit()
            return message
        except Exception:
            self._connection.rollback()
            raise ProtectedMessageError("Protected teammate message storage is unavailable") from None

    @sqlite_connection_guarded
    def list_messages(self, agent_run_id: str) -> tuple[AgentMessage, ...]:
        try:
            self._connection.execute("BEGIN")
            rows = self._connection.execute(
                "SELECT * FROM agent_messages WHERE agent_run_id = ? ORDER BY created_at, rowid",
                (agent_run_id,),
            ).fetchall()
            self._connection.commit()
            return tuple(
                AgentMessage(row["id"], row["agent_run_id"], row["role"], row["content"], row["created_at"])
                for row in rows
            )
        except Exception:
            self._connection.rollback()
            raise

    @sqlite_connection_guarded
    def request_cancel(self, agent_run_id: str) -> AgentRun:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            current = _run_from_row(self._get_row(agent_run_id))
            if current.status in _ACTIVE_STATUSES:
                self._connection.execute(
                    "UPDATE agent_runs SET cancel_requested = 1 WHERE id = ?", (agent_run_id,)
                )
                current = replace(current, cancel_requested=True)
            self._connection.commit()
            return current
        except Exception:
            self._connection.rollback()
            raise

    @sqlite_connection_guarded
    def cancel_requested(self, agent_run_id: str) -> bool:
        try:
            self._connection.execute("BEGIN")
            requested = self._read_run(agent_run_id).cancel_requested
            self._connection.commit()
            return requested
        except Exception:
            self._connection.rollback()
            raise

    @sqlite_connection_guarded
    def fail_run(self, agent_run_id: str, error_category: str, error_message: str = "") -> AgentRun:
        del error_message
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            message = _SAFE_ERROR_MESSAGES.get(error_category, "Teammate run failed")
            run = self._transition_in_transaction(
                agent_run_id, AgentRunStatus.FAILED, error_category=error_category, error_message=message
            )
            self._connection.commit()
            return run
        except Exception:
            self._connection.rollback()
            raise

    @sqlite_connection_guarded
    def complete_run(self, agent_run_id: str, result_json: str) -> AgentRun:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            run = self._transition_in_transaction(
                agent_run_id, AgentRunStatus.COMPLETED, result_json=result_json
            )
            self._connection.commit()
            return run
        except Exception:
            self._connection.rollback()
            raise

    @sqlite_connection_guarded
    def mark_interrupted_runs(self) -> int:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            completed_at = utc_now()
            cursor = self._connection.execute(
                "UPDATE agent_runs SET status = 'failed', error_category = 'interrupted', "
                "error_message = ?, completed_at = ? WHERE status IN ('working', 'waiting')",
                (_SAFE_ERROR_MESSAGES["interrupted"], completed_at),
            )
            self._connection.commit()
            return cursor.rowcount
        except Exception:
            self._connection.rollback()
            raise

    def _read_run(self, agent_run_id: str) -> AgentRun:
        return _run_from_row(self._get_row(agent_run_id))

    def _get_row(self, agent_run_id: str) -> sqlite3.Row:
        row = self._connection.execute("SELECT * FROM agent_runs WHERE id = ?", (agent_run_id,)).fetchone()
        if row is None:
            raise KeyError("Agent run not found")
        return row

    def _transition_in_transaction(
        self, agent_run_id: str, target: AgentRunStatus, *, result_json: str | None = None,
        error_category: str | None = None, error_message: str | None = None,
    ) -> AgentRun:
        current = _run_from_row(self._get_row(agent_run_id))
        if target not in RUN_TRANSITIONS[current.status]:
            raise InvalidAgentRunTransition(f"{current.status} -> {target}")
        now = utc_now()
        started_at = current.started_at or (now if target is AgentRunStatus.WORKING else None)
        completed_at = now if target in {AgentRunStatus.COMPLETED, AgentRunStatus.FAILED, AgentRunStatus.CANCELLED} else None
        self._connection.execute(
            "UPDATE agent_runs SET status = ?, result_json = ?, error_category = ?, error_message = ?, "
            "started_at = ?, completed_at = ? WHERE id = ?",
            (target, result_json, error_category, error_message, started_at, completed_at, agent_run_id),
        )
        return AgentRun(
            id=current.id, task_id=current.task_id, role=current.role, objective=current.objective,
            status=target, result_json=result_json, error_category=error_category,
            error_message=error_message, cancel_requested=current.cancel_requested,
            created_at=current.created_at, started_at=started_at, completed_at=completed_at,
        )


def _run_from_row(row: sqlite3.Row) -> AgentRun:
    return AgentRun(
        id=row["id"], task_id=row["task_id"], role=AgentRole(row["role"]),
        objective=row["objective"], status=AgentRunStatus(row["status"]),
        result_json=row["result_json"], error_category=row["error_category"],
        error_message=row["error_message"], cancel_requested=bool(row["cancel_requested"]),
        created_at=row["created_at"], started_at=row["started_at"], completed_at=row["completed_at"],
    )


class _ProtectedAgentMessageRepository:
    def __init__(self, connection: sqlite3.Connection, protector: SecretProtector) -> None:
        self._connection = connection
        self._lock = sqlite_connection_lock(connection)
        self._protector = protector

    def protect(self, content: str) -> bytes:
        try:
            return self._protector.protect(content)
        except BaseException:
            raise ProtectedMessageError("Protected teammate message storage is unavailable") from None

    @sqlite_connection_guarded
    def get(self, message_id: str) -> str | None:
        row = self._connection.execute(
            "SELECT protected_content FROM protected_agent_message_payloads WHERE agent_message_id = ?", (message_id,)
        ).fetchone()
        if row is None:
            return None
        try:
            return self._protector.unprotect(bytes(row["protected_content"]))
        except BaseException:
            raise ProtectedMessageError("Protected teammate message recovery is unavailable") from None


class TeammateRunner:
    """Runs one durable, role-limited teammate interaction to a JSON handoff."""

    def __init__(
        self, *, settings: Settings, provider: ModelProvider, tools: ToolRegistry,
        repository: TeamRepository, cancel_event: Event | None = None,
        secret_protector: SecretProtector | None = None,
    ) -> None:
        self._settings = settings
        self._provider = provider
        self._tools = tools
        self._repository = repository
        self._cancel_event = cancel_event or Event()
        self._protected_messages = (
            _ProtectedAgentMessageRepository(repository._connection, secret_protector)
            if secret_protector is not None else None
        )

    def run(self, agent_run_id: str) -> AgentRun:
        run = self._repository.get_run(agent_run_id)
        if run.status in {AgentRunStatus.COMPLETED, AgentRunStatus.FAILED, AgentRunStatus.CANCELLED}:
            return run
        if self._cancelled(run.id):
            return self._repository.transition_run(run.id, AgentRunStatus.CANCELLED)
        run = self._repository.transition_run(run.id, AgentRunStatus.WORKING)
        tools = self._tools.restrict(ROLE_TOOLS[run.role])
        deadline = monotonic() + self._settings.teammate_timeout_seconds
        for _turn in range(self._settings.max_teammate_turns):
            terminal = self._terminal_before_action(run.id, deadline)
            if terminal is not None:
                return terminal
            try:
                response = self._provider.complete(self._request(run, tools))
            except (ProviderError, ProtectedMessageError):
                return self._repository.fail_run(run.id, "provider_error")
            except Exception:
                return self._repository.fail_run(run.id, "provider_error")
            if not response.tool_calls:
                try:
                    return self._repository.complete_run(run.id, self._parse_handoff(response.text))
                except ValueError:
                    return self._repository.fail_run(run.id, "invalid_handoff")
            failure = self._record_and_execute_calls(run, tools, response.tool_calls, deadline)
            if failure is not None:
                return failure
        return self._repository.fail_run(run.id, "agent_turn_limit")

    def _terminal_before_action(self, run_id: str, deadline: float) -> AgentRun | None:
        if self._cancelled(run_id):
            return self._repository.transition_run(run_id, AgentRunStatus.CANCELLED)
        if monotonic() >= deadline:
            return self._repository.fail_run(run_id, "agent_timeout")
        return None

    def _cancelled(self, run_id: str) -> bool:
        return self._cancel_event.is_set() or self._repository.cancel_requested(run_id)

    def _request(self, run: AgentRun, tools: ToolRegistry) -> ModelRequest:
        messages = tuple(
            ModelMessage(message.role, self._provider_content(message.id, message.content))
            for message in self._repository.list_messages(run.id)
        )
        return ModelRequest(ROLE_PROMPTS[run.role], run.objective, messages, tools.schemas())

    def _provider_content(self, message_id: str, safe_content: str) -> str:
        if self._protected_messages is None:
            return safe_content
        return self._protected_messages.get(message_id) or safe_content

    def _record_and_execute_calls(
        self, run: AgentRun, tools: ToolRegistry, calls: tuple[ToolCall, ...], deadline: float
    ) -> AgentRun | None:
        try:
            self._record_tool_calls(run, calls)
        except ProtectedMessageError:
            return self._repository.fail_run(run.id, "message_protection_error")
        context = ToolContext(project_id=self._repository.project_id_for_run(run.id), task_id=run.task_id, agent_id=run.id, run_id=run.id)
        for index, call in enumerate(calls):
            terminal = self._terminal_before_action(run.id, deadline)
            if terminal is not None:
                self._record_cancelled(run.id, calls[index:], "cancelled_due_to_cancel")
                return terminal
            try:
                result = tools.execute(call, context)
            except Exception:
                self._record_result(run.id, call.name, ToolResult(call.id, False, {}, "tool_error", "Tool failed"))
                return self._repository.fail_run(run.id, "tool_error")
            try:
                self._record_result(run.id, call.name, result)
            except ProtectedMessageError:
                return self._repository.fail_run(run.id, "message_protection_error")
            if result.pauses_task:
                self._record_cancelled(run.id, calls[index + 1:], "cancelled_due_to_pause")
                return self._repository.transition_run(run.id, AgentRunStatus.WAITING)
        return None

    def _record_tool_calls(self, run: AgentRun, calls: tuple[ToolCall, ...]) -> None:
        raw = json.dumps({"tool_calls": [{"id": c.id, "name": c.name, "arguments": c.arguments} for c in calls]}, ensure_ascii=False, separators=(",", ":"))
        safe = json.dumps({"tool_calls": [_safe_tool_call(c) for c in calls]}, ensure_ascii=False, separators=(",", ":"))
        if any(call.name == "query_database" for call in calls):
            if self._protected_messages is None:
                raise ProtectedMessageError("Protected teammate message storage is unavailable")
            self._repository.append_protected_message(run.id, "assistant_tool_call", safe, self._protected_messages.protect(raw))
        else:
            self._repository.append_message(run.id, "assistant_tool_call", safe)

    def _record_result(self, run_id: str, tool_name: str, result: ToolResult) -> None:
        raw = _tool_result_json(result)
        safe = _safe_tool_result_json(result, tool_name)
        # Database result previews can contain sensitive result values even if the request did not.
        if tool_name == "query_database":
            if self._protected_messages is None:
                raise ProtectedMessageError("Protected teammate message storage is unavailable")
            self._repository.append_protected_message(run_id, "tool", safe, self._protected_messages.protect(raw))
        else:
            self._repository.append_message(run_id, "tool", safe)

    def _record_cancelled(self, run_id: str, calls: tuple[ToolCall, ...], category: str) -> None:
        for call in calls:
            self._record_result(run_id, call.name, ToolResult(call.id, False, {}, category, "Cancelled before execution"))

    def _parse_handoff(self, text: str) -> str:
        if not text or len(text.encode("utf-8")) > _MAX_HANDOFF_BYTES:
            raise ValueError("invalid handoff")
        try:
            value = json.loads(text)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid handoff") from exc
        if not isinstance(value, dict):
            raise ValueError("invalid handoff")
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _safe_tool_call(call: ToolCall) -> dict[str, object]:
    if call.name != "query_database":
        return {"id": call.id, "name": call.name, "arguments": call.arguments}
    return {"id": call.id, "name": call.name, "arguments": {
        "sql_hash": sha256(str(call.arguments.get("sql", "")).encode("utf-8")).hexdigest(),
        "data_source_id_hash": sha256(str(call.arguments.get("data_source_id", "")).encode("utf-8")).hexdigest(),
    }}


def _tool_result_json(result: ToolResult) -> str:
    return json.dumps({"call_id": result.call_id, "success": result.success, "output": result.output,
                       "error_category": result.error_category, "message": result.message,
                       "retryable": result.retryable, "diagnostics": result.diagnostics}, ensure_ascii=False, separators=(",", ":"))


def _safe_tool_result_json(result: ToolResult, tool_name: str) -> str:
    if tool_name != "query_database":
        return _tool_result_json(result)
    output = result.output if isinstance(result.output, dict) else {}
    return json.dumps({"call_id": result.call_id, "success": result.success,
                       "output": {key: output.get(key) for key in ("status", "query_hash", "row_count", "risk", "artifact_id") if key in output},
                       "error_category": result.error_category, "message": result.message,
                       "retryable": result.retryable, "diagnostics": None}, ensure_ascii=False, separators=(",", ":"))
