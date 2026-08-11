import { FormEvent, useEffect, useMemo, useState } from "react";

import { HttpApiClient, type ApiClient } from "./api";
import { ArtifactWorkspace } from "./components/ArtifactWorkspace";
import { Conversation } from "./components/Conversation";
import { DataSourceSettings } from "./components/DataSourceSettings";
import { KnowledgeSettings } from "./components/KnowledgeSettings";
import { ModelSettings } from "./components/ModelSettings";
import { ProjectNav } from "./components/ProjectNav";
import type {
  AgentRun,
  AnalysisRun,
  Artifact,
  ModelConfigurationStatus,
  Project,
  SaveModelConfiguration,
  SkillCandidate,
  Task,
  TaskDetail,
  TaskEvent,
  TraceEvent,
} from "./types";

interface AppProps { api?: ApiClient }

export function App({ api: suppliedApi }: AppProps) {
  const api = useMemo(() => suppliedApi ?? new HttpApiClient(), [suppliedApi]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [task, setTask] = useState<TaskDetail | null>(null);
  const [events, setEvents] = useState<TaskEvent[]>([]);
  const [agents, setAgents] = useState<AgentRun[]>([]);
  const [runs, setRuns] = useState<AnalysisRun[]>([]);
  const [traceEvents, setTraceEvents] = useState<TraceEvent[]>([]);
  const [selectedRunId, setSelectedRunId] = useState("");
  const [candidate, setCandidate] = useState<SkillCandidate | null>(null);
  const [curating, setCurating] = useState(false);
  const [objective, setObjective] = useState("");
  const [initialUploading, setInitialUploading] = useState(false);
  const [initialUploadName, setInitialUploadName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [modelConfiguration, setModelConfiguration] = useState<ModelConfigurationStatus | null>(null);
  const [modelSettingsOpen, setModelSettingsOpen] = useState(false);
  const [dataSourceSettingsOpen, setDataSourceSettingsOpen] = useState(false);
  const [knowledgeSettingsOpen, setKnowledgeSettingsOpen] = useState(false);
  const [projectNavOpen, setProjectNavOpen] = useState(false);
  const [projectNavCollapsed, setProjectNavCollapsed] = useState(false);
  const [artifactWorkspaceOpen, setArtifactWorkspaceOpen] = useState(false);
  const [artifactWorkspaceCollapsed, setArtifactWorkspaceCollapsed] = useState(false);

  useEffect(() => {
    void api.listProjects().then((items) => {
      setProjects(items);
      setSelectedProjectId((current) => current ?? items[0]?.id ?? null);
    }).catch((cause: Error) => setError(cause.message));
    void api.getModelConfiguration().then(setModelConfiguration)
      .catch((cause: Error) => setError(cause.message));
  }, [api]);

  useEffect(() => {
    if (!selectedProjectId) {
      setArtifacts([]); setTasks([]); setSelectedTaskId(null); return;
    }
    void Promise.all([api.listArtifacts(selectedProjectId), api.listTasks(selectedProjectId)])
      .then(([projectArtifacts, projectTasks]) => {
        setArtifacts(projectArtifacts); setTasks(projectTasks);
        setSelectedTaskId((current) => projectTasks.some((item) => item.id === current)
          ? current : (projectTasks[0]?.id ?? null));
      }).catch((cause: Error) => setError(cause.message));
  }, [api, selectedProjectId]);

  useEffect(() => {
    if (!selectedTaskId) {
      setTask(null); setEvents([]); setAgents([]); setRuns([]);
      setTraceEvents([]); setSelectedRunId(""); setCandidate(null); return;
    }
    let active = true;
    const refresh = async () => {
      const [detail, nextAgents, nextRuns] = await Promise.all([
        api.getTask(selectedTaskId), api.listAgents(selectedTaskId), api.listRuns(selectedTaskId),
      ]);
      if (active) { setTask(detail); setAgents(nextAgents); setRuns(nextRuns); }
    };
    void refresh().catch((cause: Error) => setError(cause.message));
    const unsubscribe = api.subscribeToTask(selectedTaskId, (event) => {
      if (!active) return;
      setEvents((current) => [...current.filter((item) => item.sequence !== event.sequence), event]);
      void refresh().catch((cause: Error) => setError(cause.message));
    });
    return () => { active = false; unsubscribe(); };
  }, [api, selectedTaskId]);

  async function createProject(name: string) {
    try {
      const created = await api.createProject(name);
      setProjects((current) => [...current, created]); setSelectedProjectId(created.id); setError(null);
    } catch (cause) { setError((cause as Error).message); }
  }

  async function renameProject(projectId: string, name: string) {
    try {
      const updated = await api.updateProject(projectId, name);
      setProjects((current) => current.map((project) => project.id === updated.id ? updated : project));
      setError(null);
    } catch (cause) { setError((cause as Error).message); }
  }

  async function uploadDataset(file: File) {
    if (!selectedProjectId) return;
    try {
      const artifact = await api.uploadDataset(selectedProjectId, file);
      setArtifacts((current) => [...current, artifact]); setError(null);
    } catch (cause) { setError((cause as Error).message); throw cause; }
  }

  async function uploadChatFile(file: File) {
    const extension = file.name.split(".").pop()?.toLowerCase();
    if (extension === "csv" || extension === "xlsx") {
      await uploadDataset(file);
      return;
    }
    try {
      await api.importBusinessKnowledge(file);
      setError(null);
    } catch (cause) {
      setError((cause as Error).message);
      throw cause;
    }
  }

  async function uploadInitialDataset(file: File) {
    setInitialUploading(true);
    try {
      await uploadDataset(file);
      setInitialUploadName(file.name);
    } finally {
      setInitialUploading(false);
    }
  }

  async function createTask(event: FormEvent) {
    event.preventDefault();
    if (!selectedProjectId || !objective.trim()) return;
    try {
      const created = await api.createTask(selectedProjectId, objective.trim());
      setTasks((current) => [...current, created]); setSelectedTaskId(created.id);
      setObjective(""); setError(null);
    } catch (cause) { setError((cause as Error).message); }
  }

  async function selectRun(runId: string) {
    setSelectedRunId(runId); setCandidate(null);
    if (!runId) { setTraceEvents([]); return; }
    try {
      setTraceEvents(await api.getTrace(runId));
      setCandidate(await api.getSkillCandidateForRun(runId).catch(() => null));
    } catch (cause) { setError((cause as Error).message); }
  }

  async function acceptAndCurate(runId: string) {
    setCurating(true); setError(null);
    try {
      await api.acceptRun(runId);
      if (selectedTaskId) setRuns(await api.listRuns(selectedTaskId));
      for (let attempt = 0; attempt < 20; attempt += 1) {
        const next = await api.getSkillCandidateForRun(runId).catch(() => null);
        if (next) { setCandidate(next); break; }
        await new Promise((resolve) => window.setTimeout(resolve, 250));
      }
      if (selectedTaskId) setAgents(await api.listAgents(selectedTaskId));
    } catch (cause) { setError((cause as Error).message); }
    finally { setCurating(false); }
  }

  async function decideSkill(candidateId: string, approved: boolean) {
    setCandidate(await api.decideSkillCandidate(candidateId, approved));
  }

  const shellClassName = ["app-shell", task ? "with-conversation" : "",
    projectNavCollapsed ? "project-collapsed" : "",
    artifactWorkspaceCollapsed ? "artifact-collapsed" : ""].filter(Boolean).join(" ");

  return <div className={shellClassName}>
    <button type="button" className={projectNavCollapsed ? "project-drawer-toggle force-visible" : "project-drawer-toggle"}
      aria-label="打开任务台" aria-expanded={projectNavOpen}
      onClick={() => { setProjectNavCollapsed(false); setProjectNavOpen(true); }}>
      <span aria-hidden="true">TRACE</span>任务台
    </button>
    {task && <button type="button" className={artifactWorkspaceCollapsed ? "artifact-drawer-toggle force-visible" : "artifact-drawer-toggle"}
      aria-label="打开证据与运行" aria-expanded={!artifactWorkspaceCollapsed && artifactWorkspaceOpen}
      onClick={() => { setArtifactWorkspaceCollapsed(false); setArtifactWorkspaceOpen(true); }}>
      证据与运行<span aria-hidden="true">{task.artifacts.length}</span>
    </button>}

    {!projectNavCollapsed && <ProjectNav projects={projects} selectedProjectId={selectedProjectId}
      tasks={tasks} selectedTaskId={selectedTaskId}
      onSelectProject={(id) => { setSelectedProjectId(id); setProjectNavOpen(false); }}
      onSelectTask={(id) => { setSelectedTaskId(id); setProjectNavOpen(false); }}
      onStartNewTask={() => {
        setSelectedTaskId(null); setTask(null); setEvents([]); setObjective("");
        setInitialUploadName(""); setError(null); setProjectNavOpen(false);
      }}
      onCreateProject={createProject} onRenameProject={renameProject}
      modelConfiguration={modelConfiguration} onOpenModelSettings={() => setModelSettingsOpen(true)}
      onOpenDataSources={() => setDataSourceSettingsOpen(true)} onOpenKnowledge={() => setKnowledgeSettingsOpen(true)} drawerOpen={projectNavOpen}
      onClose={() => setProjectNavOpen(false)} onCollapse={() => { setProjectNavOpen(false); setProjectNavCollapsed(true); }} />}

    {error && <div className="error-banner" role="alert">{error}</div>}
    {!task && <main className="task-workbench empty-workbench">
      <div className="empty-sequence">{selectedProjectId && tasks.length ? "NEW CONVERSATION" : "DATA → PLAN → EVIDENCE"}</div>
      <h1>{selectedProjectId ? tasks.length ? "开始新对话" : "提出第一个经营问题" : "创建一个分析项目"}</h1>
      <p>{selectedProjectId ? tasks.length
        ? "输入第一个问题后，这条对话会出现在左侧项目中。"
        : "Agent 会检查项目数据、制定计划并留下可复现的代码与证据。"
        : "项目是数据、业务口径、分析任务和报告的长期容器。"}</p>
      {selectedProjectId && <form className="objective-form" onSubmit={createTask}>
        <label htmlFor="objective">分析目标</label><textarea id="objective" value={objective} autoFocus
          placeholder="例如：为什么华东区本季度利润下降？" onChange={(event) => setObjective(event.target.value)} />
        <div className="objective-actions">
          <label className={initialUploading ? "objective-upload uploading" : "objective-upload"}>
            <span aria-hidden="true">＋</span>
            <span>{initialUploading ? "正在上传…" : initialUploadName ? `已添加 ${initialUploadName}` : "添加 CSV / Excel"}</span>
            <input type="file" accept=".csv,.xlsx" aria-label="在提出问题前上传分析文件"
              disabled={initialUploading} onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = "";
                if (file) void uploadInitialDataset(file);
              }} />
          </label>
          <button type="submit" disabled={!objective.trim()}>开始分析</button>
        </div>
      </form>}
    </main>}

    {task && <Conversation task={task} events={events}
      onUpload={uploadChatFile}
      onSend={async (content) => { if (selectedTaskId) { await api.sendMessage(selectedTaskId, content); setTask(await api.getTask(selectedTaskId)); } }}
      onApproval={async (id, approved) => { await api.decideApproval(id, approved); if (selectedTaskId) setTask(await api.getTask(selectedTaskId)); }} />}
    {task && !artifactWorkspaceCollapsed && <ArtifactWorkspace task={task} agents={agents} runs={runs} traceEvents={traceEvents}
      selectedRunId={selectedRunId} candidate={candidate} curating={curating}
      onSelectRun={selectRun} onAcceptRun={acceptAndCurate} onDecideSkill={decideSkill}
      drawerOpen={artifactWorkspaceOpen} onClose={() => { setArtifactWorkspaceOpen(false); setArtifactWorkspaceCollapsed(true); }} />}
    {modelConfiguration && <ModelSettings configuration={modelConfiguration} open={modelSettingsOpen}
      onClose={() => setModelSettingsOpen(false)} onSave={async (input: SaveModelConfiguration) => {
        setModelConfiguration(await api.saveModelConfiguration(input)); setModelSettingsOpen(false); setError(null);
      }} />}
    <DataSourceSettings open={dataSourceSettingsOpen} api={api}
      onClose={() => setDataSourceSettingsOpen(false)} />
    <KnowledgeSettings open={knowledgeSettingsOpen} api={api}
      onClose={() => setKnowledgeSettingsOpen(false)} />
  </div>;
}
