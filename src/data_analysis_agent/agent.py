from __future__ import annotations

import json
import time
from dataclasses import asdict, replace
from hashlib import sha256
from typing import Callable

from data_analysis_agent.context import (
    ContextCheckpointRepository,
    ContextManager,
    ContextMessage,
)
from data_analysis_agent.domain import Task, TaskStatus
from data_analysis_agent.model_config import SecretProtector
from data_analysis_agent.prompts import PromptContext, assemble_prompt
from data_analysis_agent.providers import (
    ModelProvider,
    ModelRequest,
    ProviderError,
)
from data_analysis_agent.recovery import RecoveryPolicy, RecoveryState
from data_analysis_agent.repositories import (
    ProtectedMessageError,
    ProtectedToolMessageRepository,
    TaskRepository,
)
from data_analysis_agent.settings import Settings
from data_analysis_agent.tools import ToolCall, ToolContext, ToolRegistry, ToolResult
from data_analysis_agent.tracing import TraceContext, TraceRecorder, TraceRepository

SYSTEM_PROMPT = assemble_prompt(PromptContext(role="lead")).text

_PROTECTED_TOOL_NAMES = frozenset({"query_database", "await_teammates"})
_TRANSIENT_PROVIDER_ERRORS = frozenset({"timeout", "network", "rate_limit", "upstream"})
_APPROVAL_RESUME_MESSAGE = (
    "Human approval was granted and waiting teammates were resumed. "
    "Do not request confirmation again and do not substitute a direct data query. "
    "Use list_teammates or await_teammates to read the existing teammate result, "
    "then continue from its handoff."
)


class AgentRunner:
    def __init__(
        self,
        *,
        settings: Settings,
        provider: ModelProvider,
        tools: ToolRegistry,
        tasks: TaskRepository,
        secret_protector: SecretProtector | None = None,
        traces: TraceRepository | None = None,
        retry_sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._settings = settings
        self._provider = provider
        self._tools = tools
        self._tasks = tasks
        self._protected_messages = (
            ProtectedToolMessageRepository(tasks._connection, secret_protector)
            if secret_protector is not None
            else None
        )
        self._traces = traces
        self._trace_recorder = TraceRecorder(traces) if traces is not None else None
        self._retry_sleep = retry_sleep or time.sleep
        self._context = ContextManager(
            ContextCheckpointRepository(tasks._connection),
            max_tokens=settings.context_max_tokens,
            target_tokens=settings.context_target_tokens,
            recent_messages=settings.context_recent_messages,
            max_tool_result_chars=settings.context_max_tool_result_chars,
        )

    def run(self, task_id: str, trace_context: TraceContext | None = None) -> Task:
        task = self._tasks.get(task_id)
        if task.status in {
            TaskStatus.COMPLETED,
            TaskStatus.CANCELLED,
            TaskStatus.NEEDS_INPUT,
        }:
            return task

        owns_trace = self._traces is not None and trace_context is None
        if self._traces is not None and trace_context is None:
            run = self._traces.start_run(task_id)
            trace_context = TraceContext(run.id, task_id)

        def finish_trace(status: str, error_category: str | None = None) -> None:
            if owns_trace and self._traces is not None and trace_context is not None:
                self._traces.finish_run(trace_context.run_id, status, error_category)

        def trace(event_type: str, status: str, payload: dict[str, object], **kwargs: object):
            if trace_context is not None and self._trace_recorder is not None:
                try:
                    return self._trace_recorder.event(
                        trace_context, event_type, status, payload, **kwargs
                    )
                except Exception:
                    try:
                        self._tasks.append_event(
                            task_id,
                            "trace.recording_failed",
                            {"event_type": event_type},
                        )
                    except Exception:
                        pass
            return None

        trace("agent.started", "running", {"role": "lead"})
        context = ToolContext(
            project_id=task.project_id,
            task_id=task.id,
            run_id=trace_context.run_id if trace_context else None,
        )
        action_count = 0
        recovery = RecoveryState(RecoveryPolicy(max_attempts=self._settings.max_tool_attempts))
        last_signature = ""
        repeated_count = 0
        while action_count < self._settings.max_tool_actions:
            try:
                response = self._model_turn(task_id, trace)
                trace(
                    "model.completed",
                    "completed",
                    {
                        "tool_call_count": len(response.tool_calls),
                        "has_text": bool(response.text.strip()),
                    },
                )
            except (ProviderError, ProtectedMessageError) as exc:
                failed_task = self._fail_task(task_id, "provider_error", str(exc))
                trace("agent.completed", "failed", {"category": "provider_error"})
                finish_trace("failed", "provider_error")
                return failed_task

            if not response.tool_calls:
                active_lookup = getattr(self._tools, "active_teammate_ids", None)
                active_agent_ids = (
                    tuple(active_lookup(task_id)) if callable(active_lookup) else ()
                )
                if active_agent_ids:
                    response = replace(
                        response,
                        text="",
                        tool_calls=(
                            ToolCall(
                                id=f"auto-await-{action_count + 1}",
                                name="await_teammates",
                                arguments={
                                    "agent_ids": list(active_agent_ids),
                                    "timeout_seconds": min(
                                        self._settings.teammate_timeout_seconds, 120
                                    ),
                                },
                            ),
                        ),
                    )
                    trace(
                        "teammate.await.enforced",
                        "running",
                        {"agent_ids": list(active_agent_ids)},
                    )

            if response.text.strip():
                message = self._tasks.add_message(
                    task_id,
                    "assistant",
                    response.text,
                )
                self._tasks.append_event(
                    task_id,
                    "message.created",
                    {"message_id": message.id, "role": message.role},
                )

            if not response.tool_calls:
                completed_task = self._complete_task(task_id)
                trace("agent.completed", "completed", {})
                finish_trace("completed")
                return completed_task

            raw_tool_calls = {
                "tool_calls": [
                    {"id": call.id, "name": call.name, "arguments": call.arguments}
                    for call in response.tool_calls
                ]
            }
            safe_tool_calls = json.dumps(
                {"tool_calls": [_safe_tool_call(call) for call in response.tool_calls]},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            if any(call.name in _PROTECTED_TOOL_NAMES for call in response.tool_calls):
                if self._protected_messages is None:
                    return self._fail_task(
                        task_id,
                        "message_protection_error",
                        "Protected tool message storage is unavailable",
                    )
                try:
                    encrypted = self._protected_messages.protect(
                        json.dumps(raw_tool_calls, ensure_ascii=False, separators=(",", ":"))
                    )
                    tool_call_message = self._tasks.add_protected_tool_message(
                        task_id, "assistant_tool_call", safe_tool_calls, encrypted
                    )
                except ProtectedMessageError:
                    return self._fail_task(
                        task_id,
                        "message_protection_error",
                        "Protected tool message storage is unavailable",
                    )
            else:
                tool_call_message = self._tasks.add_message(
                    task_id, "assistant_tool_call", safe_tool_calls
                )
            self._tasks.append_event(
                task_id,
                "message.created",
                {"message_id": tool_call_message.id, "role": tool_call_message.role},
            )

            for call_index, call in enumerate(response.tool_calls):
                action_count += 1
                signature = json.dumps(
                    {"name": call.name, "arguments": call.arguments},
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                if signature == last_signature:
                    repeated_count += 1
                else:
                    last_signature = signature
                    repeated_count = 1
                if repeated_count > 2:
                    return self._fail_task(
                        task_id,
                        "repeated_tool_call",
                        "The same tool call failed to make progress",
                    )

                tool_event = trace("tool.started", "running", {"tool": call.name})
                call_context = replace(
                    context,
                    parent_event_id=tool_event.id if tool_event is not None else None,
                )
                tool_result = self._tools.execute(call, call_context)
                if not tool_result.success:
                    recovery_decision = recovery.record(
                        signature,
                        tool_result.error_category,
                        retryable=tool_result.retryable,
                    )
                    if tool_result.retryable:
                        diagnostics = dict(tool_result.diagnostics or {})
                        diagnostics["recovery"] = asdict(recovery_decision)
                        tool_result = replace(
                            tool_result,
                            retryable=recovery_decision.retry_allowed,
                            diagnostics=diagnostics,
                        )
                        trace(
                            "tool.retry" if recovery_decision.retry_allowed else "tool.fallback",
                            "retrying" if recovery_decision.retry_allowed else "completed",
                            {
                                "tool": call.name,
                                "category": tool_result.error_category,
                                "attempt": recovery_decision.attempt,
                                "remaining": recovery_decision.remaining,
                            },
                            error_category=tool_result.error_category,
                        )
                artifact_ids = _result_artifact_ids(tool_result)
                trace(
                    "tool.completed",
                    "completed" if tool_result.success else "failed",
                    {
                        "tool": call.name,
                        "success": tool_result.success,
                        "error_category": tool_result.error_category,
                    },
                    error_category=tool_result.error_category,
                    artifact_ids=artifact_ids,
                )
                protected_result: bytes | None = None
                if call.name in _PROTECTED_TOOL_NAMES and tool_result.success:
                    assert self._protected_messages is not None
                    try:
                        protected_result = self._protected_messages.protect(
                            _tool_result_json(tool_result)
                        )
                    except ProtectedMessageError:
                        return self._fail_task(
                            task_id,
                            "message_protection_error",
                            "Protected tool message storage is unavailable",
                        )
                try:
                    message = self._record_tool_result(
                        task_id, tool_result, call.name, protected_result
                    )
                except ProtectedMessageError:
                    return self._fail_task(
                        task_id,
                        "message_protection_error",
                        "Protected tool message storage is unavailable",
                    )
                task_needs_input = self._tasks.get(task_id).status is TaskStatus.NEEDS_INPUT
                if tool_result.pauses_task or task_needs_input:
                    for cancelled in response.tool_calls[call_index + 1 :]:
                        self._record_tool_result(
                            task_id,
                            ToolResult(
                                call_id=cancelled.id,
                                success=False,
                                output={},
                                error_category="cancelled_due_to_pause",
                                message="Cancelled because an earlier tool call paused the task",
                                retryable=False,
                            ),
                            cancelled.name,
                        )
                    if call.name == "request_user_input" and tool_result.success:
                        question = tool_result.output.get("question")
                        if isinstance(question, str) and question:
                            assistant_message = self._tasks.add_message(
                                task_id, "assistant", question
                            )
                            self._tasks.append_event(
                                task_id,
                                "message.created",
                                {
                                    "message_id": assistant_message.id,
                                    "role": assistant_message.role,
                                },
                            )
                    current = self._tasks.get(task_id)
                    trace("agent.completed", "completed", {"paused": True})
                    finish_trace("completed")
                    return current
                if action_count >= self._settings.max_tool_actions:
                    break

        failed_task = self._fail_task(
            task_id,
            "tool_action_limit",
            "Tool action limit reached",
        )
        trace("agent.completed", "failed", {"category": "tool_action_limit"})
        finish_trace("failed", "tool_action_limit")
        return failed_task

    def _complete_model(
        self,
        request: ModelRequest,
        trace: Callable[..., None],
    ):
        for retry_number in range(self._settings.model_max_retries + 1):
            try:
                return self._provider.complete(request)
            except ProviderError as exc:
                retryable = exc.category in _TRANSIENT_PROVIDER_ERRORS
                if not retryable or retry_number >= self._settings.model_max_retries:
                    raise
                delay = min(4.0, 0.25 * (2**retry_number))
                trace(
                    "model.retry",
                    "retrying",
                    {
                        "category": exc.category,
                        "attempt": retry_number + 1,
                        "remaining": self._settings.model_max_retries - retry_number,
                        "delay_seconds": delay,
                    },
                )
                self._retry_sleep(delay)

        raise RuntimeError("unreachable")

    def _model_turn(self, task_id: str, trace: Callable[..., None]):
        request = self._build_request(task_id, trace=trace)
        try:
            return self._complete_model(request, trace)
        except ProviderError as exc:
            if exc.category != "context_length_exceeded":
                raise
            trace(
                "context.rejected",
                "retrying",
                {"owner_type": "task", "reason": "provider_context_length"},
                error_category=exc.category,
            )
            compacted = self._build_request(task_id, force_context_compact=True, trace=trace)
            return self._complete_model(compacted, trace)

    def resume(self, task_id: str, user_message: str) -> Task:
        task = self._tasks.get(task_id)
        if task.status is not TaskStatus.NEEDS_INPUT:
            raise ValueError("Only a task waiting for input can be resumed")
        return self._continue(task_id, user_message, task.status)

    def follow_up(self, task_id: str, user_message: str) -> Task:
        task = self._tasks.get(task_id)
        if task.status is not TaskStatus.COMPLETED:
            raise ValueError("Only a completed task accepts a follow-up")
        return self._continue(task_id, user_message, task.status)

    def retry(self, task_id: str, user_message: str) -> Task:
        task = self._tasks.get(task_id)
        if task.status is not TaskStatus.FAILED:
            raise ValueError("Only a failed task can be retried")
        message = self._tasks.add_message(task_id, "user", user_message)
        self._tasks.transition(task_id, TaskStatus.RUNNING)
        self._tasks.append_event(
            task_id,
            "task.retried",
            {"message_id": message.id, "reason": "user_retry"},
        )
        return self.run(task_id)

    def resume_approval(
        self,
        task_id: str,
        resume_waiting: Callable[[], object] | None = None,
    ) -> Task:
        task = self._tasks.get(task_id)
        if task.status is not TaskStatus.NEEDS_INPUT:
            raise ValueError("Only a task waiting for approval can be resumed")
        self._tasks.transition(task_id, TaskStatus.UNDERSTANDING)
        self._tasks.append_event(
            task_id,
            "task.resumed",
            {"reason": "approval_granted"},
        )
        if resume_waiting is not None:
            resume_waiting()
        message = self._tasks.add_message(task_id, "system", _APPROVAL_RESUME_MESSAGE)
        self._tasks.append_event(
            task_id,
            "message.created",
            {"message_id": message.id, "role": message.role},
        )
        return self.run(task_id)

    def _continue(
        self,
        task_id: str,
        user_message: str,
        previous_status: TaskStatus,
    ) -> Task:
        message = self._tasks.add_message(task_id, "user", user_message)
        self._tasks.append_event(
            task_id,
            "message.created",
            {"message_id": message.id, "role": message.role},
        )
        if previous_status is TaskStatus.COMPLETED:
            self._tasks.reopen_completed(task_id)
            self._tasks.append_event(
                task_id,
                "task.reopened",
                {"reason": "follow_up"},
            )
        else:
            self._tasks.transition(task_id, TaskStatus.UNDERSTANDING)
        return self.run(task_id)

    def _build_request(
        self,
        task_id: str,
        *,
        force_context_compact: bool = False,
        trace: Callable[..., None] | None = None,
    ) -> ModelRequest:
        task = self._tasks.get(task_id)
        schemas = self._tools.schemas()
        assembly = assemble_prompt(
            PromptContext(
                role="lead",
                project_id=task.project_id,
                task_id=task.id,
                task_status=task.status.value,
                enabled_tools=tuple(schema.name for schema in schemas),
            )
        )
        messages = tuple(
            ContextMessage(
                id=message.id,
                role=message.role,
                content=self._provider_content(message.id, message.content),
                safe_content=message.content,
            )
            for message in self._tasks.list_messages(task_id)
        )
        projection = self._context.project(
            owner_type="task",
            owner_id=task.id,
            system=assembly.text,
            objective=task.objective,
            messages=messages,
            tools=schemas,
            force=force_context_compact,
        )
        if trace is not None:
            trace(
                "prompt.assembled",
                "completed",
                {
                    "role": "lead",
                    "section_ids": assembly.section_ids,
                    "section_versions": {
                        section.id: section.version for section in assembly.sections
                    },
                    "prompt_hash": assembly.content_hash,
                    "prompt_tokens": assembly.estimated_tokens,
                    "context_tokens": projection.projected_tokens,
                },
            )
            if projection.reason is not None:
                trace(
                    "context.compacted",
                    "completed",
                    {
                        "owner_type": "task",
                        "reason": projection.reason,
                        "checkpoint_id": projection.checkpoint_id,
                        "before_tokens": projection.original_tokens,
                        "after_tokens": projection.projected_tokens,
                        "source_message_count": projection.source_message_count,
                    },
                )
        return ModelRequest(
            system=assembly.text,
            objective=task.objective,
            messages=projection.messages,
            tools=schemas,
        )

    def _provider_content(self, message_id: str, safe_content: str) -> str:
        if self._protected_messages is None:
            return safe_content
        return self._protected_messages.get(message_id) or safe_content

    def _record_tool_result(
        self,
        task_id: str,
        result: ToolResult,
        tool_name: str,
        protected_content: bytes | None = None,
    ):
        content = _safe_tool_result_json(result, tool_name)
        message = (
            self._tasks.add_protected_tool_message(task_id, "tool", content, protected_content)
            if protected_content is not None
            else self._tasks.add_message(task_id, "tool", content)
        )
        self._tasks.append_event(
            task_id,
            "message.created",
            {"message_id": message.id, "role": message.role},
        )
        return message

    def _complete_task(self, task_id: str) -> Task:
        task = self._tasks.get(task_id)
        if task.status is TaskStatus.UNDERSTANDING:
            task = self._tasks.transition(task_id, TaskStatus.PLANNING)
        if task.status is TaskStatus.PLANNING:
            task = self._tasks.transition(task_id, TaskStatus.RUNNING)
        if task.status is TaskStatus.RUNNING:
            task = self._tasks.transition(task_id, TaskStatus.SYNTHESIZING)
        if task.status is TaskStatus.SYNTHESIZING:
            task = self._tasks.transition(task_id, TaskStatus.COMPLETED)
        self._tasks.append_event(
            task_id,
            "task.state",
            {"status": task.status.value},
        )
        return task

    def _fail_task(self, task_id: str, category: str, message: str) -> Task:
        task = self._tasks.get(task_id)
        if task.status is not TaskStatus.FAILED:
            task = self._tasks.transition(task_id, TaskStatus.FAILED)
        self._tasks.append_event(
            task_id,
            "task.failed",
            {"error_category": category, "message": message},
        )
        return task


def _safe_tool_call(call) -> dict[str, object]:
    if call.name != "query_database":
        return {"id": call.id, "name": call.name, "arguments": call.arguments}
    sql = str(call.arguments.get("sql", ""))
    source = str(call.arguments.get("data_source_id", ""))
    return {
        "id": call.id,
        "name": call.name,
        "arguments": {
            "sql_hash": sha256(sql.encode("utf-8")).hexdigest(),
            "data_source_id_hash": sha256(source.encode("utf-8")).hexdigest(),
        },
    }


def _tool_result_payload(result: ToolResult) -> dict[str, object]:
    return {
        "call_id": result.call_id,
        "success": result.success,
        "output": result.output,
        "error_category": result.error_category,
        "message": result.message,
        "retryable": result.retryable,
        "diagnostics": result.diagnostics,
    }


def _tool_result_json(result: ToolResult) -> str:
    return json.dumps(_tool_result_payload(result), ensure_ascii=False, separators=(",", ":"))


def _result_artifact_ids(result: ToolResult) -> tuple[str, ...]:
    if not result.success or not isinstance(result.output, dict):
        return ()
    values: list[str] = []
    artifact_id = result.output.get("artifact_id")
    if isinstance(artifact_id, str) and artifact_id:
        values.append(artifact_id)
    artifact_ids = result.output.get("artifact_ids")
    if isinstance(artifact_ids, list):
        values.extend(value for value in artifact_ids if isinstance(value, str) and value)
    return tuple(dict.fromkeys(values))


def _safe_tool_result_json(result: ToolResult, tool_name: str) -> str:
    payload = _tool_result_payload(result)
    output = result.output if isinstance(result.output, dict) else {}
    if tool_name == "await_teammates":
        raw_agents = output.get("agents")
        agents = raw_agents if isinstance(raw_agents, list) else []
        payload["output"] = {
            "agents": [
                {
                    key: item.get(key)
                    for key in (
                        "agent_id",
                        "role",
                        "status",
                        "result_summary",
                        "error_category",
                    )
                }
                for item in agents
                if isinstance(item, dict)
            ],
            **(
                {"wait_reason": output["wait_reason"]}
                if isinstance(output.get("wait_reason"), str)
                else {}
            ),
            **(
                {"approvals": output["approvals"]}
                if isinstance(output.get("approvals"), list)
                else {}
            ),
        }
        payload["diagnostics"] = None
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if tool_name != "query_database":
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    payload["output"] = {
        key: output.get(key)
        for key in ("status", "query_hash", "row_count", "risk", "artifact_id")
        if key in output
    }
    payload["diagnostics"] = None
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
