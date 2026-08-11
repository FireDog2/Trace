import { FormEvent, useEffect, useMemo, useState } from "react";

import type { ApiClient } from "../api";
import type { AccessLevel, DataSource, PolicyRule } from "../types";

const policyKey = (table: string, column: string | null) => `${table}\u0000${column ?? ""}`;

export function DataSourceSettings({ open, api, onClose }: {
  open: boolean; api: ApiClient; onClose(): void;
}) {
  const [sources, setSources] = useState<DataSource[]>([]);
  const [source, setSource] = useState<DataSource | null>(null);
  const [name, setName] = useState(""); const [dsn, setDsn] = useState("");
  const [schema, setSchema] = useState<Record<string, string[]>>({});
  const [schemaStatus, setSchemaStatus] = useState<"idle" | "loading" | "ready" | "empty" | "error">("idle");
  const [rules, setRules] = useState<Record<string, AccessLevel>>({});
  const [savedRules, setSavedRules] = useState<Record<string, AccessLevel>>({});
  const [editing, setEditing] = useState(false); const [saving, setSaving] = useState(false);
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [manualTable, setManualTable] = useState(""); const [manualColumn, setManualColumn] = useState("");
  const [notice, setNotice] = useState(""); const [error, setError] = useState("");
  const dirty = useMemo(() => JSON.stringify(rules) !== JSON.stringify(savedRules), [rules, savedRules]);

  useEffect(() => {
    if (!open) return;
    setError(""); setSource(null); setEditing(false); setSchemaStatus("idle"); setDeleteConfirmOpen(false);
    void api.listDataSources().then((items) => {
      setSources(items);
      if (items[0]) void choose(items[0]);
    }).catch(() => setError("无法读取数据源。请确认项目权限。"));
  }, [api, open]);

  async function choose(next: DataSource) {
    setSource(next); setNotice(""); setError(""); setEditing(false); setDeleteConfirmOpen(false); setSchema({}); setSchemaStatus("loading");
    setName(next.name); setDsn(""); setManualTable(""); setManualColumn("");
    const schemaRequest = api.getDataSourceSchema(next.id)
      .then((value) => ({ ok: true as const, value }))
      .catch(() => ({ ok: false as const }));
    try {
      const policyValue = await api.getDataSourcePolicies(next.id);
      const policySchema = schemaFromPolicies(policyValue.rules);
      const policyRules = hydrateRules(policySchema, policyValue.rules);
      setSchema(policySchema); setRules(policyRules); setSavedRules(policyRules);
      setSource({ ...next, policy_version: policyValue.version });
      const schemaResult = await schemaRequest;
      if (!schemaResult.ok) {
        setSchemaStatus("error");
        return;
      }
      const mergedSchema = mergeSchemas(policySchema, schemaResult.value.tables);
      const hydrated = hydrateRules(mergedSchema, policyValue.rules);
      setSchema(mergedSchema); setRules(hydrated); setSavedRules(hydrated);
      setSchemaStatus(Object.keys(schemaResult.value.tables).length ? "ready" : "empty");
    } catch { setSchemaStatus("error"); setError("无法读取已有访问策略，请刷新后重试。"); }
  }

  async function saveSource(event: FormEvent) {
    event.preventDefault();
    try {
      const saved = source
        ? await api.updateDataSource(source.id, { name, ...(dsn.trim() ? { dsn } : {}) })
        : await api.createDataSource({ name, dsn });
      setDsn("");
      setSources((current) => source
        ? current.map((item) => item.id === saved.id ? saved : item)
        : [...current, saved]);
      await choose(saved);
      if (!source) setEditing(true);
      setNotice(source ? "连接配置已更新；DSN 已安全保存。" : "已保存连接。现在可以配置首个访问策略版本。");
    } catch { setDsn(""); setError(source ? "连接修改未保存。请检查名称或新的 PostgreSQL DSN。" : "连接未保存。请检查 PostgreSQL DSN。"); }
  }

  function beginNewSource() {
    setSource(null); setName(""); setDsn(""); setSchema({}); setSchemaStatus("idle");
    setRules({}); setSavedRules({}); setEditing(false); setDeleteConfirmOpen(false); setNotice(""); setError("");
  }

  async function removeSource() {
    if (!source || deleting) return;
    const deletedSource = source;
    setDeleting(true); setError("");
    try {
      await api.deleteDataSource(deletedSource.id);
      setSources((current) => current.filter((item) => item.id !== deletedSource.id));
      setSource(null); setName(""); setDsn(""); setSchema({}); setSchemaStatus("idle");
      setRules({}); setSavedRules({}); setEditing(false); setDeleteConfirmOpen(false);
      setNotice(`已删除数据源 ${deletedSource.name}；历史查询与审计记录仍保留。`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "数据源未删除，请稍后重试。");
    } finally {
      setDeleting(false);
    }
  }

  function changeTable(table: string, columns: string[], access: AccessLevel) {
    setRules((current) => {
      const next = { ...current, [policyKey(table, null)]: access };
      for (const column of columns) next[policyKey(table, column)] = access;
      return next;
    });
  }

  function addManualRule(event: FormEvent) {
    event.preventDefault();
    const table = manualTable.trim(); const column = manualColumn.trim();
    if (!table) return;
    setSchema((current) => {
      const columns = current[table] ?? [];
      return { ...current, [table]: column && !columns.includes(column) ? [...columns, column] : columns };
    });
    setRules((current) => {
      const next = { ...current };
      if (!(policyKey(table, null) in next)) next[policyKey(table, null)] = "denied";
      if (column) next[policyKey(table, column)] = next[policyKey(table, null)];
      return next;
    });
    setManualTable(""); setManualColumn("");
  }

  async function savePolicies() {
    if (!source || !dirty) return;
    const payload: PolicyRule[] = Object.entries(rules).map(([key, access]) => {
      const [table, column] = key.split("\u0000");
      return { table, column: column || null, access };
    });
    setSaving(true); setError("");
    try {
      const saved = await api.replaceDataSourcePolicies(source.id, payload, source.policy_version);
      setSources((current) => current.map((item) => item.id === source.id ? { ...item, policy_version: saved.version } : item));
      setSource((current) => current ? { ...current, policy_version: saved.version } : current);
      setSavedRules(rules); setEditing(false);
      setNotice(`访问策略修改已保存为 v${saved.version}。`);
    } catch (reason) {
      setError(reason instanceof Error && reason.message.includes("changed")
        ? "访问策略已被其他人修改，请重新载入后再编辑。"
        : "策略未保存。请检查字段配置后重试。");
    } finally { setSaving(false); }
  }

  function cancelEditing() { setRules(savedRules); setEditing(false); setError(""); }

  if (!open) return null;
  return <div className="drawer-backdrop"><aside className="data-source-settings" role="dialog" aria-modal="true" aria-label="数据库治理">
    <header className="drawer-header"><div><span className="settings-index">GOVERN / POSTGRES</span><h2>数据库治理</h2>
      <p>连接、字段级策略与人工审批边界。</p></div><button className="settings-close" onClick={onClose} aria-label="关闭数据库治理">×</button></header>
    {error && <p className="settings-error" role="alert">{error}</p>}{notice && <p className="settings-notice">{notice}</p>}
    <form className="settings-fields connection-editor" onSubmit={saveSource}>
      <div className="connection-editor-heading"><div><span className="settings-index">{source ? "EDIT CONNECTION" : "NEW CONNECTION"}</span>
        <h3>{source ? "修改连接配置" : "添加 PostgreSQL 连接"}</h3></div>
        {source && <div className="connection-editor-actions">
          <button type="button" className="delete-source-trigger" onClick={() => setDeleteConfirmOpen(true)}>删除数据源</button>
          <button type="button" onClick={beginNewSource}>＋ 新增连接</button>
        </div>}</div>
      <label><span>数据源名称</span>
      <input aria-label="数据源名称" value={name} onChange={(event) => setName(event.target.value)} required /></label>
      <label><span>PostgreSQL DSN</span><input aria-label="PostgreSQL DSN" type="password" autoComplete="off"
        value={dsn} onChange={(event) => setDsn(event.target.value)} required={!source}
        placeholder={source ? "••••••••••••（已配置）" : "postgresql://user:password@host:5432/database"} />
        {source && <small className="credential-hint"><i aria-hidden="true">✓</i> DSN 已加密保存；留空保持原配置，输入新地址可替换。</small>}</label>
      <button type="submit" className="primary-action">{source ? "保存连接修改" : "保存并检查连接"}</button></form>
    {source && deleteConfirmOpen && <section className="delete-source-confirmation" role="alertdialog" aria-labelledby="delete-source-title">
      <div><span className="settings-index">DELETE CONNECTION</span><h3 id="delete-source-title">删除 {source.name}？</h3>
        <p>删除后，所有项目都无法再使用此连接；已产生的历史查询与审计记录会继续保留。</p></div>
      <div className="delete-source-actions">
        <button type="button" onClick={() => setDeleteConfirmOpen(false)} disabled={deleting}>取消</button>
        <button type="button" className="danger-action" onClick={() => void removeSource()} disabled={deleting}>{deleting ? "删除中…" : "确认删除"}</button>
      </div>
    </section>}
    <section className="governed-sources"><h3>已连接数据源</h3>{sources.length ? <div className="source-list">
      {sources.map((item) => <button type="button" key={item.id} onClick={() => void choose(item)} className={source?.id === item.id ? "active" : ""}>
        {item.name}<small>policy v{item.policy_version}</small></button>)}</div> : <p className="empty-note">先保存一个 PostgreSQL 数据源，再配置表和字段策略。</p>}</section>
    {source && <section className={`policy-editor ${editing ? "is-editing" : ""}`}><div className="panel-heading"><div><h3>{source.name} 访问策略</h3>
      <p className="policy-version-note">当前 v{source.policy_version} · {editing ? "正在修改，保存后生成新版本" : "只读预览"}</p></div>
      {editing ? <div className="policy-actions"><button type="button" onClick={cancelEditing}>取消</button>
        <button type="button" onClick={() => void savePolicies()} disabled={!dirty || saving} className="primary-action">{saving ? "保存中…" : "保存修改"}</button></div>
        : <button type="button" onClick={() => { setEditing(true); setNotice(""); }} className="primary-action">修改访问策略</button>}</div>
      <p className="policy-help">修改表级权限会同步该表全部字段，也可以再单独调整某个字段。</p>
      {schemaStatus === "loading" && <div className="policy-schema-state" role="status"><span className="schema-spinner" />
        <div><strong>正在读取表结构</strong><p>已有策略会先显示，数据库字段将在连接完成后补齐。</p></div></div>}
      {(schemaStatus === "empty" || schemaStatus === "error") && <div className="policy-schema-state is-warning" role="status">
        <div><strong>{schemaStatus === "error" ? "暂时无法连接数据库" : "数据库没有返回可见表"}</strong>
          <p>你仍可修改已显示的规则，或手动添加需要治理的表和字段。</p></div>
        <button type="button" onClick={() => void choose(source)}>重新读取</button></div>}
      {editing && (schemaStatus === "empty" || schemaStatus === "error") && <form className="manual-policy-rule" onSubmit={addManualRule}>
        <label><span>表名</span><input aria-label="手动添加表名" value={manualTable} onChange={(event) => setManualTable(event.target.value)} placeholder="orders" required /></label>
        <label><span>字段名（可选）</span><input aria-label="手动添加字段名" value={manualColumn} onChange={(event) => setManualColumn(event.target.value)} placeholder="customer_email" /></label>
        <button type="submit">添加规则</button></form>}
      {Object.entries(schema).map(([table, columns]) => <details key={table} open><summary>{table} <small>{columns.length} fields</small></summary>
        <PolicySelect label={`${table}.*`} value={rules[policyKey(table, null)] ?? "denied"} disabled={!editing}
          onChange={(access) => changeTable(table, columns, access)} />
        {columns.map((column) => <PolicySelect key={column} label={`${table}.${column}`}
          value={rules[policyKey(table, column)] ?? rules[policyKey(table, null)] ?? "denied"} disabled={!editing}
          onChange={(access) => setRules((current) => ({ ...current, [policyKey(table, column)]: access }))} />)}
      </details>)}</section>}
  </aside></div>;
}

function schemaFromPolicies(rules: PolicyRule[]): Record<string, string[]> {
  const schema: Record<string, string[]> = {};
  for (const rule of rules) {
    const columns = schema[rule.table] ?? [];
    schema[rule.table] = rule.column && !columns.includes(rule.column) ? [...columns, rule.column] : columns;
  }
  return schema;
}

function mergeSchemas(first: Record<string, string[]>, second: Record<string, string[]>): Record<string, string[]> {
  const merged = { ...first };
  for (const [table, columns] of Object.entries(second)) {
    merged[table] = [...new Set([...(merged[table] ?? []), ...columns])];
  }
  return merged;
}

function hydrateRules(schema: Record<string, string[]>, policies: PolicyRule[]): Record<string, AccessLevel> {
  const explicit = Object.fromEntries(policies.map((rule) => [policyKey(rule.table, rule.column), rule.access])) as Record<string, AccessLevel>;
  const hydrated: Record<string, AccessLevel> = {};
  for (const [table, columns] of Object.entries(schema)) {
    const tableAccess = explicit[policyKey(table, null)] ?? "denied";
    hydrated[policyKey(table, null)] = tableAccess;
    for (const column of columns) hydrated[policyKey(table, column)] = explicit[policyKey(table, column)] ?? tableAccess;
  }
  return hydrated;
}

function PolicySelect({ label, value, disabled, onChange }: { label: string; value: AccessLevel; disabled: boolean; onChange(value: AccessLevel): void }) {
  return <label className="policy-row"><span>{label}</span><select aria-label={`${label} 权限`} value={value} disabled={disabled}
    onChange={(event) => onChange(event.target.value as AccessLevel)}>
    <option value="allowed">自动允许</option><option value="approval_required">需要审批</option><option value="denied">禁止访问</option>
  </select></label>;
}
