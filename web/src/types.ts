export interface Project {
  id: string;
  name: string;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface Artifact {
  id: string;
  project_id: string;
  task_id: string | null;
  artifact_type: string;
  title: string;
  description: string;
  content_type: string | null;
  created_at: string;
  content_url: string;
}

export interface Task {
  id: string;
  project_id: string;
  objective: string;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface Message {
  id: string;
  role: string;
  content: string;
  created_at: string;
}

export interface PlanStep {
  id: string;
  position: number;
  title: string;
  status: string;
}

export interface Plan {
  id: string;
  revision: number;
  reason: string;
  steps: PlanStep[];
}

export interface TaskDetail extends Task {
  messages: Message[];
  plan: Plan | null;
  artifacts: Artifact[];
}

export interface ApprovalDecision {
  id: string;
  status: string;
  decided_at: string | null;
}

export type TaskEventType =
  | "task.state"
  | "task.reopened"
  | "task.resumed"
  | "task.failed"
  | "plan.updated"
  | "tool.started"
  | "tool.completed"
  | "tool.failed"
  | "artifact.created"
  | "approval.requested"
  | "approval.decided"
  | "message.created";

export interface TaskEvent {
  sequence: number;
  type: TaskEventType;
  payload: Record<string, unknown>;
}

export interface ProviderDefinition {
  provider: string;
  label: string;
  base_url: string | null;
  models: string[];
  default_model: string;
  custom_url: boolean;
}

export interface ModelConfigurationStatus {
  provider: string | null;
  base_url: string;
  model: string;
  configured: boolean;
  has_api_key: boolean;
  updated_at: string | null;
  providers: ProviderDefinition[];
}

export interface SaveModelConfiguration {
  provider: string;
  base_url?: string | null;
  model: string;
  api_key?: string | null;
}
