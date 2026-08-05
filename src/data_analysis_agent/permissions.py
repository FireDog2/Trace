from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from data_analysis_agent.db import utc_now


AUTOMATIC_TOOLS = frozenset(
    {
        "inspect_workspace",
        "inspect_dataset",
        "query_data",
        "run_python",
        "save_artifact",
        "write_report",
        "update_plan",
        "request_user_input",
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

READ_ONLY_SQL_PREFIXES = frozenset({"select", "with", "describe", "summarize", "explain"})


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
        if action.tool_name == "query_data" and not _is_read_only_sql(
            str(action.arguments.get("sql", "SELECT 1"))
        ):
            return PermissionDecision(
                PermissionLevel.DENY,
                "Only read-only SQL is allowed",
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

    def request(
        self,
        task_id: str,
        action: ToolAction,
        reason: str,
    ) -> Approval:
        approval = Approval(
            id=str(uuid4()),
            task_id=task_id,
            action=action,
            reason=reason.strip(),
            status="pending",
            created_at=utc_now(),
            decided_at=None,
        )
        self._connection.execute(
            "INSERT INTO approvals("
            "id, task_id, action_json, reason, status, created_at, decided_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                approval.id,
                approval.task_id,
                json.dumps(
                    {
                        "tool_name": action.tool_name,
                        "arguments": action.arguments,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                approval.reason,
                approval.status,
                approval.created_at,
                approval.decided_at,
            ),
        )
        self._connection.commit()
        return approval

    def get(self, approval_id: str) -> Approval:
        row = self._connection.execute(
            "SELECT * FROM approvals WHERE id = ?",
            (approval_id,),
        ).fetchone()
        if row is None:
            raise ApprovalNotFound(approval_id)
        return _approval_from_row(row)

    def decide(self, approval_id: str, approved: bool) -> Approval:
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


def _declares_source_mutation(arguments: dict[str, Any]) -> bool:
    outputs = arguments.get("declared_outputs", ())
    if not isinstance(outputs, list | tuple):
        return False
    for output in outputs:
        normalized = PurePosixPath(str(output).replace("\\", "/"))
        if normalized.parts and normalized.parts[0].lower() == "source":
            return True
    return False


def _contains_absolute_path(arguments: dict[str, Any]) -> bool:
    for key, value in arguments.items():
        if "path" not in key.lower() or not isinstance(value, str):
            continue
        if Path(value).is_absolute():
            return True
    return False


def _is_read_only_sql(sql: str) -> bool:
    without_comments = re.sub(r"/\*.*?\*/|--[^\r\n]*", " ", sql, flags=re.DOTALL)
    statements = [part.strip() for part in without_comments.split(";") if part.strip()]
    if len(statements) != 1:
        return False
    first_word = statements[0].split(maxsplit=1)[0].lower()
    return first_word in READ_ONLY_SQL_PREFIXES


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
