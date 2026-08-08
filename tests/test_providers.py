from __future__ import annotations

import json

import httpx
import pytest

from data_analysis_agent.providers import (
    ModelMessage,
    ModelRequest,
    OpenAICompatibleProvider,
    ProviderError,
    provider_from_credentials,
)
from data_analysis_agent.model_config import ModelProviderKind, ProviderCredentials
from data_analysis_agent.tools import ToolSchema


def sample_model_request() -> ModelRequest:
    return ModelRequest(
        system="Use evidence.",
        objective="Diagnose profit decline",
        messages=(
            ModelMessage(
                role="assistant_tool_call",
                content=json.dumps(
                    {
                        "tool_calls": [
                            {
                                "id": "prior-call",
                                "name": "inspect_workspace",
                                "arguments": {},
                            }
                        ]
                    }
                ),
            ),
            ModelMessage(
                role="tool",
                content=json.dumps(
                    {"call_id": "prior-call", "success": True, "output": {}}
                ),
            ),
        ),
        tools=(
            ToolSchema(
                name="inspect_dataset",
                description="Inspect a dataset",
                input_schema={
                    "type": "object",
                    "properties": {"artifact_id": {"type": "string"}},
                    "required": ["artifact_id"],
                },
            ),
        ),
    )


def test_openai_adapter_normalizes_tool_call_and_preserves_history() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer secret"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "inspect_dataset",
                                        "arguments": json.dumps({"artifact_id": "a1"}),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    response = OpenAICompatibleProvider(
        base_url="https://model.invalid/v1",
        api_key="secret",
        model="test-model",
        transport=httpx.MockTransport(handler),
    ).complete(sample_model_request())

    assert response.tool_calls[0].id == "call_1"
    assert response.tool_calls[0].name == "inspect_dataset"
    assert response.tool_calls[0].arguments == {"artifact_id": "a1"}
    assert captured["messages"][2]["tool_calls"][0]["id"] == "prior-call"
    assert captured["messages"][3]["tool_call_id"] == "prior-call"
    assert captured["tools"][0]["function"]["name"] == "inspect_dataset"


def test_openai_adapter_classifies_authentication_failure() -> None:
    provider = OpenAICompatibleProvider(
        base_url="https://model.invalid/v1",
        api_key="secret",
        model="test-model",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(401, json={"error": {"message": "bad key"}})
        ),
    )

    with pytest.raises(ProviderError) as error:
        provider.complete(sample_model_request())

    assert error.value.category == "authentication"
    assert "secret" not in str(error.value)


def test_provider_from_credentials_builds_openai_compatible_provider() -> None:
    provider = provider_from_credentials(
        ProviderCredentials(
            provider=ModelProviderKind.CUSTOM,
            base_url="https://relay.example/v1",
            model="relay-model",
            api_key="relay-key",
        )
    )

    assert isinstance(provider, OpenAICompatibleProvider)


def test_openai_adapter_classifies_context_length_without_reflecting_provider_body() -> None:
    provider = OpenAICompatibleProvider(
        base_url="https://model.invalid/v1",
        api_key="secret",
        model="test-model",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                400,
                json={"error": {"code": "context_length_exceeded", "message": "secret prompt is too long"}},
            )
        ),
    )

    with pytest.raises(ProviderError) as error:
        provider.complete(sample_model_request())

    assert error.value.category == "context_length_exceeded"
    assert "secret" not in str(error.value)
