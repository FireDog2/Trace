from __future__ import annotations

import sys

import pytest

from data_analysis_agent.model_config import (
    ModelConfigurationError,
    ModelConfigurationRepository,
    ModelProviderKind,
    WindowsDpapiProtector,
)


class RecordingProtector:
    def protect(self, value: str) -> bytes:
        return b"protected:" + value.encode("utf-8")

    def unprotect(self, value: bytes) -> str:
        prefix = b"protected:"
        if not value.startswith(prefix):
            raise ValueError("Invalid protected value")
        return value[len(prefix) :].decode("utf-8")


def test_save_returns_redacted_configuration_and_protects_key(db_connection) -> None:
    repository = ModelConfigurationRepository(db_connection, RecordingProtector())

    status = repository.save(
        provider="deepseek",
        model="deepseek-v4-flash",
        base_url=None,
        api_key="secret-key",
    )

    row = db_connection.execute(
        "SELECT base_url, protected_api_key FROM model_configuration"
    ).fetchone()
    assert status.provider is ModelProviderKind.DEEPSEEK
    assert status.base_url == "https://api.deepseek.com"
    assert status.model == "deepseek-v4-flash"
    assert status.configured is True
    assert status.has_api_key is True
    assert not hasattr(status, "api_key")
    assert row["protected_api_key"] == b"protected:secret-key"


def test_blank_key_preserves_key_only_for_same_provider(db_connection) -> None:
    repository = ModelConfigurationRepository(db_connection, RecordingProtector())
    repository.save("deepseek", "deepseek-v4-flash", None, "deepseek-key")

    status = repository.save("deepseek", "deepseek-v4-pro", None, "")

    assert status.model == "deepseek-v4-pro"
    assert repository.credentials().api_key == "deepseek-key"


def test_switching_provider_requires_a_new_key(db_connection) -> None:
    repository = ModelConfigurationRepository(db_connection, RecordingProtector())
    repository.save("deepseek", "deepseek-v4-flash", None, "deepseek-key")

    with pytest.raises(ModelConfigurationError, match="new API key"):
        repository.save("openai", "gpt-5.6-terra", None, None)


@pytest.mark.parametrize(
    ("base_url", "message"),
    [
        ("file:///tmp/model", "http or https"),
        ("relay.example/v1", "http or https"),
        ("", "Base URL"),
    ],
)
def test_custom_provider_rejects_invalid_url(
    db_connection,
    base_url: str,
    message: str,
) -> None:
    repository = ModelConfigurationRepository(db_connection, RecordingProtector())

    with pytest.raises(ModelConfigurationError, match=message):
        repository.save("custom", "relay-model", base_url, "relay-key")


def test_custom_provider_preserves_user_endpoint(db_connection) -> None:
    repository = ModelConfigurationRepository(db_connection, RecordingProtector())

    repository.save(
        "custom",
        "relay-model",
        "https://relay.example/v1/",
        "relay-key",
    )
    credentials = repository.credentials()

    assert credentials.provider is ModelProviderKind.CUSTOM
    assert credentials.base_url == "https://relay.example/v1"
    assert credentials.model == "relay-model"
    assert credentials.api_key == "relay-key"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI only")
def test_windows_dpapi_round_trip_does_not_return_plaintext() -> None:
    protector = WindowsDpapiProtector()

    protected = protector.protect("local-secret")

    assert protected != b"local-secret"
    assert protector.unprotect(protected) == "local-secret"
