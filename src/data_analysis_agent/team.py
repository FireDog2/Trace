from __future__ import annotations

import sqlite3
import json
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from threading import Event, RLock
from time import monotonic
from typing import Callable
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


class TeamCapacityError(RuntimeError):
    """Raised when a parent task already has its active teammate quota."""


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
    turns_used: int
    deadline_at: str | None
    worker_token: str | None
    worker_claimed_at: str | None


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
    "message_persistence_error": "Protected teammate message storage is unavailable",
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
        return self._create_run(task_id, role, objective, max_active=None)

    @sqlite_connection_guarded
    def create_run_if_capacity(
        self, task_id: str, role: AgentRole, objective: str, *, max_active: int = 3
    ) -> AgentRun:
        if not 1 <= max_active <= 3:
            raise ValueError("max_active must be between 1 and 3")
        return self._create_run(task_id, role, objective, max_active=max_active)

    def _create_run(
        self, task_id: str, role: AgentRole, objective: str, *, max_active: int | None
    ) -> AgentRun:
        normalized_objective = objective.strip()
        if not normalized_objective:
            raise ValueError("Agent objective must not be empty")
        run = AgentRun(
            id=str(uuid4()), task_id=task_id, role=AgentRole(role), objective=normalized_objective,
            status=AgentRunStatus.CREATED, result_json=None, error_category=None,
            error_message=None, cancel_requested=False, created_at=utc_now(),
            started_at=None, completed_at=None,
            turns_used=0, deadline_at=None, worker_token=None, worker_claimed_at=None,
        )
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            if max_active is not None:
                active = int(self._connection.execute(
                    "SELECT COUNT(*) FROM agent_runs WHERE task_id = ? "
                    "AND status IN ('created', 'working', 'waiting')", (task_id,)
                ).fetchone()[0])
                if active >= max_active:
                    raise TeamCapacityError("At most three teammates may be active")
            self._connection.execute(
                "INSERT INTO agent_runs("
                "id, task_id, role, objective, status, result_json, error_category, "
                "error_message, cancel_requested, created_at, started_at, completed_at, turns_used, deadline_at, worker_token, worker_claimed_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run.id, run.task_id, run.role, run.objective, run.status, None, None,
                    None, 0, run.created_at, None, None, 0, None, None, None,
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
    def claim_worker(self, agent_run_id: str, token: str) -> AgentRun | None:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            current = self._read_run(agent_run_id)
            if current.worker_token == token:
                self._connection.commit(); return current
            cursor = self._connection.execute(
                "UPDATE agent_runs SET worker_token = ?, worker_claimed_at = ? WHERE id = ? "
                "AND worker_token IS NULL AND status IN ('created', 'waiting', 'working')",
                (token, utc_now(), agent_run_id),
            )
            self._connection.commit()
            return replace(current, worker_token=token, worker_claimed_at=utc_now()) if cursor.rowcount == 1 else None
        except Exception:
            self._connection.rollback(); raise

    @sqlite_connection_guarded
    def release_worker(self, agent_run_id: str, token: str) -> None:
        self._connection.execute("UPDATE agent_runs SET worker_token = NULL, worker_claimed_at = NULL WHERE id = ? AND worker_token = ?", (agent_run_id, token))
        self._connection.commit()

    @sqlite_connection_guarded
    def reserve_turn(self, agent_run_id: str, token: str, max_turns: int, timeout_seconds: int) -> AgentRun | None:
        """Atomically establish a durable deadline and claim one model turn when available."""
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            current = _run_from_row(self._get_row(agent_run_id))
            now = _utc_datetime(utc_now())
            deadline_at = current.deadline_at or (now + timedelta(seconds=timeout_seconds)).isoformat()
            cursor = self._connection.execute(
                "UPDATE agent_runs SET deadline_at = COALESCE(deadline_at, ?), turns_used = turns_used + 1 "
                "WHERE id = ? AND worker_token = ? AND turns_used < ? "
                "AND (deadline_at IS NULL OR deadline_at > ?)",
                (deadline_at, agent_run_id, token, max_turns, now.isoformat()),
            )
            self._connection.commit()
            return replace(current, turns_used=current.turns_used + 1, deadline_at=deadline_at) if cursor.rowcount == 1 else None
        except Exception:
            self._connection.rollback()
            raise

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
                "error_message = ?, completed_at = ?, worker_token = NULL, worker_claimed_at = NULL WHERE status IN ('working', 'waiting')",
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
        release = target in {AgentRunStatus.WAITING, AgentRunStatus.COMPLETED, AgentRunStatus.FAILED, AgentRunStatus.CANCELLED}
        self._connection.execute(
            "UPDATE agent_runs SET status = ?, result_json = ?, error_category = ?, error_message = ?, "
            "started_at = ?, completed_at = ?, worker_token = CASE WHEN ? THEN NULL ELSE worker_token END, "
            "worker_claimed_at = CASE WHEN ? THEN NULL ELSE worker_claimed_at END WHERE id = ?",
            (target, result_json, error_category, error_message, started_at, completed_at, int(release), int(release), agent_run_id),
        )
        return AgentRun(
            id=current.id, task_id=current.task_id, role=current.role, objective=current.objective,
            status=target, result_json=result_json, error_category=error_category,
            error_message=error_message, cancel_requested=current.cancel_requested,
            created_at=current.created_at, started_at=started_at, completed_at=completed_at,
            turns_used=current.turns_used, deadline_at=current.deadline_at,
            worker_token=None if release else current.worker_token, worker_claimed_at=None if release else current.worker_claimed_at,
        )


def _run_from_row(row: sqlite3.Row) -> AgentRun:
    return AgentRun(
        id=row["id"], task_id=row["task_id"], role=AgentRole(row["role"]),
        objective=row["objective"], status=AgentRunStatus(row["status"]),
        result_json=row["result_json"], error_category=row["error_category"],
        error_message=row["error_message"], cancel_requested=bool(row["cancel_requested"]),
        created_at=row["created_at"], started_at=row["started_at"], completed_at=row["completed_at"],
        turns_used=int(row["turns_used"]), deadline_at=row["deadline_at"],
        worker_token=row["worker_token"], worker_claimed_at=row["worker_claimed_at"],
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
        token = str(uuid4())
        claimed = self._repository.claim_worker(run.id, token)
        if claimed is None:
            return self._repository.get_run(run.id)
        try:
            return self._run_owned(claimed, token)
        finally:
            self._repository.release_worker(run.id, token)

    def _run_owned(self, run: AgentRun, token: str) -> AgentRun:
        if self._cancelled(run.id):
            return self._repository.transition_run(run.id, AgentRunStatus.CANCELLED)
        if run.status in {AgentRunStatus.CREATED, AgentRunStatus.WAITING}:
            run = self._repository.transition_run(run.id, AgentRunStatus.WORKING)
        tools = self._tools.restrict(ROLE_TOOLS[run.role])
        while True:
            terminal = self._terminal_before_action(run)
            if terminal is not None:
                return terminal
            reserved = self._repository.reserve_turn(
                run.id, token, self._settings.max_teammate_turns, self._settings.teammate_timeout_seconds
            )
            if reserved is None:
                current = self._repository.get_run(run.id)
                if self._boundary_category(current) == "agent_timeout":
                    return self._repository.fail_run(run.id, "agent_timeout")
                return self._repository.fail_run(run.id, "agent_turn_limit")
            run = reserved
            terminal = self._terminal_before_action(run)
            if terminal is not None:
                return terminal
            try:
                response = self._provider.complete(self._request(run, tools))
            except (ProviderError, ProtectedMessageError):
                return self._repository.fail_run(run.id, "provider_error")
            except Exception:
                return self._repository.fail_run(run.id, "provider_error")
            terminal = self._terminal_before_action(run)
            if terminal is not None:
                return terminal
            if not response.tool_calls:
                try:
                    return self._repository.complete_run(run.id, self._parse_handoff(response.text))
                except ValueError:
                    return self._repository.fail_run(run.id, "invalid_handoff")
            failure = self._record_and_execute_calls(run, tools, response.tool_calls)
            if failure is not None:
                return failure
    def _terminal_before_action(self, run: AgentRun) -> AgentRun | None:
        boundary = self._boundary_category(run)
        if boundary == "cancelled":
            return self._repository.transition_run(run.id, AgentRunStatus.CANCELLED)
        if boundary == "agent_timeout":
            return self._repository.fail_run(run.id, "agent_timeout")
        return None

    def _boundary_category(self, run: AgentRun) -> str | None:
        # Cancellation wins over a coincident deadline because it is explicit user intent.
        if self._cancelled(run.id):
            return "cancelled"
        if run.deadline_at is not None and _utc_datetime(utc_now()) >= _utc_datetime(run.deadline_at):
            return "agent_timeout"
        return None

    def _finish_boundary(self, run_id: str, boundary: str) -> AgentRun:
        if boundary == "cancelled":
            return self._repository.transition_run(run_id, AgentRunStatus.CANCELLED)
        return self._repository.fail_run(run_id, "agent_timeout")

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
        self, run: AgentRun, tools: ToolRegistry, calls: tuple[ToolCall, ...]
    ) -> AgentRun | None:
        try:
            self._record_tool_calls(run, calls)
        except Exception:
            return self._repository.fail_run(run.id, "message_persistence_error")
        context = ToolContext(project_id=self._repository.project_id_for_run(run.id), task_id=run.task_id, agent_id=run.id, run_id=run.id)
        for index, call in enumerate(calls):
            boundary = self._boundary_category(run)
            if boundary is not None:
                category = "cancelled_due_to_cancel" if boundary == "cancelled" else "cancelled_due_to_timeout"
                if not self._try_record_cancelled(run.id, calls[index:], category):
                    return self._repository.fail_run(run.id, "message_persistence_error")
                return self._finish_boundary(run.id, boundary)
            try:
                result = tools.execute(call, context)
            except Exception:
                try:
                    self._record_result(run.id, call.name, ToolResult(call.id, False, {}, "tool_error", "Tool failed"))
                except Exception:
                    return self._repository.fail_run(run.id, "message_persistence_error")
                if not self._try_record_cancelled(run.id, calls[index + 1:], "cancelled_due_to_tool_error"):
                    return self._repository.fail_run(run.id, "message_persistence_error")
                return self._repository.fail_run(run.id, "tool_error")
            boundary = self._boundary_category(run)
            if boundary is not None:
                category = "cancelled_due_to_cancel" if boundary == "cancelled" else "cancelled_due_to_timeout"
                if not self._try_record_cancelled(run.id, calls[index:], category):
                    return self._repository.fail_run(run.id, "message_persistence_error")
                return self._finish_boundary(run.id, boundary)
            try:
                self._record_result(run.id, call.name, result)
            except Exception:
                return self._repository.fail_run(run.id, "message_persistence_error")
            if result.pauses_task:
                if not self._try_record_cancelled(run.id, calls[index + 1:], "cancelled_due_to_pause"):
                    return self._repository.fail_run(run.id, "message_persistence_error")
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
        # Only successful rich database results carry raw previews worth encrypting.
        if tool_name == "query_database" and result.success and _has_rich_database_result(result):
            if self._protected_messages is None:
                raise ProtectedMessageError("Protected teammate message storage is unavailable")
            self._repository.append_protected_message(run_id, "tool", safe, self._protected_messages.protect(raw))
        else:
            self._repository.append_message(run_id, "tool", safe)

    def _record_cancelled(self, run_id: str, calls: tuple[ToolCall, ...], category: str) -> None:
        for call in calls:
            self._record_result(run_id, call.name, ToolResult(call.id, False, {}, category, "Cancelled before execution"))

    def _try_record_cancelled(self, run_id: str, calls: tuple[ToolCall, ...], category: str) -> bool:
        try:
            self._record_cancelled(run_id, calls, category)
            return True
        except Exception:
            return False

    def _parse_handoff(self, text: str) -> str:
        if not text or len(text.encode("utf-8")) > _MAX_HANDOFF_BYTES:
            raise ValueError("invalid handoff")
        try:
            value = json.loads(text, parse_constant=_reject_non_finite_json)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("invalid handoff") from exc
        if not isinstance(value, dict):
            raise ValueError("invalid handoff")
        try:
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid handoff") from exc


RunnerFactory = Callable[[AgentRole, Event], TeammateRunner]


class TeamManager:
    """Owns local execution handles while SQLite remains the source of truth."""

    def __init__(
        self, repository: TeamRepository, settings: Settings, runner_factory: RunnerFactory
    ) -> None:
        self._repository = repository
        self._settings = settings
        self._runner_factory = runner_factory
        self._executor = ThreadPoolExecutor(max_workers=settings.max_active_teammates)
        self._lock = RLock()
        self._futures: dict[str, Future[AgentRun]] = {}
        self._cancel_events: dict[str, Event] = {}
        self._closed = False

    def spawn(self, task_id: str, role: AgentRole, objective: str) -> AgentRun:
        run = self._repository.create_run_if_capacity(
            task_id, role, objective, max_active=self._settings.max_active_teammates
        )
        event = Event()
        with self._lock:
            if self._closed:
                self._repository.request_cancel(run.id)
                self._repository.transition_run(run.id, AgentRunStatus.CANCELLED)
                return self._repository.get_run(run.id)
            self._cancel_events[run.id] = event
            try:
                future = self._executor.submit(self._execute, run.id, AgentRole(role), event)
            except BaseException:
                self._cancel_events.pop(run.id, None)
                return self._repository.fail_run(run.id, "provider_error")
            self._futures[run.id] = future
            future.add_done_callback(lambda completed, run_id=run.id: self._cleanup_done(run_id, completed))
        return run

    def list(self, task_id: str) -> tuple[AgentRun, ...]:
        return self._repository.list_runs(task_id)

    def await_runs(self, run_ids: tuple[str, ...], timeout_seconds: int) -> tuple[AgentRun, ...]:
        if not 1 <= timeout_seconds <= 120:
            raise ValueError("timeout_seconds must be between 1 and 120")
        if not 1 <= len(run_ids) <= 3:
            raise ValueError("one to three agent IDs are required")
        with self._lock:
            futures = [self._futures.get(run_id) for run_id in run_ids]
        wait([future for future in futures if future is not None], timeout=timeout_seconds)
        return tuple(self._repository.get_run(run_id) for run_id in run_ids)

    def cancel(self, agent_run_id: str) -> AgentRun:
        run = self._repository.request_cancel(agent_run_id)
        with self._lock:
            event = self._cancel_events.get(agent_run_id)
            future = self._futures.get(agent_run_id)
        if event is not None:
            event.set()
        if future is not None and future.cancel():
            try:
                return self._repository.transition_run(agent_run_id, AgentRunStatus.CANCELLED)
            except InvalidAgentRunTransition:
                pass
        return self._repository.get_run(agent_run_id)

    def shutdown(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            run_ids = tuple(self._cancel_events)
            events = tuple(self._cancel_events.values())
        for event in events:
            event.set()
        for run_id in run_ids:
            self._repository.request_cancel(run_id)
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _execute(self, run_id: str, role: AgentRole, event: Event) -> AgentRun:
        try:
            return self._runner_factory(role, event).run(run_id)
        except BaseException:
            try:
                current = self._repository.get_run(run_id)
                if current.status in _ACTIVE_STATUSES:
                    return self._repository.fail_run(run_id, "provider_error")
                return current
            except BaseException:
                raise

    def _cleanup_done(self, run_id: str, future: Future[AgentRun]) -> None:
        try:
            future.result()
        except BaseException:
            pass
        with self._lock:
            self._futures.pop(run_id, None)
            self._cancel_events.pop(run_id, None)


def _safe_tool_call(call: ToolCall) -> dict[str, object]:
    if call.name != "query_database":
        return {"id": call.id, "name": call.name, "arguments": call.arguments}
    return {"id": call.id, "name": call.name, "arguments": {
        "sql_hash": sha256(str(call.arguments.get("sql", "")).encode("utf-8")).hexdigest(),
        "data_source_id_hash": sha256(str(call.arguments.get("data_source_id", "")).encode("utf-8")).hexdigest(),
    }}


def _has_rich_database_result(result: ToolResult) -> bool:
    return isinstance(result.output, dict) and any(key in result.output for key in ("preview", "rows", "raw_result"))


def _reject_non_finite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _utc_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


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
