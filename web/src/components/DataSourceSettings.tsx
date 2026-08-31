import { FormEvent, useEffect, useMemo, useState } from "react";

import type { ApiClient } from "../api";
import type { AccessLevel, DataSource, PolicyRule } from "../types";

const policyKey = (table: string, column: string | null) =>
  `${table}\u0000${column ?? ""}`;

export function DataSourceSettings({
  open,
  api,
  onClose,
}: {
  open: boolean;
  api: ApiClient;
  onClose(): void;
}) {
  const [sources, setSources] = useState<DataSource[]>([]);
  const [source, setSource] = useState<DataSource | null>(null);
  const [name, setName] = useState("");
  const [dsn, setDsn] = useState("");
  const [schema, setSchema] = useState<Record<string, string[]>>({});
  const [schemaStatus, setSchemaStatus] = useState<
    "idle" | "loading" | "ready" | "empty" | "error"
  >("idle");
  const [rules, setRules] = useState<Record<string, AccessLevel>>({});
  const [savedRules, setSavedRules] = useState<Record<string, AccessLevel>>({});
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [manualTable, setManualTable] = useState("");
  const [manualColumn, setManualColumn] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const dirty = useMemo(
    () => JSON.stringify(rules) !== JSON.stringify(savedRules),
    [rules, savedRules],
  );

  useEffect(() => {
    if (!open) return;
    setError("");
    setSource(null);
    setEditing(false);
    setSchemaStatus("idle");
    setDeleteConfirmOpen(false);
    void api
      .listDataSources()
      .then((items) => {
        setSources(items);
        if (items[0]) void choose(items[0]);
      })
      .catch(() =>
        setError("Unable to load data sources. Check the project permissions."),
      );
  }, [api, open]);

  async function choose(next: DataSource) {
    setSource(next);
    setNotice("");
    setError("");
    setEditing(false);
    setDeleteConfirmOpen(false);
    setSchema({});
    setSchemaStatus("loading");
    setName(next.name);
    setDsn("");
    setManualTable("");
    setManualColumn("");
    const schemaRequest = api
      .getDataSourceSchema(next.id)
      .then((value) => ({ ok: true as const, value }))
      .catch(() => ({ ok: false as const }));
    try {
      const policyValue = await api.getDataSourcePolicies(next.id);
      const policySchema = schemaFromPolicies(policyValue.rules);
      const policyRules = hydrateRules(policySchema, policyValue.rules);
      setSchema(policySchema);
      setRules(policyRules);
      setSavedRules(policyRules);
      setSource({ ...next, policy_version: policyValue.version });
      const schemaResult = await schemaRequest;
      if (!schemaResult.ok) {
        setSchemaStatus("error");
        return;
      }
      const mergedSchema = mergeSchemas(
        policySchema,
        schemaResult.value.tables,
      );
      const hydrated = hydrateRules(mergedSchema, policyValue.rules);
      setSchema(mergedSchema);
      setRules(hydrated);
      setSavedRules(hydrated);
      setSchemaStatus(
        Object.keys(schemaResult.value.tables).length ? "ready" : "empty",
      );
    } catch {
      setSchemaStatus("error");
      setError(
        "Unable to load the existing access policy. Refresh and try again.",
      );
    }
  }

  async function saveSource(event: FormEvent) {
    event.preventDefault();
    try {
      const saved = source
        ? await api.updateDataSource(source.id, {
            name,
            ...(dsn.trim() ? { dsn } : {}),
          })
        : await api.createDataSource({ name, dsn });
      setDsn("");
      setSources((current) =>
        source
          ? current.map((item) => (item.id === saved.id ? saved : item))
          : [...current, saved],
      );
      await choose(saved);
      if (!source) setEditing(true);
      setNotice(
        source
          ? "Connection settings updated; the DSN is stored securely."
          : "Connection saved. You can now configure the first access policy version.",
      );
    } catch {
      setDsn("");
      setError(
        source
          ? "Connection changes were not saved. Check the name or new PostgreSQL DSN."
          : "Connection was not saved. Check the PostgreSQL DSN.",
      );
    }
  }

  function beginNewSource() {
    setSource(null);
    setName("");
    setDsn("");
    setSchema({});
    setSchemaStatus("idle");
    setRules({});
    setSavedRules({});
    setEditing(false);
    setDeleteConfirmOpen(false);
    setNotice("");
    setError("");
  }

  async function removeSource() {
    if (!source || deleting) return;
    const deletedSource = source;
    setDeleting(true);
    setError("");
    try {
      await api.deleteDataSource(deletedSource.id);
      setSources((current) =>
        current.filter((item) => item.id !== deletedSource.id),
      );
      setSource(null);
      setName("");
      setDsn("");
      setSchema({});
      setSchemaStatus("idle");
      setRules({});
      setSavedRules({});
      setEditing(false);
      setDeleteConfirmOpen(false);
      setNotice(
        `Deleted data source ${deletedSource.name}; historical queries and audit records are retained.`,
      );
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "The data source was not deleted. Try again later.",
      );
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
    const table = manualTable.trim();
    const column = manualColumn.trim();
    if (!table) return;
    setSchema((current) => {
      const columns = current[table] ?? [];
      return {
        ...current,
        [table]:
          column && !columns.includes(column) ? [...columns, column] : columns,
      };
    });
    setRules((current) => {
      const next = { ...current };
      if (!(policyKey(table, null) in next))
        next[policyKey(table, null)] = "denied";
      if (column) next[policyKey(table, column)] = next[policyKey(table, null)];
      return next;
    });
    setManualTable("");
    setManualColumn("");
  }

  async function savePolicies() {
    if (!source || !dirty) return;
    const payload: PolicyRule[] = Object.entries(rules).map(([key, access]) => {
      const [table, column] = key.split("\u0000");
      return { table, column: column || null, access };
    });
    setSaving(true);
    setError("");
    try {
      const saved = await api.replaceDataSourcePolicies(
        source.id,
        payload,
        source.policy_version,
      );
      setSources((current) =>
        current.map((item) =>
          item.id === source.id
            ? { ...item, policy_version: saved.version }
            : item,
        ),
      );
      setSource((current) =>
        current ? { ...current, policy_version: saved.version } : current,
      );
      setSavedRules(rules);
      setEditing(false);
      setNotice(`Access policy changes saved as v${saved.version}.`);
    } catch (reason) {
      setError(
        reason instanceof Error && reason.message.includes("changed")
          ? "The access policy was changed by someone else. Reload it before editing."
          : "The policy was not saved. Check the field configuration and try again.",
      );
    } finally {
      setSaving(false);
    }
  }

  function cancelEditing() {
    setRules(savedRules);
    setEditing(false);
    setError("");
  }

  if (!open) return null;
  return (
    <div className="drawer-backdrop">
      <aside
        className="data-source-settings"
        role="dialog"
        aria-modal="true"
        aria-label="Database governance"
      >
        <header className="drawer-header">
          <div>
            <span className="settings-index">GOVERN / POSTGRES</span>
            <h2>Database governance</h2>
            <p>
              Connections, field-level policies, and human approval boundaries.
            </p>
          </div>
          <button
            className="settings-close"
            onClick={onClose}
            aria-label="Close database governance"
          >
            ×
          </button>
        </header>
        {error && (
          <p className="settings-error" role="alert">
            {error}
          </p>
        )}
        {notice && <p className="settings-notice">{notice}</p>}
        <form
          className="settings-fields connection-editor"
          onSubmit={saveSource}
        >
          <div className="connection-editor-heading">
            <div>
              <span className="settings-index">
                {source ? "EDIT CONNECTION" : "NEW CONNECTION"}
              </span>
              <h3>
                {source
                  ? "Edit connection settings"
                  : "Add PostgreSQL connection"}
              </h3>
            </div>
            {source && (
              <div className="connection-editor-actions">
                <button
                  type="button"
                  className="delete-source-trigger"
                  onClick={() => setDeleteConfirmOpen(true)}
                >
                  Delete data source
                </button>
                <button type="button" onClick={beginNewSource}>
                  ＋ Add connection
                </button>
              </div>
            )}
          </div>
          <label>
            <span>Data source name</span>
            <input
              aria-label="Data source name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              required
            />
          </label>
          <label>
            <span>PostgreSQL DSN</span>
            <input
              aria-label="PostgreSQL DSN"
              type="password"
              autoComplete="off"
              value={dsn}
              onChange={(event) => setDsn(event.target.value)}
              required={!source}
              placeholder={
                source
                  ? "•••••••••••• (configured)"
                  : "postgresql://user:password@host:5432/database"
              }
            />
            {source && (
              <small className="credential-hint">
                <i aria-hidden="true">✓</i> DSN is stored in encrypted form.
                Leave blank to keep the current value, or enter a new address to
                replace it.
              </small>
            )}
          </label>
          <button type="submit" className="primary-action">
            {source ? "Save connection changes" : "Save and test connection"}
          </button>
        </form>
        {source && deleteConfirmOpen && (
          <section
            className="delete-source-confirmation"
            role="alertdialog"
            aria-labelledby="delete-source-title"
          >
            <div>
              <span className="settings-index">DELETE CONNECTION</span>
              <h3 id="delete-source-title">Delete {source.name}?</h3>
              <p>
                After deletion, no project will be able to use this connection.
                Existing query history and audit records will be retained.
              </p>
            </div>
            <div className="delete-source-actions">
              <button
                type="button"
                onClick={() => setDeleteConfirmOpen(false)}
                disabled={deleting}
              >
                Cancel
              </button>
              <button
                type="button"
                className="danger-action"
                onClick={() => void removeSource()}
                disabled={deleting}
              >
                {deleting ? "Deleting…" : "Confirm deletion"}
              </button>
            </div>
          </section>
        )}
        <section className="governed-sources">
          <h3>Connected data sources</h3>
          {sources.length ? (
            <div className="source-list">
              {sources.map((item) => (
                <button
                  type="button"
                  key={item.id}
                  onClick={() => void choose(item)}
                  className={source?.id === item.id ? "active" : ""}
                >
                  {item.name}
                  <small>policy v{item.policy_version}</small>
                </button>
              ))}
            </div>
          ) : (
            <p className="empty-note">
              Save a PostgreSQL data source before configuring table and field
              policies.
            </p>
          )}
        </section>
        {source && (
          <section className={`policy-editor ${editing ? "is-editing" : ""}`}>
            <div className="panel-heading">
              <div>
                <h3>{source.name} access policy</h3>
                <p className="policy-version-note">
                  Current v{source.policy_version} ·{" "}
                  {editing
                    ? "Editing; saving will create a new version"
                    : "Read-only preview"}
                </p>
              </div>
              {editing ? (
                <div className="policy-actions">
                  <button type="button" onClick={cancelEditing}>
                    Cancel
                  </button>
                  <button
                    type="button"
                    onClick={() => void savePolicies()}
                    disabled={!dirty || saving}
                    className="primary-action"
                  >
                    {saving ? "Saving…" : "Save changes"}
                  </button>
                </div>
              ) : (
                <button
                  type="button"
                  onClick={() => {
                    setEditing(true);
                    setNotice("");
                  }}
                  className="primary-action"
                >
                  Edit access policy
                </button>
              )}
            </div>
            <p className="policy-help">
              Changing table-level access updates every field in that table.
              Individual fields can still be adjusted afterward.
            </p>
            {schemaStatus === "loading" && (
              <div className="policy-schema-state" role="status">
                <span className="schema-spinner" />
                <div>
                  <strong>Loading table schema</strong>
                  <p>
                    Existing policies appear first; database fields will be
                    added after the connection completes.
                  </p>
                </div>
              </div>
            )}
            {(schemaStatus === "empty" || schemaStatus === "error") && (
              <div className="policy-schema-state is-warning" role="status">
                <div>
                  <strong>
                    {schemaStatus === "error"
                      ? "Unable to connect to the database right now"
                      : "The database returned no visible tables"}
                  </strong>
                  <p>
                    You can still edit the displayed rules or manually add
                    tables and fields to govern.
                  </p>
                </div>
                <button type="button" onClick={() => void choose(source)}>
                  Reload
                </button>
              </div>
            )}
            {editing &&
              (schemaStatus === "empty" || schemaStatus === "error") && (
                <form className="manual-policy-rule" onSubmit={addManualRule}>
                  <label>
                    <span>Table name</span>
                    <input
                      aria-label="Manually add table name"
                      value={manualTable}
                      onChange={(event) => setManualTable(event.target.value)}
                      placeholder="orders"
                      required
                    />
                  </label>
                  <label>
                    <span>Field name (optional)</span>
                    <input
                      aria-label="Manually add field name"
                      value={manualColumn}
                      onChange={(event) => setManualColumn(event.target.value)}
                      placeholder="customer_email"
                    />
                  </label>
                  <button type="submit">Add rule</button>
                </form>
              )}
            {Object.entries(schema).map(([table, columns]) => (
              <details key={table} open>
                <summary>
                  {table} <small>{columns.length} fields</small>
                </summary>
                <PolicySelect
                  label={`${table}.*`}
                  value={rules[policyKey(table, null)] ?? "denied"}
                  disabled={!editing}
                  onChange={(access) => changeTable(table, columns, access)}
                />
                {columns.map((column) => (
                  <PolicySelect
                    key={column}
                    label={`${table}.${column}`}
                    value={
                      rules[policyKey(table, column)] ??
                      rules[policyKey(table, null)] ??
                      "denied"
                    }
                    disabled={!editing}
                    onChange={(access) =>
                      setRules((current) => ({
                        ...current,
                        [policyKey(table, column)]: access,
                      }))
                    }
                  />
                ))}
              </details>
            ))}
          </section>
        )}
      </aside>
    </div>
  );
}

function schemaFromPolicies(rules: PolicyRule[]): Record<string, string[]> {
  const schema: Record<string, string[]> = {};
  for (const rule of rules) {
    const columns = schema[rule.table] ?? [];
    schema[rule.table] =
      rule.column && !columns.includes(rule.column)
        ? [...columns, rule.column]
        : columns;
  }
  return schema;
}

function mergeSchemas(
  first: Record<string, string[]>,
  second: Record<string, string[]>,
): Record<string, string[]> {
  const merged = { ...first };
  for (const [table, columns] of Object.entries(second)) {
    merged[table] = [...new Set([...(merged[table] ?? []), ...columns])];
  }
  return merged;
}

function hydrateRules(
  schema: Record<string, string[]>,
  policies: PolicyRule[],
): Record<string, AccessLevel> {
  const explicit = Object.fromEntries(
    policies.map((rule) => [policyKey(rule.table, rule.column), rule.access]),
  ) as Record<string, AccessLevel>;
  const hydrated: Record<string, AccessLevel> = {};
  for (const [table, columns] of Object.entries(schema)) {
    const tableAccess = explicit[policyKey(table, null)] ?? "denied";
    hydrated[policyKey(table, null)] = tableAccess;
    for (const column of columns)
      hydrated[policyKey(table, column)] =
        explicit[policyKey(table, column)] ?? tableAccess;
  }
  return hydrated;
}

function PolicySelect({
  label,
  value,
  disabled,
  onChange,
}: {
  label: string;
  value: AccessLevel;
  disabled: boolean;
  onChange(value: AccessLevel): void;
}) {
  return (
    <label className="policy-row">
      <span>{label}</span>
      <select
        aria-label={`${label} access`}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value as AccessLevel)}
      >
        <option value="allowed">Allow automatically</option>
        <option value="approval_required">Approval required</option>
        <option value="denied">Deny access</option>
      </select>
    </label>
  );
}
