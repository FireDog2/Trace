import { FormEvent, useState } from "react";

import type { Artifact, Project, Task } from "../types";

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
}: ProjectNavProps) {
  const [newProjectName, setNewProjectName] = useState("");

  async function submitProject(event: FormEvent) {
    event.preventDefault();
    const name = newProjectName.trim();
    if (!name) return;
    await onCreateProject(name);
    setNewProjectName("");
  }

  return (
    <nav className="project-rail" aria-label="分析项目">
      <header className="brand-block">
        <span className="brand-index">TRACE / LOCAL</span>
        <strong>迹析</strong>
        <p>每个结论，都有来路。</p>
      </header>

      <section className="rail-section">
        <div className="section-label">项目</div>
        {projects.length > 0 && (
          <select
            aria-label="当前项目"
            value={selectedProjectId ?? ""}
            onChange={(event) => onSelectProject(event.target.value)}
          >
            {projects.map((project) => (
              <option key={project.id} value={project.id}>
                {project.name}
              </option>
            ))}
          </select>
        )}
        <form className="compact-form" onSubmit={submitProject}>
          <input
            aria-label="新项目名称"
            value={newProjectName}
            placeholder="新建分析项目"
            onChange={(event) => setNewProjectName(event.target.value)}
          />
          <button type="submit" aria-label="创建项目">＋</button>
        </form>
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
        </ul>
      </section>
    </nav>
  );
}
