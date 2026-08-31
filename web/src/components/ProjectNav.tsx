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
  onDeleteProject(id: string): Promise<void>;
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
  onDeleteProject,
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
  const [openProjectMenuId, setOpenProjectMenuId] = useState<string | null>(
    null,
  );
  const [openTaskMenuId, setOpenTaskMenuId] = useState<string | null>(null);
  const [projectDeleteCandidate, setProjectDeleteCandidate] =
    useState<Project | null>(null);
  const [deletingProject, setDeletingProject] = useState(false);
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
    if (
      openProjectMenuId &&
      !projects.some((project) => project.id === openProjectMenuId)
    ) {
      setOpenProjectMenuId(null);
    }
  }, [openProjectMenuId, projects]);

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
    setOpenProjectMenuId(null);
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

  async function confirmProjectDelete() {
    if (!projectDeleteCandidate) return;
    setDeletingProject(true);
    try {
      await onDeleteProject(projectDeleteCandidate.id);
      setProjectDeleteCandidate(null);
    } catch {
      // Keep the confirmation open so the user can retry after the parent reports the error.
    } finally {
      setDeletingProject(false);
    }
  }

  return (
    <nav
      className={drawerOpen ? "project-rail drawer-open" : "project-rail"}
      aria-label="Workbench"
    >
      <header className="brand-block">
        <div className="brand-lockup">
          <span className="brand-orbit" aria-hidden="true">
            <i />
          </span>
          <div>
            <strong>TRACE</strong>
            <p>Analysis Workbench</p>
          </div>
        </div>
        <button
          type="button"
          className="rail-collapse"
          aria-label="Collapse workbench"
          onClick={onCollapse}
        >
          ‹
        </button>
        <button
          type="button"
          className="rail-close"
          aria-label="Close workbench"
          onClick={onClose}
        >
          ×
        </button>
      </header>

      <section className="rail-section model-section">
        <div className="section-label">Model configuration</div>
        <button
          type="button"
          className={`model-route ${modelConfiguration?.configured ? "configured" : "unconfigured"}`}
          aria-label={
            modelConfiguration?.configured
              ? "Change model configuration"
              : "Configure model"
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
                : "Configure analysis model"}
            </strong>
            <small>
              {modelConfiguration?.configured
                ? modelConfiguration.providers.find(
                    (item) => item.provider === modelConfiguration.provider,
                  )?.label
                : "DeepSeek · OpenAI · Custom gateway"}
            </small>
          </span>
          <i>{modelConfiguration?.configured ? "READY" : "SETUP"}</i>
        </button>
      </section>

      <section className="rail-section global-config-section">
        <div className="section-heading">
          <div className="section-label">Global settings</div>
          <span className="scope-badge">Shared by all projects</span>
        </div>
        <div className="global-route-list">
          <button
            type="button"
            className="global-route"
            onClick={onOpenDataSources}
            aria-label="Configure global data sources"
          >
            <span className="source-icon database" aria-hidden="true">
              DB
            </span>
            <span>
              <strong>Data sources</strong>
              <small>Connections, field scope, and access policies</small>
            </span>
            <i aria-hidden="true">›</i>
          </button>
          <button
            type="button"
            className="global-route"
            onClick={onOpenKnowledge}
            aria-label="Configure global business definitions"
          >
            <span className="source-icon knowledge" aria-hidden="true">
              KB
            </span>
            <span>
              <strong>Business definitions</strong>
              <small>Upload documents for automatic classification</small>
            </span>
            <i aria-hidden="true">›</i>
          </button>
        </div>
      </section>

      <section className="rail-section project-section">
        <div className="section-heading">
          <div className="section-label">
            Projects <span>{projects.length}</span>
          </div>
          <button
            type="button"
            className="section-action"
            aria-label={creatingProject ? "Cancel new project" : "New project"}
            onClick={() => setCreatingProject((current) => !current)}
          >
            {creatingProject ? "Cancel" : "＋ New"}
          </button>
        </div>
        {creatingProject && (
          <form className="compact-form" onSubmit={submitProject}>
            <input
              autoFocus
              aria-label="New project name"
              value={newProjectName}
              placeholder="Enter a project name"
              onChange={(event) => setNewProjectName(event.target.value)}
            />
            <button
              type="submit"
              aria-label="Create project"
              disabled={!newProjectName.trim()}
            >
              Create
            </button>
          </form>
        )}
        <p className="project-hint">
          Click to switch projects; double-click to expand conversations
        </p>
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
                          ? `Collapse project ${project.name}`
                          : `Expand project ${project.name}`
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
                        aria-label={`Rename project ${project.name}`}
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
                      <div
                        className="project-menu-anchor"
                        onBlur={(event) => {
                          if (
                            !event.currentTarget.contains(event.relatedTarget)
                          ) {
                            setOpenProjectMenuId(null);
                          }
                        }}
                      >
                        <button
                          type="button"
                          className="project-more"
                          aria-label={`Project actions for ${project.name}`}
                          aria-haspopup="menu"
                          aria-expanded={openProjectMenuId === project.id}
                          onClick={() =>
                            setOpenProjectMenuId((current) =>
                              current === project.id ? null : project.id,
                            )
                          }
                        >
                          •••
                        </button>
                        {openProjectMenuId === project.id && (
                          <div className="task-menu project-menu" role="menu">
                            <button
                              type="button"
                              role="menuitem"
                              onClick={() => beginRename(project)}
                            >
                              <span aria-hidden="true">✎</span>Rename
                            </button>
                            <button
                              type="button"
                              role="menuitem"
                              className="danger"
                              onClick={() => {
                                setOpenProjectMenuId(null);
                                setProjectDeleteCandidate(project);
                              }}
                            >
                              <span aria-hidden="true">×</span>Delete project
                            </button>
                          </div>
                        )}
                      </div>
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
                        <span aria-hidden="true">＋</span> New conversation
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
                                    aria-label="Pinned"
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
                                  aria-label={`Conversation actions for ${task.objective}`}
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
                                      {task.pinned ? "Unpin" : "Pin"}
                                    </button>
                                    <button
                                      type="button"
                                      role="menuitem"
                                      className="danger"
                                      disabled={!deleteAllowed}
                                      title={
                                        deleteAllowed
                                          ? undefined
                                          : "Wait for the analysis to finish before deleting"
                                      }
                                      onClick={() => {
                                        setOpenTaskMenuId(null);
                                        setDeleteCandidate(task);
                                      }}
                                    >
                                      <span aria-hidden="true">×</span>Delete
                                    </button>
                                  </div>
                                )}
                              </div>
                            </li>
                          );
                        })}
                        {tasks.length === 0 && (
                          <li className="rail-empty">No conversations yet</li>
                        )}
                      </ul>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="rail-empty">
            No projects yet. Create an analysis workspace to get started.
          </p>
        )}
      </section>
      {projectDeleteCandidate && (
        <div
          className="task-delete-backdrop"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget && !deletingProject) {
              setProjectDeleteCandidate(null);
            }
          }}
        >
          <section
            className="task-delete-confirmation"
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="delete-project-title"
            onKeyDown={(event) => {
              if (event.key === "Escape" && !deletingProject) {
                setProjectDeleteCandidate(null);
              }
            }}
          >
            <span>DELETE PROJECT</span>
            <h2 id="delete-project-title">Permanently delete this project?</h2>
            <p>{projectDeleteCandidate.name}</p>
            <small>
              All conversations, uploaded datasets, and analysis artifacts in
              this project will be permanently deleted. This action cannot be
              undone.
            </small>
            <div>
              <button
                type="button"
                autoFocus
                disabled={deletingProject}
                onClick={() => setProjectDeleteCandidate(null)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="danger"
                disabled={deletingProject}
                onClick={() => void confirmProjectDelete()}
              >
                {deletingProject ? "Deleting…" : "Delete permanently"}
              </button>
            </div>
          </section>
        </div>
      )}
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
            <h2 id="delete-task-title">Delete this conversation?</h2>
            <p>{deleteCandidate.objective}</p>
            <small>
              The conversation will be removed from the task list; source data
              and audit records will be retained.
            </small>
            <div>
              <button
                type="button"
                autoFocus
                disabled={deletingTask}
                onClick={() => setDeleteCandidate(null)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="danger"
                disabled={deletingTask}
                onClick={() => void confirmDelete()}
              >
                {deletingTask ? "Deleting…" : "Confirm deletion"}
              </button>
            </div>
          </section>
        </div>
      )}
    </nav>
  );
}
