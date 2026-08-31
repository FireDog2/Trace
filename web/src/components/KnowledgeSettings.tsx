import { ChangeEvent, FormEvent, useEffect, useState } from "react";

import type { ApiClient } from "../api";
import type {
  BusinessKnowledgeEntry,
  BusinessKnowledgeKind,
  SaveBusinessKnowledge,
} from "../types";

const EMPTY_FORM: SaveBusinessKnowledge = {
  kind: "metric",
  name: "",
  definition: "",
  formula: "",
  dimensions: [],
  synonyms: [],
  source: "",
};

const KIND_LABELS: Record<BusinessKnowledgeKind, string> = {
  metric: "Metric",
  term: "Term",
  rule: "Business rule",
};

export function KnowledgeSettings({
  open,
  api,
  onClose,
}: {
  open: boolean;
  api: ApiClient;
  onClose(): void;
}) {
  const [entries, setEntries] = useState<BusinessKnowledgeEntry[]>([]);
  const [mode, setMode] = useState<"document" | "manual">("document");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<SaveBusinessKnowledge>(EMPTY_FORM);
  const [dimensionsText, setDimensionsText] = useState("");
  const [synonymsText, setSynonymsText] = useState("");
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    if (!open) return;
    setError("");
    setNotice("");
    setMode("document");
    void api
      .listBusinessKnowledge()
      .then(setEntries)
      .catch(() =>
        setError(
          "Unable to load business knowledge. Confirm that the project is still available.",
        ),
      );
  }, [api, open]);

  function resetForm() {
    setEditingId(null);
    setForm(EMPTY_FORM);
    setDimensionsText("");
    setSynonymsText("");
  }

  function edit(entry: BusinessKnowledgeEntry) {
    setMode("manual");
    setEditingId(entry.id);
    setForm({
      kind: entry.kind,
      name: entry.name,
      definition: entry.definition,
      formula: entry.formula ?? "",
      dimensions: entry.dimensions,
      synonyms: entry.synonyms,
      source: entry.source ?? "",
    });
    setDimensionsText(entry.dimensions.join(", "));
    setSynonymsText(entry.synonyms.join(", "));
    setNotice("");
    setError("");
  }

  async function importDocument(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setImporting(true);
    setError("");
    setNotice("");
    try {
      const result = await api.importBusinessKnowledge(file);
      setEntries((current) =>
        [...current, ...result.created]
          .filter(
            (entry, index, values) =>
              values.findIndex((item) => item.id === entry.id) === index,
          )
          .sort((a, b) => a.name.localeCompare(b.name)),
      );
      const categories = [
        result.category_counts.metric
          ? `${result.category_counts.metric} ${result.category_counts.metric === 1 ? "metric" : "metrics"}`
          : "",
        result.category_counts.term
          ? `${result.category_counts.term} ${result.category_counts.term === 1 ? "term" : "terms"}`
          : "",
        result.category_counts.rule
          ? `${result.category_counts.rule} ${result.category_counts.rule === 1 ? "business rule" : "business rules"}`
          : "",
      ]
        .filter(Boolean)
        .join(", ");
      const skipped = result.skipped_names.length
        ? `; ${result.skipped_names.length} duplicate entries skipped`
        : "";
      setNotice(
        result.created.length
          ? `${result.file_name} imported: ${categories}${skipped}.`
          : `${result.file_name} did not add any new content${skipped}.`,
      );
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Document import failed. Check the file format and try again.",
      );
    } finally {
      setImporting(false);
    }
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    const input: SaveBusinessKnowledge = {
      ...form,
      formula: form.formula?.trim() || null,
      source: form.source?.trim() || null,
      dimensions: splitValues(dimensionsText),
      synonyms: splitValues(synonymsText),
    };
    try {
      const saved = editingId
        ? await api.updateBusinessKnowledge(editingId, input)
        : await api.createBusinessKnowledge(input);
      setEntries((current) =>
        editingId
          ? current.map((item) => (item.id === saved.id ? saved : item))
          : [...current, saved].sort((a, b) => a.name.localeCompare(b.name)),
      );
      setNotice(
        editingId
          ? `Saved ${saved.name} v${saved.version}.`
          : `Added ${saved.name}.`,
      );
      resetForm();
      setError("");
      setMode("document");
    } catch {
      setError(
        "Business knowledge was not saved. Check for a duplicate name and make sure all required fields are complete.",
      );
    }
  }

  async function remove(entry: BusinessKnowledgeEntry) {
    try {
      await api.deleteBusinessKnowledge(entry.id);
      setEntries((current) => current.filter((item) => item.id !== entry.id));
      if (editingId === entry.id) resetForm();
      setNotice(`Deleted ${entry.name}.`);
      setError("");
    } catch {
      setError(
        "Unable to delete this business knowledge entry. Refresh and try again.",
      );
    }
  }

  if (!open) return null;
  return (
    <div className="drawer-backdrop">
      <aside
        className="data-source-settings knowledge-settings"
        role="dialog"
        aria-modal="true"
        aria-label="Business knowledge base"
      >
        <header className="drawer-header">
          <div>
            <span className="settings-index">GROUND / BUSINESS</span>
            <h2>Business knowledge base</h2>
            <p>
              Upload an existing document and the system will split and classify
              its metrics, terms, and rules.
            </p>
          </div>
          <button
            className="settings-close"
            onClick={onClose}
            aria-label="Close business knowledge base"
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
        <div
          className="knowledge-mode-switch"
          role="tablist"
          aria-label="Knowledge entry method"
        >
          <button
            type="button"
            role="tab"
            aria-selected={mode === "document"}
            onClick={() => setMode("document")}
          >
            Upload document
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === "manual"}
            onClick={() => setMode("manual")}
          >
            Add manually
          </button>
        </div>
        {mode === "document" ? (
          <section
            className="knowledge-import"
            aria-label="Upload business document"
          >
            <div className="document-mark" aria-hidden="true">
              <span>DOC</span>
              <i />
            </div>
            <div>
              <span className="section-label">DOCUMENT INTAKE</span>
              <h3>Give the system an existing definitions document</h3>
              <p>
                Document sections and fields are detected automatically and
                classified as metrics, terms, or business rules. Duplicate names
                are skipped and existing definitions are never overwritten.
              </p>
            </div>
            <label
              className={`knowledge-file-button ${importing ? "is-busy" : ""}`}
            >
              <input
                type="file"
                aria-label="Choose a business definitions document"
                accept=".pdf,.docx,.md,.txt,.csv,.xlsx"
                disabled={importing}
                onChange={(event) => void importDocument(event)}
              />
              <span>{importing ? "Parsing…" : "Choose document"}</span>
              <small>
                PDF · Word · Markdown · TXT · CSV · Excel, up to 10 MB
              </small>
            </label>
          </section>
        ) : (
          <form className="knowledge-form" onSubmit={save}>
            <div className="knowledge-form-heading">
              <div>
                <span>{editingId ? "EDIT CONTRACT" : "NEW CONTRACT"}</span>
                <h3>
                  {editingId
                    ? "Update business definition"
                    : "Add business definition"}
                </h3>
              </div>
              {editingId && (
                <button
                  type="button"
                  onClick={() => {
                    resetForm();
                    setMode("document");
                  }}
                >
                  Cancel editing
                </button>
              )}
            </div>
            <div className="knowledge-field-grid">
              <label>
                <span>Type</span>
                <select
                  aria-label="Knowledge type"
                  value={form.kind}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      kind: event.target.value as BusinessKnowledgeKind,
                    }))
                  }
                >
                  <option value="metric">Metric</option>
                  <option value="term">Term</option>
                  <option value="rule">Business rule</option>
                </select>
              </label>
              <label>
                <span>Name</span>
                <input
                  aria-label="Knowledge name"
                  required
                  value={form.name}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      name: event.target.value,
                    }))
                  }
                  placeholder="For example: Operating profit"
                />
              </label>
            </div>
            <label>
              <span>Definition</span>
              <textarea
                aria-label="Knowledge definition"
                required
                value={form.definition}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    definition: event.target.value,
                  }))
                }
                placeholder="Describe the unique meaning of this metric or term in the current project."
              />
            </label>
            <label>
              <span>Formula</span>
              <input
                aria-label="Knowledge formula"
                value={form.formula ?? ""}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    formula: event.target.value,
                  }))
                }
                placeholder="revenue - logistics_cost - operating_cost"
              />
            </label>
            <div className="knowledge-field-grid">
              <label>
                <span>Applicable dimensions</span>
                <input
                  aria-label="Applicable dimensions"
                  value={dimensionsText}
                  onChange={(event) => setDimensionsText(event.target.value)}
                  placeholder="Month, Region"
                />
              </label>
              <label>
                <span>Synonyms</span>
                <input
                  aria-label="Knowledge synonyms"
                  value={synonymsText}
                  onChange={(event) => setSynonymsText(event.target.value)}
                  placeholder="Operating profit, Operating income"
                />
              </label>
            </div>
            <label>
              <span>Source</span>
              <input
                aria-label="Knowledge source"
                value={form.source ?? ""}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    source: event.target.value,
                  }))
                }
                placeholder="Finance definitions handbook / owner / document link"
              />
            </label>
            <button type="submit" className="primary-action">
              {editingId ? "Save new version" : "Add to knowledge base"}
            </button>
          </form>
        )}
        <section className="knowledge-index">
          <div className="panel-heading">
            <div>
              <span className="section-label">CATALOG</span>
              <h3>Registered definitions</h3>
            </div>
            <span>{entries.length} ENTRIES</span>
          </div>
          {entries.length ? (
            <div className="knowledge-card-list">
              {entries.map((entry) => (
                <article key={entry.id}>
                  <header>
                    <span className={`knowledge-kind ${entry.kind}`}>
                      {KIND_LABELS[entry.kind]}
                    </span>
                    <code>v{entry.version}</code>
                  </header>
                  <h4>{entry.name}</h4>
                  <p>{entry.definition}</p>
                  {entry.formula && (
                    <div className="knowledge-formula">
                      <span>ƒ(x)</span>
                      <code>{entry.formula}</code>
                    </div>
                  )}
                  <footer>
                    <small>{entry.source || "Source not specified"}</small>
                    <div>
                      <button type="button" onClick={() => edit(entry)}>
                        Edit
                      </button>
                      <button
                        type="button"
                        className="danger-action"
                        onClick={() => void remove(entry)}
                      >
                        Delete
                      </button>
                    </div>
                  </footer>
                </article>
              ))}
            </div>
          ) : (
            <p className="empty-note">
              Upload an existing definitions document. The knowledge agent will
              retrieve the imported content before analysis.
            </p>
          )}
        </section>
      </aside>
    </div>
  );
}

function splitValues(value: string): string[] {
  return [
    ...new Set(
      value
        .split(/[,，\n]/)
        .map((item) => item.trim())
        .filter(Boolean),
    ),
  ];
}
