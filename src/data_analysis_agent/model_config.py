from __future__ import annotations

import ctypes
import sqlite3
import sys
from ctypes import wintypes
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from urllib.parse import urlsplit

from data_analysis_agent.db import (
    sqlite_connection_guarded,
    sqlite_connection_lock,
    utc_now,
)


class ModelProviderKind(StrEnum):
    DEEPSEEK = "deepseek"
    OPENAI = "openai"
    CUSTOM = "custom"


@dataclass(frozen=True, slots=True)
class ProviderDefinition:
    kind: ModelProviderKind
    label: str
    base_url: str | None
    models: tuple[str, ...]
    default_model: str
    custom_url: bool = False


MODEL_PROVIDER_CATALOG = (
    ProviderDefinition(
        kind=ModelProviderKind.DEEPSEEK,
        label="DeepSeek",
        base_url="https://api.deepseek.com",
        models=("deepseek-v4-flash", "deepseek-v4-pro"),
        default_model="deepseek-v4-flash",
    ),
    ProviderDefinition(
        kind=ModelProviderKind.OPENAI,
        label="OpenAI",
        base_url="https://api.openai.com/v1",
        models=("gpt-5.6-terra", "gpt-5.6-sol", "gpt-5.6-luna"),
        default_model="gpt-5.6-terra",
    ),
    ProviderDefinition(
        kind=ModelProviderKind.CUSTOM,
        label="第三方中转站",
        base_url=None,
        models=(),
        default_model="",
        custom_url=True,
    ),
)


@dataclass(frozen=True, slots=True)
class ModelConfigurationStatus:
    provider: ModelProviderKind | None
    base_url: str
    model: str
    configured: bool
    has_api_key: bool
    updated_at: str | None


@dataclass(frozen=True, slots=True)
class ProviderCredentials:
    provider: ModelProviderKind
    base_url: str
    model: str
    api_key: str


class SecretProtector(Protocol):
    def protect(self, value: str) -> bytes: ...

    def unprotect(self, value: bytes) -> str: ...


class ModelConfigurationError(RuntimeError):
    """Invalid or incomplete model configuration."""


class SecretStorageUnavailable(ModelConfigurationError):
    """The supported OS secret-protection service is unavailable."""


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


class WindowsDpapiProtector:
    # The local service can be started either by the desktop app or from the
    # user's terminal. Those launch surfaces may use different Windows
    # identities, so bind secrets to this machine while relying on the data
    # directory ACL to restrict who can read the encrypted blobs.
    _protect_flags = 0x1 | 0x4  # UI_FORBIDDEN | LOCAL_MACHINE
    _unprotect_flags = 0x1  # CRYPTPROTECT_UI_FORBIDDEN

    def protect(self, value: str) -> bytes:
        if sys.platform != "win32":
            raise SecretStorageUnavailable("Secure model-key storage requires Windows DPAPI")
        return self._transform(value.encode("utf-8"), decrypt=False)

    def unprotect(self, value: bytes) -> str:
        if sys.platform != "win32":
            raise SecretStorageUnavailable("Secure model-key storage requires Windows DPAPI")
        return self._transform(value, decrypt=True).decode("utf-8")

    def _transform(self, value: bytes, *, decrypt: bool) -> bytes:
        buffer = ctypes.create_string_buffer(value)
        input_blob = _DataBlob(
            len(value),
            ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
        )
        output_blob = _DataBlob()
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        if decrypt:
            succeeded = crypt32.CryptUnprotectData(
                ctypes.byref(input_blob),
                None,
                None,
                None,
                None,
                self._unprotect_flags,
                ctypes.byref(output_blob),
            )
        else:
            succeeded = crypt32.CryptProtectData(
                ctypes.byref(input_blob),
                "Data Analysis Agent model key",
                None,
                None,
                None,
                self._protect_flags,
                ctypes.byref(output_blob),
            )
        if not succeeded:
            raise SecretStorageUnavailable(
                f"Windows DPAPI failed with error {ctypes.get_last_error()}"
            )
        try:
            return ctypes.string_at(output_blob.pbData, output_blob.cbData)
        finally:
            kernel32.LocalFree(output_blob.pbData)


class ModelConfigurationRepository:
    def __init__(
        self,
        connection: sqlite3.Connection,
        secret_protector: SecretProtector,
    ) -> None:
        connection.row_factory = sqlite3.Row
        self._connection = connection
        self._lock = sqlite_connection_lock(connection)
        self._secret_protector = secret_protector

    @sqlite_connection_guarded
    def get_public(self) -> ModelConfigurationStatus:
        row = self._get_row()
        if row is None:
            return ModelConfigurationStatus(
                provider=None,
                base_url="",
                model="",
                configured=False,
                has_api_key=False,
                updated_at=None,
            )
        return ModelConfigurationStatus(
            provider=ModelProviderKind(row["provider"]),
            base_url=row["base_url"],
            model=row["model"],
            configured=True,
            has_api_key=bool(row["protected_api_key"]),
            updated_at=row["updated_at"],
        )

    @sqlite_connection_guarded
    def save(
        self,
        provider: str | ModelProviderKind,
        model: str,
        base_url: str | None,
        api_key: str | None,
    ) -> ModelConfigurationStatus:
        kind = _provider_kind(provider)
        definition = _provider_definition(kind)
        normalized_model = model.strip()
        if not normalized_model:
            raise ModelConfigurationError("Model name is required")
        if definition.custom_url:
            normalized_base_url = _custom_base_url(base_url)
        else:
            if normalized_model not in definition.models:
                raise ModelConfigurationError(
                    f"Unsupported {definition.label} model: {normalized_model}"
                )
            assert definition.base_url is not None
            requested_base_url = (base_url or "").strip().rstrip("/")
            if requested_base_url and requested_base_url != definition.base_url:
                raise ModelConfigurationError(f"{definition.label} must use its official Base URL")
            normalized_base_url = definition.base_url

        existing = self._get_row()
        normalized_key = (api_key or "").strip()
        if normalized_key:
            protected_key = self._secret_protector.protect(normalized_key)
        elif existing is not None and existing["provider"] == kind.value:
            protected_key = bytes(existing["protected_api_key"])
        else:
            raise ModelConfigurationError("A new API key is required for this provider")

        updated_at = utc_now()
        self._connection.execute(
            "INSERT INTO model_configuration("
            "singleton_id, provider, base_url, model, protected_api_key, updated_at"
            ") VALUES (1, ?, ?, ?, ?, ?) "
            "ON CONFLICT(singleton_id) DO UPDATE SET "
            "provider = excluded.provider, base_url = excluded.base_url, "
            "model = excluded.model, protected_api_key = excluded.protected_api_key, "
            "updated_at = excluded.updated_at",
            (
                kind.value,
                normalized_base_url,
                normalized_model,
                sqlite3.Binary(protected_key),
                updated_at,
            ),
        )
        self._connection.commit()
        return self.get_public()

    @sqlite_connection_guarded
    def credentials(self) -> ProviderCredentials:
        row = self._get_row()
        if row is None:
            raise ModelConfigurationError("No model is configured")
        api_key = self._secret_protector.unprotect(bytes(row["protected_api_key"]))
        if not api_key:
            raise ModelConfigurationError("The saved model API key is empty")
        return ProviderCredentials(
            provider=ModelProviderKind(row["provider"]),
            base_url=row["base_url"],
            model=row["model"],
            api_key=api_key,
        )

    def _get_row(self) -> sqlite3.Row | None:
        return self._connection.execute(
            "SELECT provider, base_url, model, protected_api_key, updated_at "
            "FROM model_configuration WHERE singleton_id = 1"
        ).fetchone()


def _provider_kind(value: str | ModelProviderKind) -> ModelProviderKind:
    try:
        return ModelProviderKind(value)
    except ValueError as exc:
        raise ModelConfigurationError(f"Unsupported model provider: {value}") from exc


def _provider_definition(kind: ModelProviderKind) -> ProviderDefinition:
    return next(item for item in MODEL_PROVIDER_CATALOG if item.kind is kind)


def _custom_base_url(value: str | None) -> str:
    normalized = (value or "").strip().rstrip("/")
    if not normalized:
        raise ModelConfigurationError("Base URL is required for a third-party relay")
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ModelConfigurationError("Base URL must use http or https")
    if parsed.username or parsed.password:
        raise ModelConfigurationError("Base URL must not include credentials")
    if parsed.query:
        raise ModelConfigurationError("Base URL must not include a query string")
    if parsed.fragment:
        raise ModelConfigurationError("Base URL must not include a fragment")
    return normalized
