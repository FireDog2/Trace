import { FormEvent, useEffect, useMemo, useState } from "react";

import type {
  ModelConfigurationStatus,
  SaveModelConfiguration,
} from "../types";

interface ModelSettingsProps {
  configuration: ModelConfigurationStatus;
  open: boolean;
  onClose(): void;
  onSave(input: SaveModelConfiguration): Promise<void>;
}

export function ModelSettings({
  configuration,
  open,
  onClose,
  onSave,
}: ModelSettingsProps) {
  const initialProvider =
    configuration.provider ??
    configuration.providers[0]?.provider ??
    "deepseek";
  const [provider, setProvider] = useState(initialProvider);
  const [baseUrl, setBaseUrl] = useState(configuration.base_url);
  const [model, setModel] = useState(configuration.model);
  const [apiKey, setApiKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const definition = useMemo(
    () => configuration.providers.find((item) => item.provider === provider),
    [configuration.providers, provider],
  );

  useEffect(() => {
    if (!open) return;
    const nextProvider =
      configuration.provider ??
      configuration.providers[0]?.provider ??
      "deepseek";
    const nextDefinition = configuration.providers.find(
      (item) => item.provider === nextProvider,
    );
    setProvider(nextProvider);
    setBaseUrl(
      configuration.configured
        ? configuration.base_url
        : (nextDefinition?.base_url ?? ""),
    );
    setModel(
      configuration.configured
        ? configuration.model
        : (nextDefinition?.default_model ?? ""),
    );
    setApiKey("");
    setError(null);
  }, [configuration, open]);

  useEffect(() => {
    if (!open) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose, open]);

  if (!open || !definition) return null;

  function chooseProvider(nextProvider: string) {
    const nextDefinition = configuration.providers.find(
      (item) => item.provider === nextProvider,
    );
    if (!nextDefinition) return;
    setProvider(nextProvider);
    setBaseUrl(nextDefinition.base_url ?? "");
    setModel(nextDefinition.default_model);
    setApiKey("");
    setError(null);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await onSave({
        provider,
        base_url: baseUrl,
        model: model.trim(),
        api_key: apiKey || null,
      });
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setSaving(false);
    }
  }

  const preservesKey =
    configuration.has_api_key && configuration.provider === provider;

  return (
    <div
      className="model-settings-backdrop"
      onMouseDown={(event) => {
        if (event.currentTarget === event.target) onClose();
      }}
    >
      <section
        className="model-settings"
        role="dialog"
        aria-modal="true"
        aria-labelledby="model-settings-title"
      >
        <header className="model-settings-header">
          <div>
            <span className="settings-index">MODEL ROUTE / RUNTIME</span>
            <h2 id="model-settings-title">Model configuration</h2>
            <p>
              Saved settings apply to new analysis tasks immediately. No service
              restart is required.
            </p>
          </div>
          <button
            type="button"
            className="settings-close"
            aria-label="Close model configuration"
            onClick={onClose}
          >
            ×
          </button>
        </header>

        <form onSubmit={submit}>
          <div className="model-settings-layout">
            <section
              className="settings-provider-panel"
              aria-labelledby="provider-heading"
            >
              <h3 id="provider-heading">Model provider</h3>
              <p>Choose the inference service used for analysis tasks.</p>
              <fieldset className="provider-selector">
                <legend className="sr-only">Choose a provider</legend>
                {configuration.providers.map((item, index) => (
                  <label
                    key={item.provider}
                    className={provider === item.provider ? "selected" : ""}
                  >
                    <input
                      type="radio"
                      aria-label={item.label}
                      name="provider"
                      value={item.provider}
                      checked={provider === item.provider}
                      onChange={() => chooseProvider(item.provider)}
                    />
                    <span className="provider-number">0{index + 1}</span>
                    <span>
                      <strong>{item.label}</strong>
                      <small>
                        {item.custom_url
                          ? "OpenAI-compatible API"
                          : "Official API"}
                      </small>
                    </span>
                  </label>
                ))}
              </fieldset>
            </section>

            <section
              className="settings-config-panel"
              aria-labelledby="configuration-heading"
            >
              <h3 id="configuration-heading">Connection settings</h3>
              <p>
                Set the service URL, model, and API key for the current
                provider.
              </p>
              <div className="settings-fields">
                <label>
                  <span>Base URL</span>
                  <input
                    aria-label="Base URL"
                    type="url"
                    value={baseUrl}
                    disabled={!definition.custom_url}
                    required
                    onChange={(event) => setBaseUrl(event.target.value)}
                  />
                  {!definition.custom_url && (
                    <small>
                      The official URL is locked to prevent misconfiguration.
                    </small>
                  )}
                </label>

                <label>
                  <span>Model</span>
                  {definition.models.length > 0 ? (
                    <select
                      aria-label="Model"
                      value={model}
                      onChange={(event) => setModel(event.target.value)}
                    >
                      {definition.models.map((item) => (
                        <option key={item} value={item}>
                          {item}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <input
                      aria-label="Model name"
                      value={model}
                      required
                      placeholder="For example: provider/model-name"
                      onChange={(event) => setModel(event.target.value)}
                    />
                  )}
                </label>

                <label>
                  <span>API Key</span>
                  <input
                    aria-label="API Key"
                    type="password"
                    value={apiKey}
                    required={!preservesKey}
                    autoComplete="new-password"
                    placeholder={
                      preservesKey
                        ? "Leave blank to keep the saved key"
                        : "Enter the provider API key"
                    }
                    onChange={(event) => setApiKey(event.target.value)}
                  />
                  <small>
                    {preservesKey
                      ? "Saved securely; the key is never displayed here."
                      : "The key is stored only in encrypted storage for the current Windows user."}
                  </small>
                </label>
              </div>
            </section>
          </div>

          {error && (
            <p className="settings-error" role="alert">
              {error}
            </p>
          )}

          <footer className="settings-actions">
            <span>
              {configuration.configured
                ? "Updates apply to the next run"
                : "Complete setup to start analyzing"}
            </span>
            <button type="button" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" disabled={saving}>
              {saving ? "Saving…" : "Save model configuration"}
            </button>
          </footer>
        </form>
      </section>
    </div>
  );
}
