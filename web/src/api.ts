import type { Artifact, Project, Task, TaskDetail, TaskEvent, TaskEventType } from "./types";

export interface ApiClient {
  listProjects(): Promise<Project[]>;
  createProject(name: string): Promise<Project>;
  uploadDataset(projectId: string, file: File): Promise<Artifact>;
  listArtifacts(projectId: string): Promise<Artifact[]>;
  listTasks(projectId: string): Promise<Task[]>;
  createTask(projectId: string, objective: string): Promise<Task>;
  getTask(taskId: string): Promise<TaskDetail>;
  sendMessage(taskId: string, content: string): Promise<Task>;
  subscribeToTask(taskId: string, onEvent: (event: TaskEvent) => void): () => void;
}

const EVENT_TYPES: TaskEventType[] = [
  "task.state",
  "task.failed",
  "plan.updated",
  "tool.started",
  "tool.completed",
  "tool.failed",
  "artifact.created",
  "approval.requested",
  "message.created",
];

export class HttpApiClient implements ApiClient {
  constructor(private readonly baseUrl = "") {}

  listProjects(): Promise<Project[]> {
    return this.request("/api/projects");
  }

  createProject(name: string): Promise<Project> {
    return this.request("/api/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
  }

  uploadDataset(projectId: string, file: File): Promise<Artifact> {
    const form = new FormData();
    form.append("file", file);
    return this.request(`/api/projects/${projectId}/datasets`, {
      method: "POST",
      body: form,
    });
  }

  listArtifacts(projectId: string): Promise<Artifact[]> {
    return this.request(`/api/projects/${projectId}/artifacts`);
  }

  listTasks(projectId: string): Promise<Task[]> {
    return this.request(`/api/projects/${projectId}/tasks`);
  }

  createTask(projectId: string, objective: string): Promise<Task> {
    return this.request(`/api/projects/${projectId}/tasks`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ objective }),
    });
  }

  getTask(taskId: string): Promise<TaskDetail> {
    return this.request(`/api/tasks/${taskId}`);
  }

  sendMessage(taskId: string, content: string): Promise<Task> {
    return this.request(`/api/tasks/${taskId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    });
  }

  subscribeToTask(taskId: string, onEvent: (event: TaskEvent) => void): () => void {
    let source: EventSource | null = null;
    let lastSequence = 0;
    let reconnectTimer: number | null = null;
    let stopped = false;

    const connect = () => {
      source = new EventSource(
        `${this.baseUrl}/api/tasks/${taskId}/events?after=${lastSequence}&follow=true`,
      );
      for (const type of EVENT_TYPES) {
        source.addEventListener(type, (rawEvent) => {
          const event = rawEvent as MessageEvent<string>;
          const sequence = Number(event.lastEventId);
          if (Number.isFinite(sequence)) lastSequence = sequence;
          onEvent({
            sequence: lastSequence,
            type,
            payload: JSON.parse(event.data) as Record<string, unknown>,
          });
        });
      }
      source.onerror = () => {
        source?.close();
        if (!stopped) reconnectTimer = window.setTimeout(connect, 1_000);
      };
    };

    connect();
    return () => {
      stopped = true;
      source?.close();
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
    };
  }

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, init);
    if (!response.ok) {
      const body = (await response.json().catch(() => ({}))) as { detail?: string };
      throw new Error(body.detail ?? `请求失败（${response.status}）`);
    }
    return (await response.json()) as T;
  }
}
