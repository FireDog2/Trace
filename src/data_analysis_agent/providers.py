from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from data_analysis_agent.model_config import ProviderCredentials
from data_analysis_agent.tools import ToolCall, ToolSchema


@dataclass(frozen=True, slots=True)
class ModelMessage:
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class ModelRequest:
    system: str
    objective: str
    messages: tuple[ModelMessage, ...]
    tools: tuple[ToolSchema, ...]


@dataclass(frozen=True, slots=True)
class ModelResponse:
    text: str
    tool_calls: tuple[ToolCall, ...]


class ModelProvider(Protocol):
    def complete(self, request: ModelRequest) -> ModelResponse: ...


class ProviderError(RuntimeError):
    """Provider request or response failure with a safe category."""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


class ScriptedProvider:
    """Deterministic provider for unit and acceptance tests."""

    def __init__(self, responses: list[ModelResponse] | tuple[ModelResponse, ...]) -> None:
        self._responses = deque(responses)
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self._responses:
            raise ProviderError(
                "script_exhausted",
                "Scripted provider has no remaining response",
            )
        return self._responses.popleft()


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: int = 60,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    def complete(self, request: ModelRequest) -> ModelResponse:
        payload = {
            "model": self._model,
            "messages": _openai_messages(request),
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
                for tool in request.tools
            ],
        }
        try:
            with httpx.Client(
                timeout=self._timeout_seconds,
                transport=self._transport,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
            ) as client:
                response = client.post(
                    f"{self._base_url}/chat/completions",
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise ProviderError("timeout", "The model request timed out") from exc
        except httpx.RequestError as exc:
            raise ProviderError("network", "The model service could not be reached") from exc

        if response.status_code in {401, 403}:
            raise ProviderError(
                "authentication",
                "The model service rejected the configured credentials",
            )
        if response.status_code == 429:
            raise ProviderError("rate_limit", "The model service rate limit was reached")
        if response.status_code >= 500:
            raise ProviderError("upstream", "The model service returned a server error")
        if response.status_code >= 400:
            if _is_context_length_error(response):
                raise ProviderError(
                    "context_length_exceeded",
                    "The model context exceeded the provider limit",
                )
            raise ProviderError(
                "request_rejected",
                f"The model service rejected the request ({response.status_code})",
            )

        try:
            body = response.json()
            message = body["choices"][0]["message"]
            calls = tuple(_normalize_tool_call(item) for item in message.get("tool_calls", ()))
            content = message.get("content") or ""
            if not isinstance(content, str):
                raise TypeError("Assistant content is not text")
            return ModelResponse(text=content, tool_calls=calls)
        except (
            KeyError,
            IndexError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            raise ProviderError(
                "malformed_response",
                "The model service returned an invalid response",
            ) from exc


def provider_from_credentials(
    credentials: ProviderCredentials,
    timeout_seconds: int = 60,
) -> ModelProvider:
    return OpenAICompatibleProvider(
        base_url=credentials.base_url,
        api_key=credentials.api_key,
        model=credentials.model,
        timeout_seconds=timeout_seconds,
    )


def _openai_messages(request: ModelRequest) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": request.system},
        {"role": "user", "content": request.objective},
    ]
    pending_tool_call_ids: set[str] = set()
    deferred_assistant_messages: list[dict[str, Any]] = []
    for message in request.messages:
        if message.role == "assistant_tool_call":
            try:
                raw_calls = json.loads(message.content)["tool_calls"]
                normalized_calls = [
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {
                            "name": call["name"],
                            "arguments": json.dumps(
                                call["arguments"],
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        },
                    }
                    for call in raw_calls
                ]
                messages.append(
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": normalized_calls,
                    }
                )
                pending_tool_call_ids.update(str(call["id"]) for call in normalized_calls)
            except (KeyError, TypeError, json.JSONDecodeError) as exc:
                raise ProviderError(
                    "invalid_history",
                    "Persisted tool-call history is invalid",
                ) from exc
        elif message.role == "tool":
            try:
                tool_result = json.loads(message.content)
                call_id = tool_result["call_id"]
            except (KeyError, TypeError, json.JSONDecodeError) as exc:
                raise ProviderError(
                    "invalid_history",
                    "Persisted tool-result history is invalid",
                ) from exc
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": message.content,
                }
            )
            pending_tool_call_ids.discard(str(call_id))
            if not pending_tool_call_ids and deferred_assistant_messages:
                messages.extend(deferred_assistant_messages)
                deferred_assistant_messages.clear()
        elif message.role == "assistant" and pending_tool_call_ids:
            # Older runs persisted the visible request-user-input question before
            # its tool result. Keep the immutable audit history intact while
            # projecting a protocol-valid order for OpenAI-compatible providers.
            deferred_assistant_messages.append(
                {"role": message.role, "content": message.content}
            )
        elif message.role in {"user", "assistant", "system"}:
            messages.append({"role": message.role, "content": message.content})
    messages.extend(deferred_assistant_messages)
    return messages


def _normalize_tool_call(raw_call: dict[str, Any]) -> ToolCall:
    function = raw_call["function"]
    arguments = json.loads(function["arguments"])
    if not isinstance(arguments, dict):
        raise TypeError("Tool arguments must be an object")
    return ToolCall(
        id=str(raw_call["id"]),
        name=str(function["name"]),
        arguments=arguments,
    )


def _is_context_length_error(response: httpx.Response) -> bool:
    if response.status_code not in {400, 413, 422}:
        return False
    try:
        body = response.json()
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    error = body.get("error", body) if isinstance(body, dict) else {}
    if not isinstance(error, dict):
        return False
    code = str(error.get("code", "")).lower()
    kind = str(error.get("type", "")).lower()
    message = str(error.get("message", "")).lower()
    markers = (
        "context_length_exceeded",
        "context window",
        "maximum context length",
        "prompt is too long",
        "too many tokens",
        "input token limit",
    )
    return any(marker in value for value in (code, kind, message) for marker in markers)
