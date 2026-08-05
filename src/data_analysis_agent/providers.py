from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Protocol

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
    """Provider request or response failure."""


class ScriptedProvider:
    """Deterministic provider for unit and acceptance tests."""

    def __init__(self, responses: list[ModelResponse] | tuple[ModelResponse, ...]) -> None:
        self._responses = deque(responses)
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self._responses:
            raise ProviderError("Scripted provider has no remaining response")
        return self._responses.popleft()
