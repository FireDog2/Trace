from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest

from data_analysis_agent.model_config import ModelProviderKind, ProviderCredentials
from data_analysis_agent.providers import (
    LocalTransformersProvider,
    ModelMessage,
    ModelRequest,
    OpenAICompatibleProvider,
    ProviderError,
    _parse_local_response,
    local_provider_from_settings,
    provider_from_credentials,
)
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
                content=json.dumps({"call_id": "prior-call", "success": True, "output": {}}),
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


def test_openai_adapter_repairs_legacy_user_input_message_order() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "done"}}]},
        )

    request = ModelRequest(
        system="Use evidence.",
        objective="Inspect a field",
        messages=(
            ModelMessage(
                role="assistant_tool_call",
                content=json.dumps(
                    {
                        "tool_calls": [
                            {
                                "id": "ask-1",
                                "name": "request_user_input",
                                "arguments": {"question": "Which detail?"},
                            }
                        ]
                    }
                ),
            ),
            ModelMessage(role="assistant", content="Which detail?"),
            ModelMessage(
                role="tool",
                content=json.dumps(
                    {
                        "call_id": "ask-1",
                        "success": True,
                        "output": {"question": "Which detail?"},
                    }
                ),
            ),
            ModelMessage(role="user", content="Data type"),
        ),
        tools=(),
    )

    OpenAICompatibleProvider(
        base_url="https://model.invalid/v1",
        api_key="secret",
        model="test-model",
        transport=httpx.MockTransport(handler),
    ).complete(request)

    projected = captured["messages"]
    assert [message["role"] for message in projected[2:]] == [
        "assistant",
        "tool",
        "assistant",
        "user",
    ]
    assert projected[2]["tool_calls"][0]["id"] == "ask-1"
    assert projected[3]["tool_call_id"] == "ask-1"


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


@pytest.mark.parametrize("device,dtype", [("auto", "auto"), ("cpu", "bfloat16")])
def test_local_runtime_is_offline_cached_serial_and_preserves_trace_protocol(
    monkeypatch, settings, tmp_path, device, dtype
) -> None:
    """Exercise the actual adapter with a fake tensor runtime, without loading a model."""
    import data_analysis_agent.providers as module

    loads = []
    prompts = []
    generations = []

    class Encoded:
        shape = (1, 3)

        def to(self, target):
            assert target == "cpu"
            return self

    class Output:
        def __getitem__(self, key):
            assert key == (0, slice(3, None))
            return [9]

    class Tokenizer:
        eos_token_id = 0

        def apply_chat_template(self, messages, **kwargs):
            assert kwargs["add_generation_prompt"] is True
            assert kwargs["tokenize"] is True
            prompts.append(messages)
            return Encoded()

        def decode(self, tokens, **kwargs):
            assert tokens == [9] and kwargs["skip_special_tokens"] is True
            return '{"text":"Evidence checked","tool_calls":[]}'

    class Model:
        device = "cpu"
        running = False

        def eval(self):
            return self

        def generate(self, encoded, **kwargs):
            assert not self.running, "Shared model inference must be serialized"
            self.running = True
            assert isinstance(encoded, Encoded)
            assert kwargs == {"max_new_tokens": 128, "do_sample": False, "pad_token_id": 0}
            generations.append(kwargs)
            time.sleep(0.01)
            self.running = False
            return Output()

    def load_tokenizer(path, **kwargs):
        assert kwargs == {"local_files_only": True}
        loads.append(("tokenizer", path))
        return Tokenizer()

    def load_model(path, **kwargs):
        assert kwargs["local_files_only"] is True
        assert kwargs["device_map"] == device
        if dtype != "auto":
            assert kwargs["torch_dtype"] == "bf16"
        loads.append(("model", path))
        return Model()

    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            inference_mode=nullcontext,
            float16="fp16",
            bfloat16="bf16",
            float32="fp32",
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "transformers",
        SimpleNamespace(
            AutoTokenizer=SimpleNamespace(from_pretrained=load_tokenizer),
            AutoModelForCausalLM=SimpleNamespace(from_pretrained=load_model),
        ),
    )
    monkeypatch.setattr(module, "_LOCAL_PROVIDER_CACHE", {})
    assert local_provider_from_settings(settings) is None
    configured = replace(
        settings,
        local_model_path=tmp_path / "checkpoint",
        local_model_device=device,
        local_model_dtype=dtype,
        local_model_max_new_tokens=128,
    )
    provider = local_provider_from_settings(configured)
    assert provider is local_provider_from_settings(configured)
    assert provider is not local_provider_from_settings(
        replace(configured, local_model_path=tmp_path / "other-checkpoint")
    )
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: provider.complete(sample_model_request()), range(4)))
    assert [result.text for result in results] == ["Evidence checked"] * 4
    assert [kind for kind, _ in loads] == ["tokenizer", "model"]
    assert len(generations) == 4
    assert "Return exactly one JSON object" in prompts[0][0]["content"]
    assert "inspect_dataset" in prompts[0][0]["content"]
    assert prompts[0][-1]["role"] == "user"
    assert prompts[0][-1]["content"].startswith("[tool]")


def test_local_loading_failure_is_safe_and_does_not_reflect_checkpoint_details(monkeypatch) -> None:
    def unavailable(*args, **kwargs):
        raise RuntimeError("secret checkpoint credentials and private path")

    monkeypatch.setitem(
        sys.modules,
        "transformers",
        SimpleNamespace(
            AutoTokenizer=SimpleNamespace(from_pretrained=unavailable),
            AutoModelForCausalLM=SimpleNamespace(from_pretrained=unavailable),
        ),
    )
    with pytest.raises(ProviderError) as error:
        LocalTransformersProvider("/private/checkpoint").complete(sample_model_request())
    assert error.value.category == "local_inference"
    assert "secret" not in str(error.value) and "/private" not in str(error.value)


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


def test_local_provider_parses_final_answer_and_structured_tool_calls() -> None:
    request = sample_model_request()
    final = _parse_local_response('{"text":"Revenue fell 8%.","tool_calls":[]}', request)
    action = _parse_local_response(
        '{"text":"","tool_calls":[{"name":"inspect_dataset","arguments":{"artifact_id":"a1"}}]}',
        request,
    )

    assert final.text == "Revenue fell 8%."
    assert action.tool_calls[0].name == "inspect_dataset"
    assert action.tool_calls[0].arguments == {"artifact_id": "a1"}
    assert action.tool_calls[0].id.startswith("local-")


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        '{"text":"","tool_calls":[]}',
        '{"text":"","tool_calls":[{"name":"not_allowed","arguments":{}}]}',
        '{"text":"answer","tool_calls":[{"name":"inspect_dataset","arguments":{}}]}',
    ],
)
def test_local_provider_rejects_invalid_or_unknown_actions(raw: str) -> None:
    with pytest.raises(ProviderError) as error:
        _parse_local_response(raw, sample_model_request())

    assert error.value.category == "invalid_local_output"


def test_local_provider_is_in_process_adapter() -> None:
    provider = LocalTransformersProvider("/local/checkpoint")

    assert provider._model_path == "/local/checkpoint"


def test_openai_adapter_classifies_context_length_without_reflecting_provider_body() -> None:
    provider = OpenAICompatibleProvider(
        base_url="https://model.invalid/v1",
        api_key="secret",
        model="test-model",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                400,
                json={
                    "error": {
                        "code": "context_length_exceeded",
                        "message": "secret prompt is too long",
                    }
                },
            )
        ),
    )

    with pytest.raises(ProviderError) as error:
        provider.complete(sample_model_request())

    assert error.value.category == "context_length_exceeded"
    assert "secret" not in str(error.value)
