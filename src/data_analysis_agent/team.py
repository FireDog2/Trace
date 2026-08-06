from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from enum import StrEnum
from uuid import uuid4

from data_analysis_agent.db import (
    sqlite_connection_guarded,
    sqlite_connection_lock,
    utc_now,
)


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
}


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
