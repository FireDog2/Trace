import { ChangeEvent, FormEvent, useEffect, useState } from "react";

import type { ApiClient } from "../api";
import type {
  BusinessKnowledgeEntry,
  BusinessKnowledgeKind,
  SaveBusinessKnowledge,
} from "../types";

const EMPTY_FORM: SaveBusinessKnowledge = {
  kind: "metric", name: "", definition: "", formula: "", dimensions: [], synonyms: [], source: "",
};

const KIND_LABELS: Record<BusinessKnowledgeKind, string> = {
  metric: "指标", term: "术语", rule: "业务规则",
};

export function KnowledgeSettings({ open, api, onClose }: {
  open: boolean; api: ApiClient; onClose(): void;
}) {
  const [entries, setEntries] = useState<BusinessKnowledgeEntry[]>([]);
  const [mode, setMode] = useState<"document" | "manual">("document");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<SaveBusinessKnowledge>(EMPTY_FORM);
  const [dimensionsText, setDimensionsText] = useState(""); const [synonymsText, setSynonymsText] = useState("");
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState(""); const [notice, setNotice] = useState("");

  useEffect(() => {
    if (!open) return;
    setError(""); setNotice(""); setMode("document");
    void api.listBusinessKnowledge().then(setEntries)
      .catch(() => setError("无法读取业务知识。请确认项目仍然可用。"));
  }, [api, open]);

  function resetForm() {
    setEditingId(null); setForm(EMPTY_FORM); setDimensionsText(""); setSynonymsText("");
  }

  function edit(entry: BusinessKnowledgeEntry) {
    setMode("manual"); setEditingId(entry.id);
    setForm({ kind: entry.kind, name: entry.name, definition: entry.definition, formula: entry.formula ?? "",
      dimensions: entry.dimensions, synonyms: entry.synonyms, source: entry.source ?? "" });
    setDimensionsText(entry.dimensions.join(", ")); setSynonymsText(entry.synonyms.join(", "));
    setNotice(""); setError("");
  }

  async function importDocument(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]; event.target.value = "";
    if (!file) return;
    setImporting(true); setError(""); setNotice("");
    try {
      const result = await api.importBusinessKnowledge(file);
      setEntries((current) => [...current, ...result.created]
        .filter((entry, index, values) => values.findIndex((item) => item.id === entry.id) === index)
        .sort((a, b) => a.name.localeCompare(b.name)));
      const categories = [
        result.category_counts.metric ? `${result.category_counts.metric} 个指标` : "",
        result.category_counts.term ? `${result.category_counts.term} 个术语` : "",
        result.category_counts.rule ? `${result.category_counts.rule} 条规则` : "",
      ].filter(Boolean).join("、");
      const skipped = result.skipped_names.length ? `；${result.skipped_names.length} 个重名条目已跳过` : "";
      setNotice(result.created.length
        ? `${result.file_name} 已导入：${categories}${skipped}。`
        : `${result.file_name} 没有新增内容${skipped}。`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "文档导入失败，请检查文件格式后重试。");
    } finally { setImporting(false); }
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    const input: SaveBusinessKnowledge = { ...form, formula: form.formula?.trim() || null,
      source: form.source?.trim() || null, dimensions: splitValues(dimensionsText), synonyms: splitValues(synonymsText) };
    try {
      const saved = editingId ? await api.updateBusinessKnowledge(editingId, input) : await api.createBusinessKnowledge(input);
      setEntries((current) => editingId ? current.map((item) => item.id === saved.id ? saved : item)
        : [...current, saved].sort((a, b) => a.name.localeCompare(b.name)));
      setNotice(editingId ? `已保存 ${saved.name} v${saved.version}。` : `已添加 ${saved.name}。`);
      resetForm(); setError(""); setMode("document");
    } catch { setError("业务知识未保存。请检查名称是否重复，以及必填字段是否完整。"); }
  }

  async function remove(entry: BusinessKnowledgeEntry) {
    try {
      await api.deleteBusinessKnowledge(entry.id);
      setEntries((current) => current.filter((item) => item.id !== entry.id));
      if (editingId === entry.id) resetForm();
      setNotice(`已删除 ${entry.name}。`); setError("");
    } catch { setError("无法删除这条业务知识，请刷新后重试。"); }
  }

  if (!open) return null;
  return <div className="drawer-backdrop"><aside className="data-source-settings knowledge-settings" role="dialog" aria-modal="true" aria-label="业务知识库">
    <header className="drawer-header"><div><span className="settings-index">GROUND / BUSINESS</span>
      <h2>业务知识库</h2><p>上传已有文档，系统会拆分并归类其中的指标、术语和规则。</p></div>
      <button className="settings-close" onClick={onClose} aria-label="关闭业务知识库">×</button></header>
    {error && <p className="settings-error" role="alert">{error}</p>}{notice && <p className="settings-notice">{notice}</p>}
    <div className="knowledge-mode-switch" role="tablist" aria-label="知识录入方式">
      <button type="button" role="tab" aria-selected={mode === "document"} onClick={() => setMode("document")}>上传文档</button>
      <button type="button" role="tab" aria-selected={mode === "manual"} onClick={() => setMode("manual")}>逐条添加</button>
    </div>
    {mode === "document" ? <section className="knowledge-import" aria-label="上传业务文档">
      <div className="document-mark" aria-hidden="true"><span>DOC</span><i /></div>
      <div><span className="section-label">DOCUMENT INTAKE</span><h3>把现有口径文档直接交给系统</h3>
        <p>自动识别文档章节和字段，归类为指标、术语或业务规则；重名内容会跳过，不会覆盖已有口径。</p></div>
      <label className={`knowledge-file-button ${importing ? "is-busy" : ""}`}>
        <input type="file" aria-label="选择业务口径文档" accept=".pdf,.docx,.md,.txt,.csv,.xlsx" disabled={importing} onChange={(event) => void importDocument(event)} />
        <span>{importing ? "正在解析…" : "选择文档"}</span><small>PDF · Word · Markdown · TXT · CSV · Excel，最大 10 MB</small>
      </label>
    </section> : <form className="knowledge-form" onSubmit={save}>
      <div className="knowledge-form-heading"><div><span>{editingId ? "EDIT CONTRACT" : "NEW CONTRACT"}</span>
        <h3>{editingId ? "更新业务口径" : "添加业务口径"}</h3></div>
        {editingId && <button type="button" onClick={() => { resetForm(); setMode("document"); }}>取消编辑</button>}</div>
      <div className="knowledge-field-grid"><label><span>类型</span><select aria-label="知识类型" value={form.kind}
        onChange={(event) => setForm((current) => ({ ...current, kind: event.target.value as BusinessKnowledgeKind }))}>
        <option value="metric">指标</option><option value="term">术语</option><option value="rule">业务规则</option></select></label>
        <label><span>名称</span><input aria-label="知识名称" required value={form.name}
          onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))} placeholder="例如：经营利润" /></label></div>
      <label><span>定义</span><textarea aria-label="知识定义" required value={form.definition}
        onChange={(event) => setForm((current) => ({ ...current, definition: event.target.value }))}
        placeholder="说明这个指标或术语在当前项目中的唯一含义。" /></label>
      <label><span>公式</span><input aria-label="知识公式" value={form.formula ?? ""}
        onChange={(event) => setForm((current) => ({ ...current, formula: event.target.value }))}
        placeholder="revenue - logistics_cost - operating_cost" /></label>
      <div className="knowledge-field-grid"><label><span>适用维度</span><input aria-label="适用维度" value={dimensionsText}
        onChange={(event) => setDimensionsText(event.target.value)} placeholder="月份, 地区" /></label>
        <label><span>同义词</span><input aria-label="知识同义词" value={synonymsText}
          onChange={(event) => setSynonymsText(event.target.value)} placeholder="营业利润, Operating income" /></label></div>
      <label><span>来源</span><input aria-label="知识来源" value={form.source ?? ""}
        onChange={(event) => setForm((current) => ({ ...current, source: event.target.value }))}
        placeholder="财务口径手册 / 负责人 / 文档链接" /></label>
      <button type="submit" className="primary-action">{editingId ? "保存新版本" : "添加到知识库"}</button>
    </form>}
    <section className="knowledge-index"><div className="panel-heading"><div><span className="section-label">CATALOG</span><h3>已登记口径</h3></div><span>{entries.length} ENTRIES</span></div>
      {entries.length ? <div className="knowledge-card-list">{entries.map((entry) => <article key={entry.id}>
        <header><span className={`knowledge-kind ${entry.kind}`}>{KIND_LABELS[entry.kind]}</span><code>v{entry.version}</code></header>
        <h4>{entry.name}</h4><p>{entry.definition}</p>{entry.formula && <div className="knowledge-formula"><span>ƒ(x)</span><code>{entry.formula}</code></div>}
        <footer><small>{entry.source || "未注明来源"}</small><div><button type="button" onClick={() => edit(entry)}>编辑</button>
          <button type="button" className="danger-action" onClick={() => void remove(entry)}>删除</button></div></footer>
      </article>)}</div> : <p className="empty-note">上传一份现有口径文档，Knowledge Agent 会在分析前检索导入的内容。</p>}
    </section>
  </aside></div>;
}

function splitValues(value: string): string[] {
  return [...new Set(value.split(/[,，\n]/).map((item) => item.trim()).filter(Boolean))];
}
