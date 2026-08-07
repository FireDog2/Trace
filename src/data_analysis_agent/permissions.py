from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from data_analysis_agent.db import sqlite_connection_lock, utc_now


AUTOMATIC_TOOLS = frozenset(
    {
        "inspect_workspace",
        "inspect_dataset",
        "search_business_knowledge",
        "query_data",
        "query_database",
        "run_python",
        "save_artifact",
        "write_report",
        "update_plan",
        "request_user_input",
        "spawn_teammate",
        "list_teammates",
        "await_teammates",
        "cancel_teammate",
    }
)

APPROVAL_TOOLS = frozenset(
    {
        "read_external_file",
        "network_access",
        "install_dependencies",
        "overwrite_artifact",
    }
)

class PermissionLevel(StrEnum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class ToolAction:
    tool_name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    level: PermissionLevel
    reason: str


@dataclass(frozen=True, slots=True)
class Approval:
    id: str
    task_id: str
    action: ToolAction
    reason: str
    status: str
    created_at: str
    decided_at: str | None


@dataclass(frozen=True, slots=True)
class ApprovalResolution:
    status: str
    approval: Approval
    created: bool = False


class PermissionError(RuntimeError):
    """Base error for permission and approval operations."""


class ApprovalNotFound(PermissionError):
    """Raised when an approval record does not exist."""


class ApprovalAlreadyDecided(PermissionError):
    """Raised when a pending approval has already been resolved."""


class PermissionPolicy:
    def classify(self, action: ToolAction) -> PermissionDecision:
        if _declares_source_mutation(action.arguments):
            return PermissionDecision(
                PermissionLevel.DENY,
                "Source datasets are immutable",
            )
        if _contains_absolute_path(action.arguments):
            return PermissionDecision(
                PermissionLevel.REQUIRE_APPROVAL,
                "Absolute paths require explicit approval",
            )
        if action.arguments.get("overwrite") is True:
            return PermissionDecision(
                PermissionLevel.REQUIRE_APPROVAL,
                "Overwriting an existing artifact requires approval",
            )
        if action.tool_name in AUTOMATIC_TOOLS:
            return PermissionDecision(
                PermissionLevel.ALLOW,
                "Project-local analysis action",
            )
        if action.tool_name in APPROVAL_TOOLS:
            return PermissionDecision(
                PermissionLevel.REQUIRE_APPROVAL,
                "The action crosses the local automatic permission boundary",
            )
        return PermissionDecision(
            PermissionLevel.DENY,
            "Unknown tools are denied by default",
        )


class ApprovalRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        connection.row_factory = sqlite3.Row
        self._connection = connection
        self._lock = sqlite_connection_lock(connection)

    def request(
        self,
        task_id: str,
        action: ToolAction,
        reason: str,
    ) -> Approval:
        with self._lock:
            approval = _pending_approval(task_id, action, reason)
            _insert_approval(self._connection, approval)
            self._connection.commit()
            return approval

    def get(self, approval_id: str) -> Approval:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM approvals WHERE id = ?",
                (approval_id,),
            ).fetchone()
        if row is None:
            raise ApprovalNotFound(approval_id)
        return _approval_from_row(row)

    def list_for_task(self, task_id: str) -> tuple[Approval, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM approvals WHERE task_id = ? ORDER BY created_at, rowid",
                (task_id,),
            ).fetchall()
        return tuple(_approval_from_row(row) for row in rows)

    def resolve_once(
        self,
        task_id: str,
        action: ToolAction,
        reason: str,
    ) -> ApprovalResolution:
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                resolution = self.resolve_once_in_transaction(task_id, action, reason)
                self._connection.commit()
                return resolution
            except Exception:
                self._connection.rollback()
                raise

    def resolve_once_in_transaction(
        self,
        task_id: str,
        action: ToolAction,
        reason: str,
    ) -> ApprovalResolution:
        """Resolve an approval without committing the caller-owned transaction."""
        with self._lock:
            if not self._connection.in_transaction:
                raise RuntimeError("approval resolution requires an active transaction")
            rows = self._connection.execute(
                "SELECT * FROM approvals "
                "WHERE task_id = ? AND action_json = ? "
                "ORDER BY created_at, rowid",
                (task_id, _action_json(action)),
            ).fetchall()
            approved = next(
                (row for row in rows if row["status"] == "approved"), None
            )
            if approved is not None:
                cursor = self._connection.execute(
                    "UPDATE approvals SET status = 'consumed' "
                    "WHERE id = ? AND status = 'approved'",
                    (approved["id"],),
                )
                if cursor.rowcount == 1:
                    return ApprovalResolution(
                        "authorized",
                        replace(_approval_from_row(approved), status="consumed"),
                    )
            pending = next(
                (row for row in rows if row["status"] == "pending"), None
            )
            if pending is not None:
                return ApprovalResolution("pending", _approval_from_row(pending))
            rejected = next(
                (row for row in reversed(rows) if row["status"] == "rejected"),
                None,
            )
            if rejected is not None:
                return ApprovalResolution("rejected", _approval_from_row(rejected))
            approval = _pending_approval(task_id, action, reason)
            _insert_approval(self._connection, approval)
            return ApprovalResolution("pending", approval, created=True)

    def decide(self, approval_id: str, approved: bool) -> Approval:
        with self._lock:
            decided_at = utc_now()
            status = "approved" if approved else "rejected"
            cursor = self._connection.execute(
                "UPDATE approvals SET status = ?, decided_at = ? "
                "WHERE id = ? AND status = 'pending'",
                (status, decided_at, approval_id),
            )
            self._connection.commit()
            if cursor.rowcount == 0:
                existing = self._connection.execute(
                    "SELECT status FROM approvals WHERE id = ?",
                    (approval_id,),
                ).fetchone()
                if existing is None:
                    raise ApprovalNotFound(approval_id)
                raise ApprovalAlreadyDecided(approval_id)
            return self.get(approval_id)

    def consume_approved(self, task_id: str, action: ToolAction) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT id FROM approvals "
                "WHERE task_id = ? AND action_json = ? AND status = 'approved' "
                "ORDER BY created_at, rowid LIMIT 1",
                (task_id, _action_json(action)),
            ).fetchone()
            if row is None:
                return False
            cursor = self._connection.execute(
                "UPDATE approvals SET status = 'consumed' "
                "WHERE id = ? AND status = 'approved'",
                (row["id"],),
            )
            self._connection.commit()
            return cursor.rowcount == 1


def _declares_source_mutation(arguments: dict[str, Any]) -> bool:
    outputs = arguments.get("declared_outputs", ())
    if not isinstance(outputs, list | tuple):
        return False
    for output in outputs:
        normalized = PurePosixPath(str(output).replace("\\", "/"))
        if normalized.parts and normalized.parts[0].lower() == "source":
            return True
    return False


def _action_json(action: ToolAction) -> str:
    return json.dumps(
        {"tool_name": action.tool_name, "arguments": action.arguments},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _pending_approval(
    task_id: str,
    action: ToolAction,
    reason: str,
) -> Approval:
    return Approval(
        id=str(uuid4()),
        task_id=task_id,
        action=action,
        reason=reason.strip(),
        status="pending",
        created_at=utc_now(),
        decided_at=None,
    )


def _insert_approval(
    connection: sqlite3.Connection,
    approval: Approval,
) -> None:
    connection.execute(
        "INSERT INTO approvals("
        "id, task_id, action_json, reason, status, created_at, decided_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            approval.id,
            approval.task_id,
            _action_json(approval.action),
            approval.reason,
            approval.status,
            approval.created_at,
            approval.decided_at,
        ),
    )


def _contains_absolute_path(arguments: dict[str, Any]) -> bool:
    for key, value in arguments.items():
        if "path" not in key.lower() or not isinstance(value, str):
            continue
        if Path(value).is_absolute():
            return True
    return False


def _approval_from_row(row: sqlite3.Row) -> Approval:
    raw_action = json.loads(row["action_json"])
    return Approval(
        id=row["id"],
        task_id=row["task_id"],
        action=ToolAction(
            tool_name=raw_action["tool_name"],
            arguments=raw_action["arguments"],
        ),
        reason=row["reason"],
        status=row["status"],
        created_at=row["created_at"],
        decided_at=row["decided_at"],
    )
