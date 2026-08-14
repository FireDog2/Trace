from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, replace
from time import monotonic
from typing import Any, Iterator
from uuid import uuid4

from data_analysis_agent.db import (
    sqlite_connection_guarded,
    sqlite_connection_lock,
    utc_now,
)
from data_analysis_agent.repositories import RecordNotFoundError

_SECRET_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "cookie",
        "dsn",
        "password",
        "protected_api_key",
        "protected_dsn",
        "secret",
        "token",
    }
)


def redact_payload(value: Any, depth: int = 0) -> Any:
    if depth >= 8:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        return {
            str(k): "[REDACTED]" if str(k).lower() in _SECRET_KEYS else redact_payload(v, depth + 1)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_payload(v, depth + 1) for v in value[:100]]
    if isinstance(value, str):
        lowered = value.lower()
        if "bearer " in lowered or "://" in value and ("@" in value or "postgres" in lowered):
            return "[REDACTED]"
        return value[:4096] + ("…" if len(value) > 4096 else "")
    return value if value is None or isinstance(value, (bool, int, float)) else str(value)[:4096]


@dataclass(frozen=True, slots=True)
class AnalysisRun:
    id: str
    task_id: str
    status: str
    accepted: bool
    started_at: str
    completed_at: str | None
    error_category: str | None


@dataclass(frozen=True, slots=True)
class TraceEvent:
    id: str
    run_id: str
    task_id: str
    agent_id: str | None
    parent_event_id: str | None
    sequence: int
    event_type: str
    status: str
    payload: dict[str, Any]
    started_at: str
    finished_at: str | None
    duration_ms: int | None
    attempt: int
    error_category: str | None
    artifact_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TraceContext:
    run_id: str
    task_id: str
    agent_id: str | None = None
    parent_event_id: str | None = None


class TraceRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        connection.row_factory = sqlite3.Row
        self._connection = connection
        self._lock = sqlite_connection_lock(connection)

    @sqlite_connection_guarded
    def start_run(self, task_id: str) -> AnalysisRun:
        if (
            self._connection.execute("SELECT 1 FROM tasks WHERE id=?", (task_id,)).fetchone()
            is None
        ):
            raise RecordNotFoundError("Task not found")
        run = AnalysisRun(str(uuid4()), task_id, "running", False, utc_now(), None, None)
        self._connection.execute(
            "INSERT INTO analysis_runs VALUES (?,?,?,?,?,?,?)",
            (run.id, run.task_id, run.status, 0, run.started_at, None, None),
        )
        self._connection.commit()
        return run

    @sqlite_connection_guarded
    def get_run(self, run_id: str) -> AnalysisRun:
        row = self._connection.execute(
            "SELECT * FROM analysis_runs WHERE id=?", (run_id,)
        ).fetchone()
        if row is None:
            raise RecordNotFoundError("Analysis run not found")
        return _run(row)

    @sqlite_connection_guarded
    def finish_run(
        self, run_id: str, status: str, error_category: str | None = None
    ) -> AnalysisRun:
        if status not in {"completed", "failed", "cancelled"}:
            raise ValueError("Invalid run status")
        self.get_run(run_id)
        self._connection.execute(
            "UPDATE analysis_runs SET status=?, completed_at=?, error_category=? WHERE id=?",
            (status, utc_now(), error_category, run_id),
        )
        self._connection.commit()
        return self.get_run(run_id)

    @sqlite_connection_guarded
    def accept_run(self, run_id: str) -> AnalysisRun:
        run = self.get_run(run_id)
        if run.status != "completed":
            raise ValueError("Only completed runs can be accepted")
        self._connection.execute("UPDATE analysis_runs SET accepted=1 WHERE id=?", (run_id,))
        self._connection.commit()
        return self.get_run(run_id)

    @sqlite_connection_guarded
    def append(
        self,
        run_id: str,
        task_id: str,
        event_type: str,
        status: str,
        payload: dict[str, Any],
        *,
        agent_id: str | None = None,
        parent_event_id: str | None = None,
        attempt: int = 1,
        error_category: str | None = None,
        artifact_ids: tuple[str, ...] = (),
    ) -> TraceEvent:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            run = self.get_run(run_id)
            if run.task_id != task_id:
                raise ValueError("Run does not belong to task")
            seq = int(
                self._connection.execute(
                    "SELECT COALESCE(MAX(sequence),0)+1 FROM trace_events WHERE run_id=?",
                    (run_id,),
                ).fetchone()[0]
            )
            event = TraceEvent(
                str(uuid4()),
                run_id,
                task_id,
                agent_id,
                parent_event_id,
                seq,
                event_type,
                status,
                redact_payload(payload),
                utc_now(),
                None,
                None,
                attempt,
                error_category,
                tuple(artifact_ids),
            )
            self._connection.execute(
                "INSERT INTO trace_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    event.id,
                    event.run_id,
                    event.task_id,
                    event.agent_id,
                    event.parent_event_id,
                    event.sequence,
                    event.event_type,
                    event.status,
                    json.dumps(event.payload, ensure_ascii=False),
                    event.started_at,
                    None,
                    None,
                    event.attempt,
                    event.error_category,
                    json.dumps(event.artifact_ids),
                ),
            )
            self._connection.commit()
            return event
        except Exception:
            self._connection.rollback()
            raise

    @sqlite_connection_guarded
    def complete_event(
        self,
        event_id: str,
        status: str,
        duration_ms: int,
        error_category: str | None = None,
    ) -> None:
        self._connection.execute(
            "UPDATE trace_events SET status=?,finished_at=?,duration_ms=?,error_category=? WHERE id=?",
            (status, utc_now(), duration_ms, error_category, event_id),
        )
        self._connection.commit()

    @sqlite_connection_guarded
    def list_runs(self, task_id: str) -> tuple[AnalysisRun, ...]:
        return tuple(
            _run(r)
            for r in self._connection.execute(
                "SELECT * FROM analysis_runs WHERE task_id=? ORDER BY started_at,rowid",
                (task_id,),
            )
        )

    @sqlite_connection_guarded
    def events_for_run(self, run_id: str) -> tuple[TraceEvent, ...]:
        self.get_run(run_id)
        return tuple(
            _event(r)
            for r in self._connection.execute(
                "SELECT * FROM trace_events WHERE run_id=? ORDER BY sequence", (run_id,)
            )
        )


class TraceRecorder:
    def __init__(self, repository: TraceRepository) -> None:
        self.repository = repository

    def event(
        self,
        context: TraceContext,
        event_type: str,
        status: str,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> TraceEvent:
        return self.repository.append(
            context.run_id,
            context.task_id,
            event_type,
            status,
            payload,
            agent_id=context.agent_id,
            parent_event_id=context.parent_event_id,
            **kwargs,
        )

    @contextmanager
    def span(
        self,
        context: TraceContext,
        event_type: str,
        payload: dict[str, Any],
        *,
        attempt: int = 1,
    ) -> Iterator[TraceContext]:
        started = monotonic()
        event = self.event(context, event_type, "running", payload, attempt=attempt)
        try:
            yield replace(context, parent_event_id=event.id)
        except Exception as exc:
            self.repository.complete_event(
                event.id,
                "failed",
                int((monotonic() - started) * 1000),
                type(exc).__name__,
            )
            raise
        else:
            self.repository.complete_event(
                event.id, "completed", int((monotonic() - started) * 1000)
            )


def _run(row: sqlite3.Row) -> AnalysisRun:
    return AnalysisRun(
        row["id"],
        row["task_id"],
        row["status"],
        bool(row["accepted"]),
        row["started_at"],
        row["completed_at"],
        row["error_category"],
    )


def _event(row: sqlite3.Row) -> TraceEvent:
    return TraceEvent(
        row["id"],
        row["run_id"],
        row["task_id"],
        row["agent_id"],
        row["parent_event_id"],
        row["sequence"],
        row["event_type"],
        row["status"],
        json.loads(row["payload_json"]),
        row["started_at"],
        row["finished_at"],
        row["duration_ms"],
        row["attempt"],
        row["error_category"],
        tuple(json.loads(row["artifact_ids_json"])),
    )
