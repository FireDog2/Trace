from __future__ import annotations

import json
from hashlib import sha256

from data_analysis_agent.domain import Task, TaskStatus
from data_analysis_agent.providers import (
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ProviderError,
)
from data_analysis_agent.model_config import SecretProtector
from data_analysis_agent.repositories import (
    ProtectedMessageError,
    ProtectedToolMessageRepository,
    TaskRepository,
)
from data_analysis_agent.settings import Settings
from data_analysis_agent.tools import ToolContext, ToolRegistry, ToolResult


SYSTEM_PROMPT = """You are a careful data analyst operating inside a persistent
analysis project. Use tools to inspect evidence, maintain a concise plan, and
create reproducible artifacts. Never claim a business finding without a
supporting artifact. Ask one focused question when a metric definition or
required dataset is missing."""

_PROTECTED_TOOL_NAMES = frozenset({"query_database", "await_teammates"})


class AgentRunner:
    def __init__(
        self,
        *,
        settings: Settings,
        provider: ModelProvider,
        tools: ToolRegistry,
        tasks: TaskRepository,
        secret_protector: SecretProtector | None = None,
    ) -> None:
        self._settings = settings
        self._provider = provider
        self._tools = tools
        self._tasks = tasks
        self._protected_messages = (
            ProtectedToolMessageRepository(tasks._connection, secret_protector)
            if secret_protector is not None else None
        )

    def run(self, task_id: str) -> Task:
        task = self._tasks.get(task_id)
        if task.status in {
            TaskStatus.COMPLETED,
            TaskStatus.CANCELLED,
            TaskStatus.NEEDS_INPUT,
        }:
            return task

        context = ToolContext(project_id=task.project_id, task_id=task.id)
        action_count = 0
        last_signature = ""
        repeated_count = 0
        while action_count < self._settings.max_tool_actions:
            try:
                response = self._provider.complete(self._build_request(task_id))
            except (ProviderError, ProtectedMessageError) as exc:
                return self._fail_task(task_id, "provider_error", str(exc))

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
                return self._complete_task(task_id)

            raw_tool_calls = {
                "tool_calls": [
                    {"id": call.id, "name": call.name, "arguments": call.arguments}
                    for call in response.tool_calls
                ]
            }
            safe_tool_calls = json.dumps(
                {"tool_calls": [_safe_tool_call(call) for call in response.tool_calls]},
                ensure_ascii=False, separators=(",", ":"),
            )
            if any(call.name in _PROTECTED_TOOL_NAMES for call in response.tool_calls):
                if self._protected_messages is None:
                    return self._fail_task(task_id, "message_protection_error", "Protected tool message storage is unavailable")
                try:
                    encrypted = self._protected_messages.protect(json.dumps(raw_tool_calls, ensure_ascii=False, separators=(",", ":")))
                    tool_call_message = self._tasks.add_protected_tool_message(task_id, "assistant_tool_call", safe_tool_calls, encrypted)
                except ProtectedMessageError:
                    return self._fail_task(task_id, "message_protection_error", "Protected tool message storage is unavailable")
            else:
                tool_call_message = self._tasks.add_message(task_id, "assistant_tool_call", safe_tool_calls)
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

                result = self._tools.execute(call, context)
                protected_result: bytes | None = None
                if call.name in _PROTECTED_TOOL_NAMES and result.success:
                    assert self._protected_messages is not None
                    try:
                        protected_result = self._protected_messages.protect(_tool_result_json(result))
                    except ProtectedMessageError:
                        return self._fail_task(task_id, "message_protection_error", "Protected tool message storage is unavailable")
                try:
                    message = self._record_tool_result(task_id, result, call.name, protected_result)
                except ProtectedMessageError:
                    return self._fail_task(task_id, "message_protection_error", "Protected tool message storage is unavailable")
                if result.pauses_task:
                    for cancelled in response.tool_calls[call_index + 1:]:
                        self._record_tool_result(
                            task_id,
                            ToolResult(call_id=cancelled.id, success=False, output={}, error_category="cancelled_due_to_pause", message="Cancelled because an earlier tool call paused the task", retryable=False),
                            cancelled.name,
                        )
                    return self._tasks.get(task_id)
                if action_count >= self._settings.max_tool_actions:
                    break

        return self._fail_task(
            task_id,
            "tool_action_limit",
            "Tool action limit reached",
        )

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

    def resume_approval(self, task_id: str) -> Task:
        task = self._tasks.get(task_id)
        if task.status is not TaskStatus.NEEDS_INPUT:
            raise ValueError("Only a task waiting for approval can be resumed")
        self._tasks.transition(task_id, TaskStatus.UNDERSTANDING)
        self._tasks.append_event(
            task_id,
            "task.resumed",
            {"reason": "approval_granted"},
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

    def _build_request(self, task_id: str) -> ModelRequest:
        task = self._tasks.get(task_id)
        messages = tuple(
            ModelMessage(role=message.role, content=self._provider_content(message.id, message.content))
            for message in self._tasks.list_messages(task_id)
        )
        return ModelRequest(
            system=SYSTEM_PROMPT,
            objective=task.objective,
            messages=messages,
            tools=self._tools.schemas(),
        )

    def _provider_content(self, message_id: str, safe_content: str) -> str:
        if self._protected_messages is None:
            return safe_content
        return self._protected_messages.get(message_id) or safe_content

    def _record_tool_result(self, task_id: str, result: ToolResult, tool_name: str, protected_content: bytes | None = None):
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
    return {"id": call.id, "name": call.name, "arguments": {
        "sql_hash": sha256(sql.encode("utf-8")).hexdigest(),
        "data_source_id_hash": sha256(source.encode("utf-8")).hexdigest(),
    }}


def _tool_result_payload(result: ToolResult) -> dict[str, object]:
    return {"call_id": result.call_id, "success": result.success, "output": result.output,
            "error_category": result.error_category, "message": result.message,
            "retryable": result.retryable, "diagnostics": result.diagnostics}


def _tool_result_json(result: ToolResult) -> str:
    return json.dumps(_tool_result_payload(result), ensure_ascii=False, separators=(",", ":"))


def _safe_tool_result_json(result: ToolResult, tool_name: str) -> str:
    payload = _tool_result_payload(result)
    output = result.output if isinstance(result.output, dict) else {}
    if tool_name == "await_teammates":
        agents = output.get("agents") if isinstance(output.get("agents"), list) else []
        payload["output"] = {"agents": [
            {key: item.get(key) for key in ("agent_id", "role", "status", "result_summary", "error_category")}
            for item in agents if isinstance(item, dict)
        ]}
        payload["diagnostics"] = None
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if tool_name != "query_database":
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    payload["output"] = {key: output.get(key) for key in
                         ("status", "query_hash", "row_count", "risk", "artifact_id") if key in output}
    payload["diagnostics"] = None
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
