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
  const initialProvider = configuration.provider ?? configuration.providers[0]?.provider ?? "deepseek";
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
    const nextProvider = configuration.provider ?? configuration.providers[0]?.provider ?? "deepseek";
    const nextDefinition = configuration.providers.find((item) => item.provider === nextProvider);
    setProvider(nextProvider);
    setBaseUrl(configuration.configured ? configuration.base_url : (nextDefinition?.base_url ?? ""));
    setModel(configuration.configured ? configuration.model : (nextDefinition?.default_model ?? ""));
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
    const nextDefinition = configuration.providers.find((item) => item.provider === nextProvider);
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

  const preservesKey = configuration.has_api_key && configuration.provider === provider;

  return (
    <div className="model-settings-backdrop" onMouseDown={(event) => {
      if (event.currentTarget === event.target) onClose();
    }}>
      <section className="model-settings" role="dialog" aria-modal="true" aria-labelledby="model-settings-title">
        <header className="model-settings-header">
          <div>
            <span className="settings-index">MODEL ROUTE / RUNTIME</span>
            <h2 id="model-settings-title">模型配置</h2>
            <p>保存后立即用于新分析任务，不需要重启服务。</p>
          </div>
          <button type="button" className="settings-close" aria-label="关闭模型配置" onClick={onClose}>×</button>
        </header>

        <form onSubmit={submit}>
          <fieldset className="provider-selector">
            <legend>选择厂商</legend>
            {configuration.providers.map((item, index) => (
              <label key={item.provider} className={provider === item.provider ? "selected" : ""}>
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
                  <small>{item.custom_url ? "OpenAI 兼容接口" : "官方接口"}</small>
                </span>
              </label>
            ))}
          </fieldset>

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
              {!definition.custom_url && <small>官方地址已锁定，避免误配。</small>}
            </label>

            <label>
              <span>模型</span>
              {definition.models.length > 0 ? (
                <select aria-label="模型" value={model} onChange={(event) => setModel(event.target.value)}>
                  {definition.models.map((item) => <option key={item} value={item}>{item}</option>)}
                </select>
              ) : (
                <input
                  aria-label="模型名称"
                  value={model}
                  required
                  placeholder="例如：provider/model-name"
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
                placeholder={preservesKey ? "留空则继续使用已保存的密钥" : "输入厂商 API Key"}
                onChange={(event) => setApiKey(event.target.value)}
              />
              <small>{preservesKey ? "已安全保存；此处不会回显。" : "密钥仅保存在当前 Windows 用户的加密存储中。"}</small>
            </label>
          </div>

          {error && <p className="settings-error" role="alert">{error}</p>}

          <footer className="settings-actions">
            <span>{configuration.configured ? "更新将作用于下一次执行" : "完成配置后即可开始分析"}</span>
            <button type="button" onClick={onClose}>取消</button>
            <button type="submit" disabled={saving}>{saving ? "保存中…" : "保存模型配置"}</button>
          </footer>
        </form>
      </section>
    </div>
  );
}
