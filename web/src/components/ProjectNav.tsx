import { FormEvent, KeyboardEvent, useEffect, useState } from "react";

import type { ModelConfigurationStatus, Project, Task } from "../types";

interface ProjectNavProps {
  projects: Project[];
  selectedProjectId: string | null;
  tasks: Task[];
  selectedTaskId: string | null;
  onSelectProject(id: string): void;
  onSelectTask(id: string): void;
  onStartNewTask(): void;
  onCreateProject(name: string): Promise<void>;
  onRenameProject(id: string, name: string): Promise<void>;
  onPinTask(id: string, pinned: boolean): Promise<void>;
  onDeleteTask(id: string): Promise<void>;
  modelConfiguration: ModelConfigurationStatus | null;
  onOpenModelSettings(): void;
  onOpenDataSources(): void;
  onOpenKnowledge(): void;
  drawerOpen: boolean;
  onClose(): void;
  onCollapse(): void;
}

export function ProjectNav({
  projects,
  selectedProjectId,
  tasks,
  selectedTaskId,
  onSelectProject,
  onSelectTask,
  onStartNewTask,
  onCreateProject,
  onRenameProject,
  onPinTask,
  onDeleteTask,
  modelConfiguration,
  onOpenModelSettings,
  onOpenDataSources,
  onOpenKnowledge,
  drawerOpen,
  onClose,
  onCollapse,
}: ProjectNavProps) {
  const [newProjectName, setNewProjectName] = useState("");
  const [creatingProject, setCreatingProject] = useState(false);
  const [expandedProjectId, setExpandedProjectId] = useState<string | null>(
    null,
  );
  const [renamingProjectId, setRenamingProjectId] = useState<string | null>(
    null,
  );
  const [renameValue, setRenameValue] = useState("");
  const [openTaskMenuId, setOpenTaskMenuId] = useState<string | null>(null);
  const [deleteCandidate, setDeleteCandidate] = useState<Task | null>(null);
  const [deletingTask, setDeletingTask] = useState(false);

  useEffect(() => {
    if (
      expandedProjectId &&
      !projects.some((project) => project.id === expandedProjectId)
    ) {
      setExpandedProjectId(null);
    }
  }, [expandedProjectId, projects]);

  useEffect(() => {
    if (openTaskMenuId && !tasks.some((task) => task.id === openTaskMenuId)) {
      setOpenTaskMenuId(null);
    }
  }, [openTaskMenuId, tasks]);

  async function submitProject(event: FormEvent) {
    event.preventDefault();
    const name = newProjectName.trim();
    if (!name) return;
    await onCreateProject(name);
    setNewProjectName("");
    setCreatingProject(false);
  }

  function toggleProject(project: Project) {
    onSelectProject(project.id);
    setExpandedProjectId((current) =>
      current === project.id ? null : project.id,
    );
  }

  function beginRename(project: Project) {
    onSelectProject(project.id);
    setRenamingProjectId(project.id);
    setRenameValue(project.name);
  }

  async function saveRename(projectId: string) {
    const name = renameValue.trim();
    if (!name) return;
    await onRenameProject(projectId, name);
    setRenamingProjectId(null);
    setRenameValue("");
  }

  function handleRenameKey(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter") {
      event.preventDefault();
      event.currentTarget.blur();
    }
    if (event.key === "Escape") {
      setRenamingProjectId(null);
      setRenameValue("");
    }
  }

  async function togglePinned(task: Task) {
    try {
      await onPinTask(task.id, !task.pinned);
      setOpenTaskMenuId(null);
    } catch {
      // The parent displays the API error without losing the current menu context.
    }
  }

  async function confirmDelete() {
    if (!deleteCandidate) return;
    setDeletingTask(true);
    try {
      await onDeleteTask(deleteCandidate.id);
      setDeleteCandidate(null);
    } catch {
      // Keep the confirmation open so the user can retry after the parent reports the error.
    } finally {
      setDeletingTask(false);
    }
  }

  return (
    <nav
      className={drawerOpen ? "project-rail drawer-open" : "project-rail"}
      aria-label="任务台"
    >
      <header className="brand-block">
        <div className="brand-lockup">
          <span className="brand-orbit" aria-hidden="true">
            <i />
          </span>
          <div>
            <strong>TRACE</strong>
            <p>分析任务台</p>
          </div>
        </div>
        <button
          type="button"
          className="rail-collapse"
          aria-label="收起任务台"
          onClick={onCollapse}
        >
          ‹
        </button>
        <button
          type="button"
          className="rail-close"
          aria-label="关闭任务台"
          onClick={onClose}
        >
          ×
        </button>
      </header>

      <section className="rail-section model-section">
        <div className="section-label">模型配置</div>
        <button
          type="button"
          className={`model-route ${modelConfiguration?.configured ? "configured" : "unconfigured"}`}
          aria-label={
            modelConfiguration?.configured ? "更改模型配置" : "配置模型"
          }
          onClick={onOpenModelSettings}
        >
          <span className="model-glyph" aria-hidden="true">
            AI
          </span>
          <span>
            <strong>
              {modelConfiguration?.configured
                ? modelConfiguration.model
                : "配置分析模型"}
            </strong>
            <small>
              {modelConfiguration?.configured
                ? modelConfiguration.providers.find(
                    (item) => item.provider === modelConfiguration.provider,
                  )?.label
                : "DeepSeek · OpenAI · 中转站"}
            </small>
          </span>
          <i>{modelConfiguration?.configured ? "READY" : "SETUP"}</i>
        </button>
      </section>

      <section className="rail-section global-config-section">
        <div className="section-heading">
          <div className="section-label">全局配置</div>
          <span className="scope-badge">所有项目通用</span>
        </div>
        <div className="global-route-list">
          <button
            type="button"
            className="global-route"
            onClick={onOpenDataSources}
            aria-label="配置全局数据源"
          >
            <span className="source-icon database" aria-hidden="true">
              DB
            </span>
            <span>
              <strong>数据源</strong>
              <small>连接、字段范围与访问策略</small>
            </span>
            <i aria-hidden="true">›</i>
          </button>
          <button
            type="button"
            className="global-route"
            onClick={onOpenKnowledge}
            aria-label="配置全局业务口径"
          >
            <span className="source-icon knowledge" aria-hidden="true">
              KB
            </span>
            <span>
              <strong>业务口径</strong>
              <small>上传文档并自动分类沉淀</small>
            </span>
            <i aria-hidden="true">›</i>
          </button>
        </div>
      </section>

      <section className="rail-section project-section">
        <div className="section-heading">
          <div className="section-label">
            项目 <span>{projects.length}</span>
          </div>
          <button
            type="button"
            className="section-action"
            aria-label={creatingProject ? "取消新建项目" : "新建项目"}
            onClick={() => setCreatingProject((current) => !current)}
          >
            {creatingProject ? "取消" : "＋ 新建"}
          </button>
        </div>
        {creatingProject && (
          <form className="compact-form" onSubmit={submitProject}>
            <input
              autoFocus
              aria-label="新项目名称"
              value={newProjectName}
              placeholder="输入项目名称"
              onChange={(event) => setNewProjectName(event.target.value)}
            />
            <button
              type="submit"
              aria-label="创建项目"
              disabled={!newProjectName.trim()}
            >
              创建
            </button>
          </form>
        )}
        <p className="project-hint">单击切换项目，双击展开对话</p>
        {projects.length > 0 ? (
          <ul className="project-tree">
            {projects.map((project) => {
              const selected = project.id === selectedProjectId;
              const expanded = project.id === expandedProjectId;
              const renaming = project.id === renamingProjectId;
              return (
                <li
                  key={project.id}
                  className={
                    expanded ? "project-node expanded" : "project-node"
                  }
                >
                  <div
                    className={selected ? "project-row active" : "project-row"}
                  >
                    <button
                      type="button"
                      className="project-toggle"
                      aria-label={
                        expanded
                          ? `收起项目 ${project.name}`
                          : `展开项目 ${project.name}`
                      }
                      aria-expanded={expanded}
                      onClick={() => toggleProject(project)}
                    >
                      <span aria-hidden="true">{expanded ? "⌄" : "›"}</span>
                    </button>
                    {renaming ? (
                      <input
                        className="project-rename-input"
                        autoFocus
                        aria-label={`修改项目名称 ${project.name}`}
                        value={renameValue}
                        onChange={(event) => setRenameValue(event.target.value)}
                        onBlur={() => void saveRename(project.id)}
                        onKeyDown={handleRenameKey}
                      />
                    ) : (
                      <button
                        type="button"
                        className="project-name"
                        aria-current={selected ? "page" : undefined}
                        onClick={() => onSelectProject(project.id)}
                        onDoubleClick={() => toggleProject(project)}
                      >
                        {project.name}
                      </button>
                    )}
                    {!renaming && (
                      <button
                        type="button"
                        className="project-more"
                        aria-label={`重命名项目 ${project.name}`}
                        onClick={() => beginRename(project)}
                      >
                        •••
                      </button>
                    )}
                  </div>
                  {expanded && selected && (
                    <div className="conversation-tree">
                      <button
                        type="button"
                        className={
                          selectedTaskId
                            ? "new-conversation"
                            : "new-conversation active"
                        }
                        aria-current={selectedTaskId ? undefined : "page"}
                        onClick={onStartNewTask}
                      >
                        <span aria-hidden="true">＋</span> 新对话
                      </button>
                      <ul>
                        {tasks.map((task) => {
                          const deleteAllowed = [
                            "completed",
                            "failed",
                            "cancelled",
                          ].includes(task.status);
                          return (
                            <li
                              key={task.id}
                              className={
                                task.pinned ? "task-node pinned" : "task-node"
                              }
                            >
                              <button
                                type="button"
                                className={
                                  selectedTaskId === task.id
                                    ? "task-link active"
                                    : "task-link"
                                }
                                onClick={() => onSelectTask(task.id)}
                              >
                                <span className={`status-pip ${task.status}`} />
                                <span className="task-objective">
                                  {task.objective}
                                </span>
                                {task.pinned && (
                                  <span
                                    className="task-pin"
                                    aria-label="已置顶"
                                  >
                                    ◆
                                  </span>
                                )}
                              </button>
                              <div
                                className="task-menu-anchor"
                                onBlur={(event) => {
                                  if (
                                    !event.currentTarget.contains(
                                      event.relatedTarget,
                                    )
                                  ) {
                                    setOpenTaskMenuId(null);
                                  }
                                }}
                              >
                                <button
                                  type="button"
                                  className="task-more"
                                  aria-label={`对话操作 ${task.objective}`}
                                  aria-haspopup="menu"
                                  aria-expanded={openTaskMenuId === task.id}
                                  onClick={() =>
                                    setOpenTaskMenuId((current) =>
                                      current === task.id ? null : task.id,
                                    )
                                  }
                                >
                                  •••
                                </button>
                                {openTaskMenuId === task.id && (
                                  <div className="task-menu" role="menu">
                                    <button
                                      type="button"
                                      role="menuitem"
                                      onClick={() => void togglePinned(task)}
                                    >
                                      <span aria-hidden="true">◆</span>
                                      {task.pinned ? "取消置顶" : "置顶"}
                                    </button>
                                    <button
                                      type="button"
                                      role="menuitem"
                                      className="danger"
                                      disabled={!deleteAllowed}
                                      title={
                                        deleteAllowed
                                          ? undefined
                                          : "请等待分析完成后再删除"
                                      }
                                      onClick={() => {
                                        setOpenTaskMenuId(null);
                                        setDeleteCandidate(task);
                                      }}
                                    >
                                      <span aria-hidden="true">×</span>删除
                                    </button>
                                  </div>
                                )}
                              </div>
                            </li>
                          );
                        })}
                        {tasks.length === 0 && (
                          <li className="rail-empty">还没有对话</li>
                        )}
                      </ul>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="rail-empty">还没有项目，先建立一个分析空间。</p>
        )}
      </section>
      {deleteCandidate && (
        <div
          className="task-delete-backdrop"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget && !deletingTask) {
              setDeleteCandidate(null);
            }
          }}
        >
          <section
            className="task-delete-confirmation"
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="delete-task-title"
            onKeyDown={(event) => {
              if (event.key === "Escape" && !deletingTask) {
                setDeleteCandidate(null);
              }
            }}
          >
            <span>DELETE CONVERSATION</span>
            <h2 id="delete-task-title">删除这条对话？</h2>
            <p>{deleteCandidate.objective}</p>
            <small>对话将从任务列表移除；源数据和审计记录会保留。</small>
            <div>
              <button
                type="button"
                autoFocus
                disabled={deletingTask}
                onClick={() => setDeleteCandidate(null)}
              >
                取消
              </button>
              <button
                type="button"
                className="danger"
                disabled={deletingTask}
                onClick={() => void confirmDelete()}
              >
                {deletingTask ? "删除中…" : "确认删除"}
              </button>
            </div>
          </section>
        </div>
      )}
    </nav>
  );
}
