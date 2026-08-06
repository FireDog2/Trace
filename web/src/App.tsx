import { FormEvent, useEffect, useMemo, useState } from "react";

import { HttpApiClient, type ApiClient } from "./api";
import { Conversation } from "./components/Conversation";
import { ModelSettings } from "./components/ModelSettings";
import { ProjectNav } from "./components/ProjectNav";
import { TaskWorkbench } from "./components/TaskWorkbench";
import type {
  Artifact,
  ModelConfigurationStatus,
  Project,
  SaveModelConfiguration,
  Task,
  TaskDetail,
  TaskEvent,
} from "./types";

interface AppProps {
  api?: ApiClient;
}

export function App({ api: suppliedApi }: AppProps) {
  const api = useMemo(() => suppliedApi ?? new HttpApiClient(), [suppliedApi]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [task, setTask] = useState<TaskDetail | null>(null);
  const [events, setEvents] = useState<TaskEvent[]>([]);
  const [objective, setObjective] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [modelConfiguration, setModelConfiguration] = useState<ModelConfigurationStatus | null>(null);
  const [modelSettingsOpen, setModelSettingsOpen] = useState(false);
  const [conversationOpen, setConversationOpen] = useState(true);
  const [projectNavOpen, setProjectNavOpen] = useState(false);

  useEffect(() => {
    void api
      .listProjects()
      .then((items) => {
        setProjects(items);
        setSelectedProjectId((current) => current ?? items[0]?.id ?? null);
      })
      .catch((cause: Error) => setError(cause.message));
  }, [api]);

  useEffect(() => {
    void api
      .getModelConfiguration()
      .then(setModelConfiguration)
      .catch((cause: Error) => setError(cause.message));
  }, [api]);

  useEffect(() => {
    if (!selectedProjectId) {
      setArtifacts([]);
      setTasks([]);
      setSelectedTaskId(null);
      return;
    }
    void Promise.all([
      api.listArtifacts(selectedProjectId),
      api.listTasks(selectedProjectId),
    ])
      .then(([projectArtifacts, projectTasks]) => {
        setArtifacts(projectArtifacts);
        setTasks(projectTasks);
        setSelectedTaskId((current) =>
          projectTasks.some((item) => item.id === current)
            ? current
            : (projectTasks[0]?.id ?? null),
        );
      })
      .catch((cause: Error) => setError(cause.message));
  }, [api, selectedProjectId]);

  useEffect(() => {
    if (!selectedTaskId) {
      setTask(null);
      setEvents([]);
      return;
    }
    let active = true;
    const refresh = () => {
      void api.getTask(selectedTaskId).then((detail) => {
        if (active) setTask(detail);
      });
    };
    refresh();
    const unsubscribe = api.subscribeToTask(selectedTaskId, (event) => {
      if (!active) return;
      setEvents((current) => [...current.filter((item) => item.sequence !== event.sequence), event]);
      refresh();
    });
    return () => {
      active = false;
      unsubscribe();
    };
  }, [api, selectedTaskId]);

  async function createProject(name: string) {
    try {
      const created = await api.createProject(name);
      setProjects((current) => [...current, created]);
      setSelectedProjectId(created.id);
      setError(null);
    } catch (cause) {
      setError((cause as Error).message);
    }
  }

  async function uploadDataset(file: File) {
    if (!selectedProjectId) return;
    try {
      const artifact = await api.uploadDataset(selectedProjectId, file);
      setArtifacts((current) => [...current, artifact]);
      setError(null);
    } catch (cause) {
      setError((cause as Error).message);
    }
  }

  async function createTask(event: FormEvent) {
    event.preventDefault();
    if (!selectedProjectId || !objective.trim()) return;
    try {
      const created = await api.createTask(selectedProjectId, objective.trim());
      setTasks((current) => [...current, created]);
      setSelectedTaskId(created.id);
      setObjective("");
      setError(null);
    } catch (cause) {
      setError((cause as Error).message);
    }
  }

  async function sendMessage(content: string) {
    if (!selectedTaskId) return;
    await api.sendMessage(selectedTaskId, content);
    setTask(await api.getTask(selectedTaskId));
  }

  async function decideApproval(approvalId: string, approved: boolean) {
    await api.decideApproval(approvalId, approved);
    if (selectedTaskId) setTask(await api.getTask(selectedTaskId));
  }

  async function saveModelConfiguration(input: SaveModelConfiguration) {
    const saved = await api.saveModelConfiguration(input);
    setModelConfiguration(saved);
    setModelSettingsOpen(false);
    setError(null);
  }

  const shellClassName = task
    ? `app-shell with-conversation${conversationOpen ? "" : " conversation-collapsed"}`
    : "app-shell";

  return (
    <div className={shellClassName}>
      <button
        type="button"
        className="project-drawer-toggle"
        aria-label="打开项目导航"
        aria-expanded={projectNavOpen}
        onClick={() => {
          setConversationOpen(false);
          setProjectNavOpen(true);
        }}
      >
        <span aria-hidden="true">TRACE</span>
        项目
      </button>

      <ProjectNav
        projects={projects}
        selectedProjectId={selectedProjectId}
        artifacts={artifacts}
        tasks={tasks}
        selectedTaskId={selectedTaskId}
        onSelectProject={(id) => {
          setSelectedProjectId(id);
          setProjectNavOpen(false);
        }}
        onSelectTask={(id) => {
          setSelectedTaskId(id);
          setProjectNavOpen(false);
        }}
        onCreateProject={createProject}
        onUpload={uploadDataset}
        modelConfiguration={modelConfiguration}
        onOpenModelSettings={() => setModelSettingsOpen(true)}
        drawerOpen={projectNavOpen}
        onClose={() => setProjectNavOpen(false)}
      />

      {error && <div className="error-banner" role="alert">{error}</div>}

      {task ? (
        <TaskWorkbench
          task={task}
          events={events}
          conversationOpen={conversationOpen}
          onOpenConversation={() => setConversationOpen(true)}
        />
      ) : (
        <main className="task-workbench empty-workbench">
          <div className="empty-sequence">DATA → PLAN → EVIDENCE</div>
          <h1>{selectedProjectId ? "提出第一个经营问题" : "创建一个分析项目"}</h1>
          <p>
            {selectedProjectId
              ? "Agent 会检查项目数据、制定计划并留下可复现的代码与证据。"
              : "项目是数据、业务口径、分析任务和报告的长期容器。"}
          </p>
          {selectedProjectId && (
            <form className="objective-form" onSubmit={createTask}>
              <label htmlFor="objective">分析目标</label>
              <textarea
                id="objective"
                value={objective}
                placeholder="例如：为什么华东区本季度利润下降？"
                onChange={(event) => setObjective(event.target.value)}
              />
              <button type="submit" disabled={!objective.trim()}>开始分析</button>
            </form>
          )}
        </main>
      )}

      {task && (
        <Conversation
          messages={task.messages}
          events={events}
          onSend={sendMessage}
          onApproval={decideApproval}
          onCollapse={() => setConversationOpen(false)}
          hidden={!conversationOpen}
        />
      )}

      {modelConfiguration && (
        <ModelSettings
          configuration={modelConfiguration}
          open={modelSettingsOpen}
          onClose={() => setModelSettingsOpen(false)}
          onSave={saveModelConfiguration}
        />
      )}
    </div>
  );
}
