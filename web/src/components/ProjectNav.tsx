import { FormEvent, useState } from "react";

import type { Artifact, ModelConfigurationStatus, Project, Task } from "../types";

interface ProjectNavProps {
  projects: Project[]; selectedProjectId: string | null; artifacts: Artifact[]; tasks: Task[];
  selectedTaskId: string | null; onSelectProject(id: string): void; onSelectTask(id: string): void;
  onStartNewTask(): void; onCreateProject(name: string): Promise<void>; onUpload(file: File): Promise<void>;
  modelConfiguration: ModelConfigurationStatus | null; onOpenModelSettings(): void;
  onOpenDataSources(): void; onOpenKnowledge(): void; drawerOpen: boolean; onClose(): void; onCollapse(): void;
}

export function ProjectNav({ projects, selectedProjectId, artifacts, tasks, selectedTaskId,
  onSelectProject, onSelectTask, onStartNewTask, onCreateProject, onUpload,
  modelConfiguration, onOpenModelSettings, onOpenDataSources, onOpenKnowledge, drawerOpen, onClose,
  onCollapse }: ProjectNavProps) {
  const [newProjectName, setNewProjectName] = useState("");
  const [creatingProject, setCreatingProject] = useState(false);
  const sourceDatasets = artifacts.filter((artifact) => artifact.artifact_type === "source_dataset");

  async function submitProject(event: FormEvent) {
    event.preventDefault(); const name = newProjectName.trim(); if (!name) return;
    await onCreateProject(name); setNewProjectName(""); setCreatingProject(false);
  }

  return <nav className={drawerOpen ? "project-rail drawer-open" : "project-rail"} aria-label="项目上下文">
    <header className="brand-block"><div className="brand-lockup"><span className="brand-orbit" aria-hidden="true"><i /></span>
      <div><strong>TRACE</strong><p>Evidence-first Analysis</p></div></div>
      <button type="button" className="rail-collapse" aria-label="收起项目上下文" onClick={onCollapse}>‹</button>
      <button type="button" className="rail-close" aria-label="关闭项目上下文" onClick={onClose}>×</button>
    </header>

    <section className="rail-section project-section"><div className="section-heading">
      <div className="section-label">项目 <span>{projects.length}</span></div>
      <button type="button" className="section-action" aria-label={creatingProject ? "取消新建项目" : "新建项目"}
        onClick={() => setCreatingProject((current) => !current)}>{creatingProject ? "取消" : "＋ 新建"}</button></div>
      {projects.length > 0 ? <ul className="project-list">{projects.map((project, index) => {
        const selected = project.id === selectedProjectId;
        return <li key={project.id}><button type="button" className={selected ? "project-link active" : "project-link"}
          aria-label={project.name} aria-current={selected ? "page" : undefined} onClick={() => onSelectProject(project.id)}>
          <span className="project-index">{String(index + 1).padStart(2, "0")}</span><span>{project.name}</span>
          <i aria-hidden="true">{selected ? "●" : "○"}</i></button></li>;
      })}</ul> : <p className="rail-empty">还没有项目，先建立一个分析容器。</p>}
      {creatingProject && <form className="compact-form" onSubmit={submitProject}><input autoFocus aria-label="新项目名称"
        value={newProjectName} placeholder="例如：Regional Growth Review" onChange={(event) => setNewProjectName(event.target.value)} />
        <button type="submit" aria-label="创建项目" disabled={!newProjectName.trim()}>创建</button></form>}
    </section>

    <section className="rail-section source-section"><div className="section-label">数据源 <span>3 TYPES</span></div>
      <div className="source-catalog">
        <button type="button" className="source-card database-source" onClick={onOpenDataSources}
          disabled={!selectedProjectId} aria-label="配置 PostgreSQL 数据源">
          <span className="source-icon database" aria-hidden="true">DB</span><span><strong>PostgreSQL</strong><small>连接、字段策略与审批</small></span><i>配置治理</i>
        </button>
        {sourceDatasets.map((artifact) => <article className="source-card loaded" key={artifact.id}>
          <span className="source-icon file" aria-hidden="true">CSV</span><div><strong title={artifact.title}>{artifact.title}</strong><small>上传文件</small></div><i>已加载</i>
        </article>)}
        {sourceDatasets.length === 0 && <article className="source-card empty"><span className="source-icon file" aria-hidden="true">FILE</span>
          <div><strong>CSV / Excel</strong><small>上传文件</small></div><i>尚未添加</i></article>}
        <button type="button" className="source-card knowledge-source" onClick={onOpenKnowledge}
          disabled={!selectedProjectId} aria-label="配置业务知识库"><span className="source-icon knowledge" aria-hidden="true">KB</span>
          <span><strong>Business Knowledge Base</strong><small>Knowledge Agent 业务口径</small></span><i>配置口径</i></button>
      </div>
      {selectedProjectId && <details className="source-add-menu"><summary>＋ 添加数据源</summary><div>
        <button type="button" onClick={onOpenDataSources}>连接 PostgreSQL <span>可用</span></button>
        <label>上传 CSV / Excel <span>可用</span><input type="file" accept=".csv,.xlsx" onChange={(event) => {
          const file = event.target.files?.[0]; if (file) void onUpload(file); event.target.value = "";
        }} /></label><button type="button" onClick={onOpenKnowledge}>连接 Business Knowledge Base <span>可用</span></button>
      </div></details>}
    </section>

    <section className="rail-section model-section"><div className="section-label">模型</div>
      <button type="button" className={`model-route ${modelConfiguration?.configured ? "configured" : "unconfigured"}`}
        aria-label={modelConfiguration?.configured ? "更改模型配置" : "配置模型"} onClick={onOpenModelSettings}>
        <span className="model-glyph" aria-hidden="true">AI</span><span><strong>{modelConfiguration?.configured ? modelConfiguration.model : "Configure model"}</strong>
          <small>{modelConfiguration?.configured ? modelConfiguration.providers.find((item) => item.provider === modelConfiguration.provider)?.label : "DeepSeek · OpenAI · Relay"}</small></span>
        <i>{modelConfiguration?.configured ? "READY" : "SETUP"}</i></button>
    </section>

    {selectedProjectId && <button type="button" className="new-analysis-button" onClick={onStartNewTask}><span aria-hidden="true">✦</span> 新建分析</button>}
    <section className="rail-section task-index"><div className="section-label">任务历史 <span>{tasks.length}</span></div><ul className="rail-list">
      {tasks.map((task) => <li key={task.id}><button className={selectedTaskId === task.id ? "task-link active" : "task-link"} onClick={() => onSelectTask(task.id)}>
        <span className={`status-pip ${task.status}`} /><span>{task.objective}</span></button></li>)}
      {tasks.length === 0 && <li className="rail-empty">从一个经营问题开始</li>}
    </ul></section>
  </nav>;
}
