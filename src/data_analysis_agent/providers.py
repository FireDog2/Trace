from __future__ import annotations

import json
import threading
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path
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


class LocalTransformersProvider:
    """In-process Hugging Face model adapter using Trace's JSON action protocol."""

    def __init__(
        self,
        model_path: str | Path,
        *,
        device: str = "auto",
        dtype: str = "auto",
        max_new_tokens: int = 2_048,
    ) -> None:
        self._model_path = str(model_path)
        self._device = device
        self._dtype = dtype
        self._max_new_tokens = max_new_tokens
        self._lock = threading.Lock()
        self._tokenizer: Any = None
        self._model: Any = None

    def complete(self, request: ModelRequest) -> ModelResponse:
        try:
            with self._lock:
                self._load()
                prompt = _local_prompt(request)
                encoded = self._tokenizer.apply_chat_template(
                    prompt,
                    tokenize=True,
                    add_generation_prompt=True,
                    return_tensors="pt",
                )
                encoded = encoded.to(self._model.device)
                import torch

                with torch.inference_mode():
                    output = self._model.generate(
                        encoded,
                        max_new_tokens=self._max_new_tokens,
                        do_sample=False,
                        pad_token_id=self._tokenizer.eos_token_id,
                    )
                generated = output[0, encoded.shape[-1] :]
                raw = self._tokenizer.decode(generated, skip_special_tokens=True).strip()
            return _parse_local_response(raw, request)
        except ProviderError:
            raise
        except ImportError as exc:
            raise ProviderError(
                "runtime_unavailable",
                "Install the local-model optional dependencies to use this checkpoint",
            ) from exc
        except Exception as exc:
            raise ProviderError("local_inference", "Local model inference failed") from exc

    def _load(self) -> None:
        if self._model is not None:
            return
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self._tokenizer = AutoTokenizer.from_pretrained(self._model_path, local_files_only=True)
        load_options: dict[str, Any] = {"local_files_only": True}
        if self._device != "auto":
            load_options["device_map"] = self._device
        else:
            load_options["device_map"] = "auto"
        if self._dtype != "auto":
            import torch

            dtype = {
                "float16": torch.float16,
                "bfloat16": torch.bfloat16,
                "float32": torch.float32,
            }.get(self._dtype)
            if dtype is None:
                raise ValueError("Unsupported local model dtype")
            load_options["torch_dtype"] = dtype
        self._model = AutoModelForCausalLM.from_pretrained(self._model_path, **load_options)
        self._model.eval()


_LOCAL_PROVIDER_CACHE: dict[tuple[str, str, str, int], LocalTransformersProvider] = {}
_LOCAL_PROVIDER_CACHE_LOCK = threading.Lock()


def local_provider_from_settings(settings: Any) -> ModelProvider | None:
    model_path = getattr(settings, "local_model_path", None)
    if model_path is None:
        return None
    key = (
        str(model_path),
        settings.local_model_device,
        settings.local_model_dtype,
        settings.local_model_max_new_tokens,
    )
    with _LOCAL_PROVIDER_CACHE_LOCK:
        provider = _LOCAL_PROVIDER_CACHE.get(key)
        if provider is None:
            provider = LocalTransformersProvider(
                key[0],
                device=key[1],
                dtype=key[2],
                max_new_tokens=key[3],
            )
            _LOCAL_PROVIDER_CACHE[key] = provider
        return provider


def _local_prompt(request: ModelRequest) -> list[dict[str, str]]:
    tool_descriptions = [
        {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        }
        for tool in request.tools
    ]
    protocol = (
        "Return exactly one JSON object with keys text and tool_calls. "
        'Use {"text":"final answer","tool_calls":[]} for a final answer, or '
        "set text to an empty string and tool_calls to objects with name and arguments. "
        "Only call listed tools. Never include markdown fences or executable code.\n"
        f"Allowed tools: {json.dumps(tool_descriptions, ensure_ascii=False)}"
    )
    history = [{"role": "system", "content": request.system + "\n\n" + protocol}]
    history.append({"role": "user", "content": request.objective})
    for message in request.messages:
        if message.role in {"user", "assistant", "system"}:
            history.append({"role": message.role, "content": message.content})
        else:
            history.append({"role": "user", "content": f"[{message.role}] {message.content}"})
    return history


def _parse_local_response(raw: str, request: ModelRequest) -> ModelResponse:
    try:
        body = json.loads(raw)
        if not isinstance(body, dict) or not isinstance(body.get("text"), str):
            raise TypeError("Expected text field")
        raw_calls = body.get("tool_calls", [])
        if not isinstance(raw_calls, list):
            raise TypeError("Expected tool_calls array")
        allowed = {tool.name for tool in request.tools}
        calls: list[ToolCall] = []
        for item in raw_calls:
            if not isinstance(item, dict):
                raise TypeError("Tool call must be an object")
            name = item.get("name")
            arguments = item.get("arguments")
            if name not in allowed:
                raise ValueError("Unknown tool")
            if not isinstance(arguments, dict):
                raise TypeError("Tool arguments must be an object")
            calls.append(
                ToolCall(
                    id=f"local-{uuid.uuid4().hex}",
                    name=name,
                    arguments=arguments,
                )
            )
        if calls and body["text"].strip():
            raise ValueError("A response cannot contain both final text and tool calls")
        if not calls and not body["text"].strip():
            raise ValueError("Response must contain text or tool calls")
        return ModelResponse(text=body["text"], tool_calls=tuple(calls))
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ProviderError(
            "invalid_local_output", "Local model returned an invalid action"
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
            deferred_assistant_messages.append({"role": message.role, "content": message.content})
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
