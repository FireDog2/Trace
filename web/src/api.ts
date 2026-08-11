import type {
  ApprovalDecision,
  AgentRun,
  AnalysisRun,
  TraceEvent,
  SkillCandidate,
  Artifact,
  Project,
  Task,
  TaskDetail,
  TaskEvent,
  TaskEventType,
  ModelConfigurationStatus,
  SaveModelConfiguration,
} from "./types";

export interface ApiClient {
  listProjects(): Promise<Project[]>;
  createProject(name: string): Promise<Project>;
  updateProject(projectId: string, name: string): Promise<Project>;
  uploadDataset(projectId: string, file: File): Promise<Artifact>;
  listArtifacts(projectId: string): Promise<Artifact[]>;
  listTasks(projectId: string): Promise<Task[]>;
  createTask(projectId: string, objective: string): Promise<Task>;
  getTask(taskId: string): Promise<TaskDetail>;
  listDataSources(): Promise<import("./types").DataSource[]>;
  createDataSource(input: { name: string; dsn: string }): Promise<import("./types").DataSource>;
  updateDataSource(sourceId: string, input: { name: string; dsn?: string }): Promise<import("./types").DataSource>;
  deleteDataSource(sourceId: string): Promise<void>;
  getDataSourceSchema(sourceId: string): Promise<import("./types").DatabaseSchema>;
  getDataSourcePolicies(sourceId: string): Promise<import("./types").PolicySnapshot>;
  replaceDataSourcePolicies(sourceId: string, rules: import("./types").PolicyRule[], expectedVersion?: number): Promise<import("./types").PolicySnapshot>;
  listBusinessKnowledge(): Promise<import("./types").BusinessKnowledgeEntry[]>;
  importBusinessKnowledge(file: File): Promise<import("./types").BusinessKnowledgeImportResult>;
  createBusinessKnowledge(input: import("./types").SaveBusinessKnowledge): Promise<import("./types").BusinessKnowledgeEntry>;
  updateBusinessKnowledge(entryId: string, input: import("./types").SaveBusinessKnowledge): Promise<import("./types").BusinessKnowledgeEntry>;
  deleteBusinessKnowledge(entryId: string): Promise<void>;
  listAgents(taskId: string): Promise<AgentRun[]>;
  listRuns(taskId: string): Promise<AnalysisRun[]>;
  getTrace(runId: string): Promise<TraceEvent[]>;
  acceptRun(runId: string): Promise<AnalysisRun>;
  getSkillCandidateForRun(runId: string): Promise<SkillCandidate>;
  createSkillCandidate(runId: string): Promise<SkillCandidate>;
  decideSkillCandidate(candidateId: string, approved: boolean): Promise<SkillCandidate>;
  sendMessage(taskId: string, content: string): Promise<Task>;
  decideApproval(approvalId: string, approved: boolean): Promise<ApprovalDecision>;
  getModelConfiguration(): Promise<ModelConfigurationStatus>;
  saveModelConfiguration(input: SaveModelConfiguration): Promise<ModelConfigurationStatus>;
  subscribeToTask(taskId: string, onEvent: (event: TaskEvent) => void): () => void;
}

const EVENT_TYPES: TaskEventType[] = [
  "task.state",
  "task.reopened",
  "task.retried",
  "task.resumed",
  "task.failed",
  "plan.updated",
  "tool.started",
  "tool.completed",
  "tool.failed",
  "artifact.created",
  "approval.requested",
  "approval.decided",
  "message.created",
  "agent.created",
  "agent.state",
  "agent.message",
  "agent.completed",
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

  updateProject(projectId: string, name: string): Promise<Project> {
    return this.request(`/api/projects/${projectId}`, {
      method: "PUT",
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
  listDataSources(): Promise<import("./types").DataSource[]> { return this.request("/api/data-sources"); }
  createDataSource(input: { name: string; dsn: string }): Promise<import("./types").DataSource> { return this.request("/api/data-sources", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input) }); }
  updateDataSource(sourceId: string, input: { name: string; dsn?: string }): Promise<import("./types").DataSource> { return this.request(`/api/data-sources/${sourceId}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input) }); }
  deleteDataSource(sourceId: string): Promise<void> { return this.request(`/api/data-sources/${sourceId}`, { method: "DELETE" }); }
  getDataSourceSchema(sourceId: string): Promise<import("./types").DatabaseSchema> { return this.request(`/api/data-sources/${sourceId}/schema`); }
  getDataSourcePolicies(sourceId: string): Promise<import("./types").PolicySnapshot> { return this.request(`/api/data-sources/${sourceId}/policies`); }
  replaceDataSourcePolicies(sourceId: string, rules: import("./types").PolicyRule[], expectedVersion?: number): Promise<import("./types").PolicySnapshot> { return this.request(`/api/data-sources/${sourceId}/policies`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ rules, expected_version: expectedVersion }) }); }
  listBusinessKnowledge(): Promise<import("./types").BusinessKnowledgeEntry[]> { return this.request("/api/knowledge"); }
  importBusinessKnowledge(file: File): Promise<import("./types").BusinessKnowledgeImportResult> { const form = new FormData(); form.append("file", file); return this.request("/api/knowledge/import", { method: "POST", body: form }); }
  createBusinessKnowledge(input: import("./types").SaveBusinessKnowledge): Promise<import("./types").BusinessKnowledgeEntry> { return this.request("/api/knowledge", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input) }); }
  updateBusinessKnowledge(entryId: string, input: import("./types").SaveBusinessKnowledge): Promise<import("./types").BusinessKnowledgeEntry> { return this.request(`/api/knowledge/${entryId}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input) }); }
  deleteBusinessKnowledge(entryId: string): Promise<void> { return this.request(`/api/knowledge/${entryId}`, { method: "DELETE" }); }

  listAgents(taskId: string): Promise<AgentRun[]> {
    return this.request(`/api/tasks/${taskId}/agents`);
  }

  listRuns(taskId: string): Promise<AnalysisRun[]> { return this.request(`/api/tasks/${taskId}/runs`); }
  getTrace(runId: string): Promise<TraceEvent[]> { return this.request(`/api/runs/${runId}/trace`); }
  acceptRun(runId: string): Promise<AnalysisRun> { return this.request(`/api/runs/${runId}/accept`, { method: "POST" }); }
  getSkillCandidateForRun(runId: string): Promise<SkillCandidate> { return this.request(`/api/runs/${runId}/skill-candidate`); }
  createSkillCandidate(runId: string): Promise<SkillCandidate> { return this.request(`/api/runs/${runId}/skill-candidates`, { method: "POST" }); }
  decideSkillCandidate(candidateId: string, approved: boolean): Promise<SkillCandidate> { return this.request(`/api/skill-candidates/${candidateId}/decision`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ approved }) }); }

  sendMessage(taskId: string, content: string): Promise<Task> {
    return this.request(`/api/tasks/${taskId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    });
  }

  decideApproval(approvalId: string, approved: boolean): Promise<ApprovalDecision> {
    return this.request(`/api/approvals/${approvalId}/decision`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ approved }),
    });
  }

  getModelConfiguration(): Promise<ModelConfigurationStatus> {
    return this.request("/api/model-config");
  }

  saveModelConfiguration(input: SaveModelConfiguration): Promise<ModelConfigurationStatus> {
    return this.request("/api/model-config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
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
      const body = (await response.json().catch(() => ({}))) as {
        detail?: string | { category?: string; message?: string };
      };
      const message = typeof body.detail === "string"
        ? body.detail
        : body.detail?.message;
      throw new Error(message ?? `请求失败（${response.status}）`);
    }
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }
}
