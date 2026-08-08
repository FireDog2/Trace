from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, replace
from typing import Iterable
from uuid import uuid4

from data_analysis_agent.db import sqlite_connection_guarded, sqlite_connection_lock, utc_now
from data_analysis_agent.prompts import estimate_text_tokens
from data_analysis_agent.providers import ModelMessage
from data_analysis_agent.tools import ToolSchema


@dataclass(frozen=True, slots=True)
class ContextMessage:
    id: str
    role: str
    content: str
    safe_content: str


@dataclass(frozen=True, slots=True)
class ContextCheckpoint:
    id: str
    owner_type: str
    owner_id: str
    through_message_id: str
    summary: str
    source_message_count: int
    before_tokens: int
    after_tokens: int
    reason: str
    created_at: str


@dataclass(frozen=True, slots=True)
class ContextProjection:
    messages: tuple[ModelMessage, ...]
    original_tokens: int
    projected_tokens: int
    compacted: bool
    checkpoint_id: str | None
    reason: str | None
    source_message_count: int


class ContextCheckpointRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        connection.row_factory = sqlite3.Row
        self._connection = connection
        self._lock = sqlite_connection_lock(connection)

    @sqlite_connection_guarded
    def latest(self, owner_type: str, owner_id: str) -> ContextCheckpoint | None:
        _validate_owner(owner_type, owner_id)
        row = self._connection.execute(
            "SELECT * FROM context_checkpoints WHERE owner_type=? AND owner_id=? "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (owner_type, owner_id),
        ).fetchone()
        return _checkpoint(row) if row is not None else None

    @sqlite_connection_guarded
    def create(
        self,
        *,
        owner_type: str,
        owner_id: str,
        through_message_id: str,
        summary: str,
        source_message_count: int,
        before_tokens: int,
        after_tokens: int,
        reason: str,
    ) -> ContextCheckpoint:
        _validate_owner(owner_type, owner_id)
        if reason not in {"auto", "reactive"}:
            raise ValueError("Context checkpoint reason must be auto or reactive")
        if not through_message_id or not summary.strip():
            raise ValueError("Context checkpoint requires a boundary and summary")
        checkpoint = ContextCheckpoint(
            id=str(uuid4()),
            owner_type=owner_type,
            owner_id=owner_id,
            through_message_id=through_message_id,
            summary=summary.strip(),
            source_message_count=source_message_count,
            before_tokens=before_tokens,
            after_tokens=after_tokens,
            reason=reason,
            created_at=utc_now(),
        )
        self._connection.execute(
            "INSERT INTO context_checkpoints(id,owner_type,owner_id,through_message_id,summary,"
            "source_message_count,before_tokens,after_tokens,reason,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                checkpoint.id,
                checkpoint.owner_type,
                checkpoint.owner_id,
                checkpoint.through_message_id,
                checkpoint.summary,
                checkpoint.source_message_count,
                checkpoint.before_tokens,
                checkpoint.after_tokens,
                checkpoint.reason,
                checkpoint.created_at,
            ),
        )
        self._connection.commit()
        return checkpoint

    @sqlite_connection_guarded
    def list_for_owner(self, owner_type: str, owner_id: str) -> tuple[ContextCheckpoint, ...]:
        _validate_owner(owner_type, owner_id)
        return tuple(
            _checkpoint(row)
            for row in self._connection.execute(
                "SELECT * FROM context_checkpoints WHERE owner_type=? AND owner_id=? "
                "ORDER BY created_at,rowid",
                (owner_type, owner_id),
            )
        )


class ContextManager:
    """Builds a bounded model view without mutating the durable transcript."""

    def __init__(
        self,
        repository: ContextCheckpointRepository,
        *,
        max_tokens: int,
        target_tokens: int,
        recent_messages: int,
        max_tool_result_chars: int,
        max_checkpoint_chars: int = 12_000,
    ) -> None:
        if not 1 <= target_tokens <= max_tokens:
            raise ValueError("Context target_tokens must be within max_tokens")
        if recent_messages < 2:
            raise ValueError("Context recent_messages must be at least 2")
        self._repository = repository
        self._max_tokens = max_tokens
        self._target_tokens = target_tokens
        self._recent_messages = recent_messages
        self._max_tool_result_chars = max_tool_result_chars
        self._max_checkpoint_chars = max_checkpoint_chars

    def project(
        self,
        *,
        owner_type: str,
        owner_id: str,
        system: str,
        objective: str,
        messages: tuple[ContextMessage, ...],
        tools: tuple[ToolSchema, ...],
        force: bool = False,
    ) -> ContextProjection:
        _validate_owner(owner_type, owner_id)
        original = _request_tokens(system, objective, messages, tools)
        checkpoint = self._repository.latest(owner_type, owner_id)
        start_index = _checkpoint_start(messages, checkpoint)
        visible = list(messages[start_index:])
        projected = _with_checkpoint(checkpoint, visible)
        projected = _budget_tool_results(projected, self._max_tool_result_chars)
        projected_tokens = _request_tokens(system, objective, projected, tools)

        if not force and projected_tokens <= self._max_tokens:
            return ContextProjection(
                messages=_model_messages(projected),
                original_tokens=original,
                projected_tokens=projected_tokens,
                compacted=checkpoint is not None,
                checkpoint_id=checkpoint.id if checkpoint else None,
                reason=None,
                source_message_count=len(messages),
            )

        boundary = _recent_boundary(messages, self._recent_messages, aggressive=True)
        if boundary <= start_index and len(messages) > start_index + 1:
            boundary = _recent_boundary(messages, 2, aggressive=True)
        if boundary <= start_index:
            # A single oversized recent result can still be bounded without inventing a checkpoint.
            return ContextProjection(
                messages=_model_messages(projected),
                original_tokens=original,
                projected_tokens=projected_tokens,
                compacted=checkpoint is not None,
                checkpoint_id=checkpoint.id if checkpoint else None,
                reason="reactive" if force else None,
                source_message_count=len(messages),
            )

        summary = _summarize(
            previous=checkpoint.summary if checkpoint else "",
            messages=messages[start_index:boundary],
            max_chars=self._max_checkpoint_chars,
        )
        reason = "reactive" if force else "auto"
        tail = list(messages[boundary:])
        preview_checkpoint = ContextCheckpoint(
            id="pending",
            owner_type=owner_type,
            owner_id=owner_id,
            through_message_id=messages[boundary - 1].id,
            summary=summary,
            source_message_count=boundary,
            before_tokens=original,
            after_tokens=0,
            reason=reason,
            created_at="",
        )
        bounded_tail = _budget_tool_results(
            _with_checkpoint(preview_checkpoint, tail), self._max_tool_result_chars
        )
        after_tokens = _request_tokens(system, objective, bounded_tail, tools)
        if after_tokens > self._target_tokens:
            summary = summary[: max(512, self._max_checkpoint_chars // 2)]
            preview_checkpoint = replace(preview_checkpoint, summary=summary)
            bounded_tail = _budget_tool_results(
                _with_checkpoint(preview_checkpoint, tail), self._max_tool_result_chars
            )
            after_tokens = _request_tokens(system, objective, bounded_tail, tools)

        created = self._repository.create(
            owner_type=owner_type,
            owner_id=owner_id,
            through_message_id=messages[boundary - 1].id,
            summary=summary,
            source_message_count=boundary,
            before_tokens=original,
            after_tokens=after_tokens,
            reason=reason,
        )
        final_messages = _budget_tool_results(
            _with_checkpoint(created, tail), self._max_tool_result_chars
        )
        return ContextProjection(
            messages=_model_messages(final_messages),
            original_tokens=original,
            projected_tokens=_request_tokens(system, objective, final_messages, tools),
            compacted=True,
            checkpoint_id=created.id,
            reason=reason,
            source_message_count=len(messages),
        )


def _validate_owner(owner_type: str, owner_id: str) -> None:
    if owner_type not in {"task", "agent"} or not owner_id:
        raise ValueError("Context checkpoint owner must be a task or agent")


def _checkpoint(row: sqlite3.Row) -> ContextCheckpoint:
    return ContextCheckpoint(
        row["id"], row["owner_type"], row["owner_id"], row["through_message_id"],
        row["summary"], row["source_message_count"], row["before_tokens"],
        row["after_tokens"], row["reason"], row["created_at"],
    )


def _checkpoint_start(
    messages: tuple[ContextMessage, ...], checkpoint: ContextCheckpoint | None
) -> int:
    if checkpoint is None:
        return 0
    for index, message in enumerate(messages):
        if message.id == checkpoint.through_message_id:
            return index + 1
    return 0


def _with_checkpoint(
    checkpoint: ContextCheckpoint | None, messages: Iterable[ContextMessage]
) -> list[ContextMessage]:
    output = list(messages)
    if checkpoint is None:
        return output
    return [
        ContextMessage(
            id=f"checkpoint:{checkpoint.id}",
            role="system",
            content="[Session checkpoint]\n" + checkpoint.summary,
            safe_content="[Session checkpoint]\n" + checkpoint.summary,
        ),
        *output,
    ]


def _recent_boundary(
    messages: tuple[ContextMessage, ...], recent_messages: int, *, aggressive: bool
) -> int:
    if len(messages) <= recent_messages:
        if not aggressive or len(messages) <= 1:
            return 0
        start = max(1, len(messages) // 2)
    else:
        start = len(messages) - recent_messages
    while start > 0 and messages[start].role == "tool":
        start -= 1
    return start


def _budget_tool_results(
    messages: list[ContextMessage], max_chars: int
) -> list[ContextMessage]:
    output: list[ContextMessage] = []
    for message in messages:
        if message.role != "tool" or len(message.content) <= max_chars:
            output.append(message)
            continue
        compacted = _compact_tool_result(message.safe_content, max_chars)
        output.append(
            ContextMessage(message.id, message.role, compacted, compacted)
        )
    return output


def _compact_tool_result(content: str, max_chars: int) -> str:
    try:
        value = json.loads(content)
    except (TypeError, ValueError, json.JSONDecodeError):
        return json.dumps(
            {"call_id": "unknown", "success": False, "output": {"compacted": True},
             "message": content[: max(64, max_chars // 2)]},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    output = value.get("output") if isinstance(value, dict) else None
    kept: dict[str, object] = {"compacted": True}
    if isinstance(output, dict):
        for key in (
            "status", "artifact_id", "artifact_ids", "query_hash", "row_count", "risk",
            "path", "report_id", "result_summary",
        ):
            if key in output:
                kept[key] = output[key]
    bounded = {
        "call_id": value.get("call_id", "unknown") if isinstance(value, dict) else "unknown",
        "success": bool(value.get("success")) if isinstance(value, dict) else False,
        "output": kept,
        "error_category": value.get("error_category") if isinstance(value, dict) else None,
        "message": value.get("message") if isinstance(value, dict) else None,
        "retryable": bool(value.get("retryable")) if isinstance(value, dict) else False,
    }
    return json.dumps(bounded, ensure_ascii=False, separators=(",", ":"))


def _summarize(
    *, previous: str, messages: tuple[ContextMessage, ...], max_chars: int
) -> str:
    lines = [
        "This checkpoint is a bounded projection of the durable transcript. The transcript remains authoritative.",
    ]
    if previous:
        lines.extend(("Prior checkpoint:", previous))
    for message in messages:
        if message.role == "assistant_tool_call":
            lines.append(_tool_call_summary(message.safe_content))
        elif message.role == "tool":
            lines.append(_tool_result_summary(message.safe_content))
        else:
            normalized = " ".join(message.safe_content.split())
            lines.append(f"{message.role}: {normalized[:1000]}")
        if sum(len(line) + 1 for line in lines) >= max_chars:
            break
    return "\n".join(lines)[:max_chars]


def _tool_call_summary(content: str) -> str:
    try:
        calls = json.loads(content).get("tool_calls", [])
        labels = [
            f"{item.get('name', 'unknown')}#{item.get('id', 'unknown')}"
            for item in calls if isinstance(item, dict)
        ]
        return "assistant tool calls: " + ", ".join(labels)
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        return "assistant tool calls: [unavailable]"


def _tool_result_summary(content: str) -> str:
    try:
        value = json.loads(content)
        output = value.get("output") if isinstance(value, dict) else {}
        evidence = []
        if isinstance(output, dict):
            for key in ("artifact_id", "artifact_ids", "query_hash", "row_count", "status", "result_summary"):
                if key in output:
                    evidence.append(f"{key}={str(output[key])[:500]}")
        return (
            f"tool result {value.get('call_id', 'unknown')}: success={bool(value.get('success'))}; "
            f"error={value.get('error_category')}; " + "; ".join(evidence)
        )
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        return "tool result: [unavailable]"


def _model_messages(messages: Iterable[ContextMessage]) -> tuple[ModelMessage, ...]:
    return tuple(ModelMessage(message.role, message.content) for message in messages)


def _request_tokens(
    system: str,
    objective: str,
    messages: Iterable[ContextMessage],
    tools: tuple[ToolSchema, ...],
) -> int:
    total = estimate_text_tokens(system) + estimate_text_tokens(objective)
    total += sum(estimate_text_tokens(message.content) + 4 for message in messages)
    total += estimate_text_tokens(
        json.dumps(
            [
                {"name": tool.name, "description": tool.description, "input_schema": tool.input_schema}
                for tool in tools
            ],
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
    )
    return total
