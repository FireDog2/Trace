from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from data_analysis_agent.settings import Settings
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
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ProviderError(
                "malformed_response",
                "The model service returned an invalid response",
            ) from exc


def provider_from_settings(settings: Settings) -> ModelProvider:
    if not settings.model_base_url or not settings.model_api_key or not settings.model_name:
        raise ProviderError(
            "not_configured",
            "DAA_MODEL_BASE_URL, DAA_MODEL_API_KEY, and DAA_MODEL_NAME are required",
        )
    return OpenAICompatibleProvider(
        base_url=settings.model_base_url,
        api_key=settings.model_api_key,
        model=settings.model_name,
        timeout_seconds=settings.model_timeout_seconds,
    )


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
    for message in request.messages:
        if message.role == "assistant_tool_call":
            try:
                raw_calls = json.loads(message.content)["tool_calls"]
                messages.append(
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
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
                        ],
                    }
                )
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
        elif message.role in {"user", "assistant", "system"}:
            messages.append({"role": message.role, "content": message.content})
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
