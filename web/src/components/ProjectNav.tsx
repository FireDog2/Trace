import { FormEvent, useState } from "react";

import type { Artifact, ModelConfigurationStatus, Project, Task } from "../types";

interface ProjectNavProps {
  projects: Project[];
  selectedProjectId: string | null;
  artifacts: Artifact[];
  tasks: Task[];
  selectedTaskId: string | null;
  onSelectProject(id: string): void;
  onSelectTask(id: string): void;
  onCreateProject(name: string): Promise<void>;
  onUpload(file: File): Promise<void>;
  modelConfiguration: ModelConfigurationStatus | null;
  onOpenModelSettings(): void;
}

export function ProjectNav({
  projects,
  selectedProjectId,
  artifacts,
  tasks,
  selectedTaskId,
  onSelectProject,
  onSelectTask,
  onCreateProject,
  onUpload,
  modelConfiguration,
  onOpenModelSettings,
}: ProjectNavProps) {
  const [newProjectName, setNewProjectName] = useState("");
  const [creatingProject, setCreatingProject] = useState(false);

  async function submitProject(event: FormEvent) {
    event.preventDefault();
    const name = newProjectName.trim();
    if (!name) return;
    await onCreateProject(name);
    setNewProjectName("");
    setCreatingProject(false);
  }

  return (
    <nav className="project-rail" aria-label="分析项目">
      <header className="brand-block">
        <span className="brand-index">LOCAL ANALYSIS SYSTEM</span>
        <strong>TRACE</strong>
        <p>每个结论，都有来路。</p>
      </header>

      <button
        type="button"
        className={`model-route ${modelConfiguration?.configured ? "configured" : "unconfigured"}`}
        aria-label={modelConfiguration?.configured ? "更改模型配置" : "配置模型"}
        onClick={onOpenModelSettings}
      >
        <span className="model-route-label">
          <span>MODEL ROUTE</span>
          <i>{modelConfiguration?.configured ? "READY" : "SETUP"}</i>
        </span>
        <strong>{modelConfiguration?.configured ? modelConfiguration.model : "配置运行模型"}</strong>
        <small>
          {modelConfiguration?.configured
            ? modelConfiguration.providers.find((item) => item.provider === modelConfiguration.provider)?.label
            : "DeepSeek · OpenAI · 中转站"}
        </small>
      </button>

      <section className="rail-section project-section">
        <div className="section-heading">
          <div className="section-label">项目 <span>{projects.length}</span></div>
          <button
            type="button"
            className="section-action"
            aria-label={creatingProject ? "取消新建项目" : "新建项目"}
            onClick={() => setCreatingProject((current) => !current)}
          >
            {creatingProject ? "取消" : "＋ 新建"}
          </button>
        </div>
        {projects.length > 0 ? (
          <ul className="project-list">
            {projects.map((project, index) => {
              const selected = project.id === selectedProjectId;
              return (
                <li key={project.id}>
                  <button
                    type="button"
                    className={selected ? "project-link active" : "project-link"}
                    aria-label={project.name}
                    aria-current={selected ? "page" : undefined}
                    onClick={() => onSelectProject(project.id)}
                  >
                    <span className="project-index">{String(index + 1).padStart(2, "0")}</span>
                    <span>{project.name}</span>
                    <i aria-hidden="true">{selected ? "●" : "○"}</i>
                  </button>
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="rail-empty">还没有项目，先建立一个分析容器。</p>
        )}
        {creatingProject && (
          <form className="compact-form" onSubmit={submitProject}>
            <input
              autoFocus
              aria-label="新项目名称"
              value={newProjectName}
              placeholder="例如：2025 区域经营复盘"
              onChange={(event) => setNewProjectName(event.target.value)}
            />
            <button type="submit" aria-label="创建项目" disabled={!newProjectName.trim()}>创建</button>
          </form>
        )}
      </section>

      <section className="rail-section">
        <div className="section-label">
          数据 <span>{artifacts.filter((item) => item.artifact_type.includes("dataset")).length}</span>
        </div>
        <ul className="rail-list dataset-list">
          {artifacts
            .filter((artifact) => artifact.artifact_type.includes("dataset"))
            .map((artifact) => (
              <li key={artifact.id}>
                <span className="file-mark">CSV</span>
                <span title={artifact.title}>{artifact.title}</span>
              </li>
            ))}
          {artifacts.filter((artifact) => artifact.artifact_type.includes("dataset")).length === 0 && (
            <li className="rail-empty">尚未添加数据</li>
          )}
        </ul>
        {selectedProjectId && (
          <label className="upload-control">
            添加 CSV / Excel
            <input
              type="file"
              accept=".csv,.xlsx"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) void onUpload(file);
                event.target.value = "";
              }}
            />
          </label>
        )}
      </section>

      <section className="rail-section task-index">
        <div className="section-label">分析任务 <span>{tasks.length}</span></div>
        <ul className="rail-list">
          {tasks.map((task) => (
            <li key={task.id}>
              <button
                className={selectedTaskId === task.id ? "task-link active" : "task-link"}
                onClick={() => onSelectTask(task.id)}
              >
                <span className={`status-pip ${task.status}`} />
                <span>{task.objective}</span>
              </button>
            </li>
          ))}
          {tasks.length === 0 && <li className="rail-empty">从一个经营问题开始</li>}
        </ul>
      </section>
    </nav>
  );
}
