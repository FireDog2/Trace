import { FormEvent, useEffect, useState } from "react";

import type { ApiClient } from "../api";
import type { AccessLevel, DataSource, PolicyRule } from "../types";

const policyKey = (table: string, column: string | null) => `${table}\u0000${column ?? ""}`;

export function DataSourceSettings({ open, projectId, api, onClose }: {
  open: boolean; projectId: string | null; api: ApiClient; onClose(): void;
}) {
  const [sources, setSources] = useState<DataSource[]>([]);
  const [source, setSource] = useState<DataSource | null>(null);
  const [name, setName] = useState(""); const [dsn, setDsn] = useState("");
  const [schema, setSchema] = useState<Record<string, string[]>>({});
  const [rules, setRules] = useState<Record<string, AccessLevel>>({});
  const [notice, setNotice] = useState(""); const [error, setError] = useState("");

  useEffect(() => {
    if (!open || !projectId) return;
    setError("");
    void api.listDataSources(projectId).then(setSources)
      .catch(() => setError("无法读取数据源。请确认项目权限。"));
  }, [api, open, projectId]);

  async function choose(next: DataSource) {
    setSource(next); setNotice(""); setError("");
    try {
      const [schemaValue, policyValue] = await Promise.all([
        api.getDataSourceSchema(next.id), api.getDataSourcePolicies(next.id),
      ]);
      setSchema(schemaValue.tables);
      setRules(Object.fromEntries(policyValue.rules.map((rule) => [policyKey(rule.table, rule.column), rule.access])));
    } catch { setError("无法检查 PostgreSQL schema 或读取策略。请确认连接可用。"); }
  }

  async function saveSource(event: FormEvent) {
    event.preventDefault(); if (!projectId) return;
    try {
      const saved = await api.createDataSource(projectId, { name, dsn });
      setDsn(""); setName(""); setSources((current) => [...current, saved]);
      await choose(saved); setNotice("已保存连接；凭据不会显示或保留在页面中。");
    } catch { setDsn(""); setError("连接未保存。请检查 PostgreSQL DSN。"); }
  }

  async function savePolicies() {
    if (!source) return;
    const payload: PolicyRule[] = Object.entries(rules).map(([key, access]) => {
      const [table, column] = key.split("\u0000");
      return { table, column: column || null, access };
    });
    try {
      const saved = await api.replaceDataSourcePolicies(source.id, payload);
      setSources((current) => current.map((item) => item.id === source.id ? { ...item, policy_version: saved.version } : item));
      setSource((current) => current ? { ...current, policy_version: saved.version } : current);
      setNotice(`访问策略已保存，版本 v${saved.version}。`);
    } catch { setError("策略未保存。请检查字段配置后重试。"); }
  }

  if (!open) return null;
  return <div className="drawer-backdrop"><aside className="data-source-settings" role="dialog" aria-modal="true" aria-label="数据库治理">
    <header className="drawer-header"><div><span className="settings-index">GOVERN / POSTGRES</span><h2>数据库治理</h2>
      <p>连接、字段级策略与人工审批边界。</p></div><button className="settings-close" onClick={onClose} aria-label="关闭数据库治理">×</button></header>
    {error && <p className="settings-error" role="alert">{error}</p>}{notice && <p className="settings-notice">{notice}</p>}
    <form className="settings-fields" onSubmit={saveSource}><label><span>数据源名称</span>
      <input aria-label="数据源名称" value={name} onChange={(event) => setName(event.target.value)} required /></label>
      <label><span>PostgreSQL DSN</span><input aria-label="PostgreSQL DSN" type="password" autoComplete="off"
        value={dsn} onChange={(event) => setDsn(event.target.value)} required /></label>
      <button type="submit" className="primary-action">保存并检查连接</button></form>
    <section className="governed-sources"><h3>已连接数据源</h3>{sources.length ? <div className="source-list">
      {sources.map((item) => <button type="button" key={item.id} onClick={() => void choose(item)} className={source?.id === item.id ? "active" : ""}>
        {item.name}<small>policy v{item.policy_version}</small></button>)}</div> : <p className="empty-note">先保存一个 PostgreSQL 数据源，再配置表和字段策略。</p>}</section>
    {source && <section className="policy-editor"><div className="panel-heading"><h3>{source.name} 访问策略</h3>
      <button type="button" onClick={() => void savePolicies()} className="primary-action">保存访问策略</button></div>
      {Object.entries(schema).map(([table, columns]) => <details key={table} open><summary>{table} <small>{columns.length} fields</small></summary>
        <PolicySelect label={`${table}.*`} value={rules[policyKey(table, null)] ?? "allowed"}
          onChange={(access) => setRules((current) => ({ ...current, [policyKey(table, null)]: access }))} />
        {columns.map((column) => <PolicySelect key={column} label={`${table}.${column}`}
          value={rules[policyKey(table, column)] ?? "allowed"}
          onChange={(access) => setRules((current) => ({ ...current, [policyKey(table, column)]: access }))} />)}
      </details>)}</section>}
  </aside></div>;
}

function PolicySelect({ label, value, onChange }: { label: string; value: AccessLevel; onChange(value: AccessLevel): void }) {
  return <label className="policy-row"><span>{label}</span><select aria-label={`${label} 权限`} value={value}
    onChange={(event) => onChange(event.target.value as AccessLevel)}>
    <option value="allowed">自动允许</option><option value="approval_required">需要审批</option><option value="denied">禁止访问</option>
  </select></label>;
}
