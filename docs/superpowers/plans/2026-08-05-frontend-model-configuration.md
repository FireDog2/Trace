# Frontend Model Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a local user configure DeepSeek, OpenAI, or a third-party OpenAI-compatible relay from the workbench and use the saved model for new analysis runs without startup environment variables.

**Architecture:** SQLite stores one active redacted model profile while an injected secret-protection boundary encrypts the API key with Windows DPAPI. API jobs resolve a provider from the latest saved configuration at run start, and a React settings sheet edits the profile without exposing saved key material.

**Tech Stack:** Python 3.12, SQLite migrations, `ctypes` Windows DPAPI, FastAPI/Pydantic, existing `httpx` OpenAI-compatible adapter, React 19, TypeScript, Vitest, pytest.

## Global Constraints

- Built-in provider URLs are fixed: DeepSeek `https://api.deepseek.com`, OpenAI `https://api.openai.com/v1`.
- DeepSeek choices are `deepseek-v4-flash` and `deepseek-v4-pro`; default is `deepseek-v4-flash`.
- OpenAI choices are `gpt-5.6-terra`, `gpt-5.6-sol`, and `gpt-5.6-luna`; default is `gpt-5.6-terra`.
- Third-party relay configuration requires an `http` or `https` Base URL and a non-empty model name.
- API keys are write-only, never logged or returned, and are not stored as plaintext on Windows.
- Changing provider requires a new API key; an omitted key preserves the current key only for the same provider.
- Existing deterministic tests continue to run without any real model credential or network request.
- Existing untracked `main.py` and `uv.lock` are user-owned and must remain untouched.

---

## File Map

- `src/data_analysis_agent/db.py`: migration 2 for the singleton model configuration.
- `src/data_analysis_agent/model_config.py`: provider catalog, validation, DPAPI secret boundary, and configuration repository.
- `src/data_analysis_agent/providers.py`: provider construction from validated credentials rather than environment settings.
- `src/data_analysis_agent/api.py`: redacted GET/PUT endpoints and runtime provider resolution.
- `src/data_analysis_agent/cli.py`: start the app without reading model environment variables.
- `tests/test_model_config.py`: persistence, validation, redaction, and secret behavior.
- `tests/test_api.py`: HTTP configuration and configured/unconfigured task behavior.
- `web/src/types.ts`: model configuration contracts.
- `web/src/api.ts`: GET/PUT model configuration client methods.
- `web/src/components/ModelSettings.tsx`: provider/model/key settings sheet.
- `web/src/components/ProjectNav.tsx`: active model-route control.
- `web/src/App.tsx`: configuration state and save orchestration.
- `web/src/App.test.tsx`: model settings workflow tests.
- `web/src/styles.css`: route control and settings sheet styling.
- `README.md`: replace environment-variable setup with workbench setup.

---

### Task 1: Persist and Protect Model Configuration

**Files:**
- Modify: `src/data_analysis_agent/db.py`
- Create: `src/data_analysis_agent/model_config.py`
- Modify: `tests/test_db.py`
- Create: `tests/test_model_config.py`

**Interfaces:**
- Produces: `ModelProviderKind(StrEnum)` values `deepseek`, `openai`, `custom`.
- Produces: `ProviderDefinition(kind, label, base_url, models, default_model, custom_url)`.
- Produces: `MODEL_PROVIDER_CATALOG: tuple[ProviderDefinition, ...]`.
- Produces: `SecretProtector.protect(value: str) -> bytes` and `unprotect(value: bytes) -> str`.
- Produces: `WindowsDpapiProtector` and test-only injected protectors.
- Produces: `ModelConfigurationRepository.get_public() -> ModelConfigurationStatus`.
- Produces: `ModelConfigurationRepository.save(provider, model, base_url, api_key) -> ModelConfigurationStatus`.
- Produces: `ModelConfigurationRepository.credentials() -> ProviderCredentials`.

- [ ] **Step 1: Write migration 2 and repository behavior tests**

Add to `tests/test_db.py`:

```python
def test_migrate_creates_model_configuration_version_two(db_connection):
    columns = {
        row[1]
        for row in db_connection.execute("PRAGMA table_info(model_configuration)")
    }
    assert columns == {
        "singleton_id", "provider", "base_url", "model",
        "protected_api_key", "updated_at",
    }
    assert db_connection.execute(
        "SELECT version FROM schema_migrations ORDER BY version"
    ).fetchall() == [(1,), (2,)]
```

Add to `tests/test_model_config.py` using a `RecordingProtector` that prefixes protected bytes with `b"protected:"`:

```python
def test_save_returns_redacted_configuration(db_connection):
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
    assert status.has_api_key is True
    assert not hasattr(status, "api_key")
    assert row["base_url"] == "https://api.deepseek.com"
    assert row["protected_api_key"] == b"protected:secret-key"


def test_switching_provider_requires_a_new_key(db_connection):
    repository = ModelConfigurationRepository(db_connection, RecordingProtector())
    repository.save("deepseek", "deepseek-v4-flash", None, "deepseek-key")
    with pytest.raises(ModelConfigurationError, match="new API key"):
        repository.save("openai", "gpt-5.6-terra", None, None)
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python -m pytest tests/test_db.py tests/test_model_config.py -v`

Expected: FAIL because migration 2 and `data_analysis_agent.model_config` do not exist.

- [ ] **Step 3: Implement migration 2 and model configuration boundary**

Add `MIGRATION_2_SQL` with exactly one row enforced by `singleton_id INTEGER PRIMARY KEY CHECK(singleton_id = 1)`. Store `protected_api_key` as a BLOB. Apply migrations in numeric order.

In `model_config.py`, normalize built-in URLs from the catalog, validate provider/model combinations, permit free-form custom models, and parse custom URLs with `urllib.parse.urlsplit`. `WindowsDpapiProtector` uses `CryptProtectData` and `CryptUnprotectData` via `ctypes` and calls `LocalFree` for returned buffers. Raise `SecretStorageUnavailable` before storing a key when the platform is not Windows.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `python -m pytest tests/test_db.py tests/test_model_config.py -v`

Expected: PASS with both migration versions and no plaintext key in SQLite.

- [ ] **Step 5: Commit the persistence slice**

```powershell
git add src/data_analysis_agent/db.py src/data_analysis_agent/model_config.py tests/test_db.py tests/test_model_config.py
git commit -m "feat: persist protected model configuration"
```

---

### Task 2: Resolve Providers Dynamically and Expose Configuration API

**Files:**
- Modify: `src/data_analysis_agent/providers.py`
- Modify: `src/data_analysis_agent/api.py`
- Modify: `src/data_analysis_agent/cli.py`
- Modify: `tests/conftest.py`
- Modify: `tests/test_providers.py`
- Modify: `tests/test_api.py`

**Interfaces:**
- Consumes: `ProviderCredentials(provider, base_url, model, api_key)`.
- Produces: `provider_from_credentials(credentials, timeout_seconds=60) -> ModelProvider`.
- Produces: `GET /api/model-config` returning redacted status plus `providers` catalog.
- Produces: `PUT /api/model-config` accepting `provider`, `model`, `base_url`, and optional `api_key`.
- Produces: `create_app(settings, provider=None, secret_protector=None, provider_factory=provider_from_credentials)`.
- Produces: `AppServices.resolve_provider() -> ModelProvider` where injected providers take precedence.

- [ ] **Step 1: Write failing provider and configuration API tests**

Add to `tests/test_providers.py`:

```python
def test_provider_from_credentials_uses_saved_endpoint():
    provider = provider_from_credentials(
        ProviderCredentials(
            provider=ModelProviderKind.CUSTOM,
            base_url="https://relay.example/v1",
            model="relay-model",
            api_key="relay-key",
        )
    )
    assert isinstance(provider, OpenAICompatibleProvider)
```

Add to `tests/test_api.py`:

```python
def test_model_configuration_api_never_returns_key(settings, recording_protector):
    with TestClient(create_app(settings, secret_protector=recording_protector)) as client:
        response = client.put("/api/model-config", json={
            "provider": "openai",
            "model": "gpt-5.6-terra",
            "api_key": "openai-secret",
        })
        assert response.status_code == 200
        body = client.get("/api/model-config").json()
        assert body["provider"] == "openai"
        assert body["has_api_key"] is True
        assert "api_key" not in body
        assert "openai-secret" not in client.get("/api/model-config").text


def test_unconfigured_task_is_rejected_before_creation(settings, recording_protector):
    with TestClient(create_app(settings, secret_protector=recording_protector)) as client:
        project = client.post("/api/projects", json={"name": "Review"}).json()
        response = client.post(
            f"/api/projects/{project['id']}/tasks",
            json={"objective": "Diagnose profit"},
        )
        assert response.status_code == 409
        assert response.json()["detail"]["category"] == "model_not_configured"
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python -m pytest tests/test_providers.py tests/test_api.py -v`

Expected: FAIL because the credential factory and configuration endpoints are absent and unconfigured tasks still return 201.

- [ ] **Step 3: Implement dynamic provider resolution and endpoints**

Move environment-independent provider creation to `provider_from_credentials`. Keep `provider_from_settings` only as a compatibility wrapper for existing callers, but remove it from `cli.py`.

`AppServices.agent()` calls `resolve_provider()` each time it creates an `AgentRunner`. Background helpers receive `settings`, optional provider override, protector, and provider factory, then rebuild services and resolve the current saved configuration. In `create_task`, resolve the provider before inserting the task; translate missing configuration to HTTP 409 with `{"category": "model_not_configured", "message": "Configure a model before starting analysis"}`.

The GET response includes the provider catalog, but never the protected key. The PUT handler maps configuration validation to HTTP 422 and secret-storage unavailability to HTTP 503.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `python -m pytest tests/test_providers.py tests/test_api.py -v`

Expected: PASS without any network call or real API key.

- [ ] **Step 5: Run the backend regression suite**

Run: `python -m pytest -q`

Expected: all backend tests PASS. Update deterministic API fixtures to inject `ScriptedProvider` where a task is expected to execute.

- [ ] **Step 6: Commit the API slice**

```powershell
git add src/data_analysis_agent/providers.py src/data_analysis_agent/api.py src/data_analysis_agent/cli.py tests/conftest.py tests/test_providers.py tests/test_api.py
git commit -m "feat: configure model providers through api"
```

---

### Task 3: Add the Workbench Model Route and Settings Sheet

**Files:**
- Modify: `web/src/types.ts`
- Modify: `web/src/api.ts`
- Create: `web/src/components/ModelSettings.tsx`
- Modify: `web/src/components/ProjectNav.tsx`
- Modify: `web/src/App.tsx`
- Modify: `web/src/App.test.tsx`
- Modify: `web/src/styles.css`

**Interfaces:**
- Produces: `ProviderDefinition`, `ModelConfigurationStatus`, and `SaveModelConfiguration` TypeScript types.
- Produces: `ApiClient.getModelConfiguration()` and `saveModelConfiguration(input)`.
- Produces: `ModelSettings({ configuration, open, onClose, onSave })`.
- Consumes: redacted configuration; never receives the saved key.

- [ ] **Step 1: Write failing workbench settings tests**

Extend the fake API with an unconfigured model response and add:

```tsx
it("configures a third-party model from the model route", async () => {
  const saveModelConfiguration = vi.fn(async (input) => configuredCustomStatus(input));
  render(<App api={fakeApi({ configured: false, saveModelConfiguration })} />);

  fireEvent.click(await screen.findByRole("button", { name: "配置模型" }));
  fireEvent.click(screen.getByRole("radio", { name: "第三方中转站" }));
  fireEvent.change(screen.getByLabelText("Base URL"), {
    target: { value: "https://relay.example/v1" },
  });
  fireEvent.change(screen.getByLabelText("模型名称"), {
    target: { value: "relay-model" },
  });
  fireEvent.change(screen.getByLabelText("API Key"), {
    target: { value: "relay-secret" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存模型配置" }));

  await waitFor(() => expect(saveModelConfiguration).toHaveBeenCalledWith({
    provider: "custom",
    base_url: "https://relay.example/v1",
    model: "relay-model",
    api_key: "relay-secret",
  }));
  expect(screen.getByText("relay-model")).toBeVisible();
});


it("locks the official endpoint and offers current DeepSeek models", async () => {
  render(<App api={fakeApi({ configured: false })} />);
  fireEvent.click(await screen.findByRole("button", { name: "配置模型" }));
  fireEvent.click(screen.getByRole("radio", { name: "DeepSeek" }));
  expect(screen.getByDisplayValue("https://api.deepseek.com")).toBeDisabled();
  expect(screen.getByRole("option", { name: "deepseek-v4-flash" })).toBeVisible();
  expect(screen.getByRole("option", { name: "deepseek-v4-pro" })).toBeVisible();
});
```

- [ ] **Step 2: Run the frontend tests and verify RED**

Run: `npm --prefix web test -- --run`

Expected: FAIL because `ApiClient` and the workbench have no model configuration workflow.

- [ ] **Step 3: Implement typed client and settings sheet**

Load `getModelConfiguration()` once when `App` mounts. Pass the status to `ProjectNav`, where a `MODEL ROUTE` button displays the provider label and model. An unconfigured status displays `配置模型`.

`ModelSettings` maintains draft state initialized on open. Provider radio rows set provider defaults. Built-in Base URLs render in a disabled input; custom provider fields are editable. API Key uses `type="password"`, clears after successful save, and displays `留空以保留已保存密钥` only when `has_api_key` is true and the provider has not changed.

The sheet uses `role="dialog"`, an accessible title, Escape/close controls, visible focus states, and a narrow-screen full-width layout. Use the existing cold analytical palette and evidence-rule vocabulary; add no gradients or generic rounded cards.

- [ ] **Step 4: Run frontend tests and verify GREEN**

Run: `npm --prefix web test -- --run`

Expected: both existing workbench tests and new configuration tests PASS.

- [ ] **Step 5: Run the production build**

Run: `npm --prefix web run build`

Expected: strict TypeScript and Vite build PASS.

- [ ] **Step 6: Commit the frontend slice**

```powershell
git add web/src
git commit -m "feat: add workbench model settings"
```

---

### Task 4: Update Operating Documentation and Verify the Feature

**Files:**
- Modify: `README.md`
- Modify: `tests/test_acceptance.py` only if the dynamic provider boundary requires an injected deterministic provider signature update.

**Interfaces:**
- Produces: a startup flow with no model environment variables.
- Documents: provider selection, key protection, third-party compatibility, and configuration recovery.

- [ ] **Step 1: Replace environment-variable setup documentation**

Remove `DAA_MODEL_BASE_URL`, `DAA_MODEL_API_KEY`, and `DAA_MODEL_NAME` setup commands. Document this flow:

1. Start API and UI with no model variables.
2. Select `MODEL ROUTE / 配置模型` in the left rail.
3. Choose DeepSeek, OpenAI, or third-party relay.
4. Choose or enter the model, enter API Key, and save.
5. Start a new analysis task.

State that keys are DPAPI-protected for the current Windows user, never returned to the browser, and must be re-entered when switching provider.

- [ ] **Step 2: Run fresh complete verification**

Run: `python -m pytest -v`

Expected: all backend tests PASS without real credentials.

Run: `npm --prefix web test -- --run`

Expected: all frontend tests PASS.

Run: `npm --prefix web run build`

Expected: production build PASS.

Run: `git diff --check`

Expected: no whitespace errors.

- [ ] **Step 3: Commit the completed feature**

```powershell
git add README.md tests/test_acceptance.py
git commit -m "docs: explain workbench model configuration"
```

## Plan Self-Review Results

- Every design requirement maps to Tasks 1 through 4.
- Provider/model names and URLs are identical across the design, backend interfaces, frontend tests, and documentation steps.
- Secret preservation and provider-switch behavior are explicitly tested.
- Dynamic provider injection preserves deterministic tests and avoids real network calls.
- The plan contains no placeholder implementation steps and does not touch user-owned untracked files.
