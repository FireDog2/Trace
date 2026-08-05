# Frontend Model Configuration Design

## Goal

Replace startup environment-variable model configuration with a local settings experience in the web workbench. A user can choose DeepSeek, OpenAI, or a third-party relay, select or enter a model, save an API key, and use the new configuration for subsequent analysis tasks without restarting the application.

## Scope

The feature includes one active application-wide model profile, a provider catalog, local persistence, protected API-key storage on Windows, runtime provider resolution, and a settings panel in the existing workbench.

It does not include account synchronization, multiple simultaneous active profiles, remote secret vaults, automatic billing checks, model discovery from provider APIs, or per-project model overrides.

## Provider Catalog

The backend is the source of truth for built-in provider metadata.

| Provider | Base URL | Model choices | Editable fields |
| --- | --- | --- | --- |
| DeepSeek | `https://api.deepseek.com` | `deepseek-v4-flash` (default), `deepseek-v4-pro` | model, API key |
| OpenAI | `https://api.openai.com/v1` | `gpt-5.6-terra` (default), `gpt-5.6-sol`, `gpt-5.6-luna` | model, API key |
| Third-party relay | user supplied | user supplied | base URL, model, API key |

All three use the existing OpenAI-compatible Chat Completions and function-calling adapter. DeepSeek legacy aliases are excluded because the official documentation marks them retired after 2026-07-24. Third-party endpoints must use `http` or `https`; built-in provider URLs cannot be overridden.

Official references:

- OpenAI model catalog: `https://developers.openai.com/api/docs/models/all`
- OpenAI Chat Completions support: `https://developers.openai.com/api/docs/models/compare`
- DeepSeek quick start and current models: `https://api-docs.deepseek.com/zh-cn/guides/reasoning_model`
- DeepSeek tool calls: `https://api-docs.deepseek.com/guides/tool_calls`

## Persistence and Secret Handling

SQLite migration 2 creates a singleton `model_configuration` table containing provider, base URL, model, protected API-key bytes, and update time. The API key is write-only:

- GET responses expose only `has_api_key`.
- PUT accepts a new API key but never echoes it.
- An omitted or blank key preserves the existing key only when the provider is unchanged.
- Changing provider requires a new API key so one provider's credential cannot be sent to another.
- Logs, task events, errors, and model history never contain the key.

On Windows, the key is encrypted and decrypted with DPAPI for the current OS user. Secret protection is an injected boundary so repository tests use a deterministic in-memory protector. Saving a key on an unsupported platform returns a clear configuration error rather than silently storing plaintext.

## Backend Components

### `model_config.py`

Owns:

- `ModelProviderKind` and catalog records;
- `ModelConfiguration` and public status records;
- provider-specific validation and URL normalization;
- `SecretProtector` protocol and Windows DPAPI implementation;
- `ModelConfigurationRepository.get()` and `save()`.

The repository is the only component that handles protected key material. Public response models are built from redacted configuration records.

### Runtime provider resolution

`AppServices.agent()` resolves the active configuration when a task begins and creates one `OpenAICompatibleProvider` for that run. A task already running keeps its provider instance; the next new task or follow-up uses the latest saved configuration.

The optional provider injection used by deterministic tests remains supported and takes precedence over persisted configuration. The CLI no longer reads `DAA_MODEL_BASE_URL`, `DAA_MODEL_API_KEY`, or `DAA_MODEL_NAME`.

### HTTP API

`GET /api/model-config` returns:

- active provider, model, and base URL;
- `configured` and `has_api_key` flags;
- the redacted built-in provider catalog.

`PUT /api/model-config` accepts provider, model, optional base URL, and optional API key. It validates and persists the profile and returns the redacted status.

Creating or continuing a task without a complete model configuration returns HTTP 409 with the category `model_not_configured`; it does not create a task that can never run.

## Frontend Experience

The left project rail gains a model-status control below the product identity. It displays the active provider and model, or `配置模型` when empty. Selecting it opens a compact settings sheet over the workbench.

The sheet follows the existing analytical visual language:

- three provider rows act like a routing switch, not decorative cards;
- the selected route is connected visually to the model and credential fields by a thin evidence-style rule;
- built-in URLs are shown as locked infrastructure labels;
- the third-party route reveals editable Base URL and model fields;
- a blank key field reads `留空以保留已保存密钥` only when preservation is valid;
- saving changes updates the rail immediately and closes the sheet;
- validation errors stay inside the sheet with a concrete recovery message.

The memorable visual element is a small `MODEL ROUTE` line in the project rail, making the selected inference route feel like part of the analysis system rather than a generic settings icon.

## Data Flow

1. The UI loads model status with the initial application data.
2. The user opens the settings sheet and selects a provider.
3. Provider selection fills locked defaults or reveals third-party fields.
4. The UI sends the API key only in the PUT request.
5. The backend validates fields, protects the key, and commits the singleton record.
6. The API returns redacted status; the UI updates the model-route label.
7. A new task resolves that saved configuration and starts its background agent run.

## Error Handling

- Missing provider, model, third-party URL, or required new API key: HTTP 422 with field-level detail.
- Unsupported URL scheme or built-in URL override: HTTP 422.
- Secure secret storage unavailable: HTTP 503 with a direct explanation.
- Saved configuration missing or unreadable when a task starts: HTTP 409; no task is created.
- Provider authentication, timeout, rate limit, and upstream failures continue through the existing categorized task-failure path.

## Testing

Backend tests cover migration 2, provider catalog defaults, redacted read responses, API-key preservation, provider-change key requirements, DPAPI-boundary injection, third-party validation, dynamic task provider resolution, and the no-configuration HTTP 409 behavior.

Frontend tests cover the unconfigured rail state, opening the settings sheet, switching all three providers, locked versus editable URL behavior, key masking semantics, saving configuration, and rendering the updated active model.

The existing provider adapter, deterministic agent, acceptance, API, and production-build suites remain green without real provider credentials.

## Self-Review

- No placeholders or deferred decisions remain.
- One active global profile is explicit and consistent across API, persistence, and UI.
- API keys are write-only and never stored as plaintext on the supported Windows platform.
- Built-in and third-party provider behaviors are distinct and unambiguous.
- The feature does not introduce provider-specific objects into the agent or tool layers.
