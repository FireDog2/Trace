import { FormEvent, useEffect, useMemo, useState } from "react";

import { HttpApiClient, type ApiClient } from "./api";
import { Conversation } from "./components/Conversation";
import { ProjectNav } from "./components/ProjectNav";
import { TaskWorkbench } from "./components/TaskWorkbench";
import type { Artifact, Project, Task, TaskDetail, TaskEvent } from "./types";

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

  return (
    <div className="app-shell">
      <ProjectNav
        projects={projects}
        selectedProjectId={selectedProjectId}
        artifacts={artifacts}
        tasks={tasks}
        selectedTaskId={selectedTaskId}
        onSelectProject={setSelectedProjectId}
        onSelectTask={setSelectedTaskId}
        onCreateProject={createProject}
        onUpload={uploadDataset}
      />

      {error && <div className="error-banner" role="alert">{error}</div>}

      {task ? (
        <TaskWorkbench task={task} events={events} />
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

      <Conversation
        messages={task?.messages ?? []}
        disabled={!task}
        onSend={sendMessage}
      />
    </div>
  );
}
