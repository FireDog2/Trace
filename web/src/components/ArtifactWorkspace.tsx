import { useMemo, useState } from "react";

import type { AgentRun, AnalysisRun, Artifact, SkillCandidate, TaskDetail, TraceEvent } from "../types";
import { AgentTeamPanel } from "./AgentTeamPanel";
import { SkillCandidatePanel } from "./SkillCandidatePanel";
import { TraceTimeline } from "./TraceTimeline";

type WorkspaceTab = "report" | "chart" | "data" | "code" | "team" | "trace";
type ArtifactTab = Exclude<WorkspaceTab, "team" | "trace">;

interface ArtifactWorkspaceProps {
  task: TaskDetail; agents: AgentRun[]; runs: AnalysisRun[]; traceEvents: TraceEvent[];
  selectedRunId: string; candidate: SkillCandidate | null; curating: boolean;
  onSelectRun(runId: string): Promise<void>; onAcceptRun(runId: string): Promise<void>;
  onDecideSkill(id: string, approved: boolean): Promise<void>;
  drawerOpen: boolean; onClose(): void;
}

const TAB_LABELS: Record<WorkspaceTab, string> = {
  report: "报告", chart: "图表", data: "数据", code: "代码", team: "Agent", trace: "Trace·Skill",
};
const EMPTY_LABELS: Record<ArtifactTab, string> = {
  report: "暂无报告产物", chart: "暂无图表产物", data: "暂无数据产物", code: "暂无代码产物",
};

function artifactTab(artifact: Artifact): ArtifactTab {
  const type = artifact.artifact_type.toLowerCase();
  if (type.includes("chart") || type.includes("plot") || type.includes("visual")) return "chart";
  if (type.includes("dataset") || type.includes("table") || type.includes("csv") || type.includes("data")) return "data";
  if (type.includes("code") || type.includes("python") || type.includes("sql") || type.includes("notebook")) return "code";
  return "report";
}

export function ArtifactWorkspace({ task, agents, runs, traceEvents, selectedRunId, candidate,
  curating, onSelectRun, onAcceptRun, onDecideSkill, drawerOpen, onClose }: ArtifactWorkspaceProps) {
  const [activeTab, setActiveTab] = useState<WorkspaceTab>("report");
  const latestConclusion = [...task.messages].reverse().find((message) => message.role === "assistant")?.content;
  const grouped = useMemo(() => {
    const result: Record<ArtifactTab, Artifact[]> = { report: [], chart: [], data: [], code: [] };
    for (const artifact of task.artifacts) result[artifactTab(artifact)].push(artifact);
    return result;
  }, [task.artifacts]);
  const selectedRun = runs.find((run) => run.id === selectedRunId);
  const tabCount = (tab: WorkspaceTab) => tab === "team" ? agents.length : tab === "trace" ? runs.length : grouped[tab].length;

  return <aside className={drawerOpen ? "artifact-workspace drawer-open" : "artifact-workspace"} aria-label="最终产物">
    <header className="artifact-workspace-header"><div><span>EVIDENCE / RUNTIME</span><h2>证据与运行</h2></div>
      <div className="artifact-header-actions"><span>{task.artifacts.length} 产物 · {agents.length} Agent</span>
        <button type="button" className="artifact-close" aria-label="关闭最终产物" onClick={onClose}>×</button></div>
    </header>
    <div className="artifact-tabs" role="tablist" aria-label="证据与运行类型">
      {(Object.keys(TAB_LABELS) as WorkspaceTab[]).map((tab) => <button type="button" role="tab"
        id={`artifact-tab-${tab}`} aria-controls="artifact-tab-panel" aria-selected={activeTab === tab}
        className={activeTab === tab ? "active" : ""} key={tab} onClick={() => setActiveTab(tab)}>
        {TAB_LABELS[tab]}<span>{tabCount(tab)}</span></button>)}
    </div>
    <div className="artifact-scroll" id="artifact-tab-panel" role="tabpanel" aria-labelledby={`artifact-tab-${activeTab}`}>
      {activeTab === "team" && <AgentTeamPanel agents={agents} />}
      {activeTab === "trace" && <section className="runtime-trace" aria-label="Trace 回放与 Skill 沉淀">
        <div className="panel-heading"><div><span className="section-label">REPLAY / CURATE</span><h3>运行回放</h3></div><span>{runs.length} RUNS</span></div>
        {runs.length ? <>
          <label className="trace-run-picker">选择运行<select value={selectedRunId} onChange={(event) => void onSelectRun(event.target.value)}>
            <option value="">选择运行</option>{runs.map((run) => <option key={run.id} value={run.id}>
              {run.id.slice(0, 8)} · {run.status}{run.accepted ? " · accepted" : ""}</option>)}</select></label>
          {selectedRun && <div className="run-review-bar"><span className={`run-state ${selectedRun.status}`}>{selectedRun.status}</span>
            {selectedRun.accepted ? <strong>已接受，Curator 将沉淀候选 Skill</strong> :
              <button type="button" className="primary-action" disabled={selectedRun.status !== "completed" || curating}
                onClick={() => void onAcceptRun(selectedRun.id)}>{curating ? "Curator 处理中" : "接受运行并沉淀 Skill"}</button>}
            {selectedRun.accepted && !candidate && <button type="button" onClick={() => void onSelectRun(selectedRun.id)}>刷新 Candidate</button>}
            <a className="replay-export" href={`/api/runs/${selectedRun.id}/replay`} target="_blank" rel="noreferrer">完整回放 JSON</a>
          </div>}
          {traceEvents.length ? <TraceTimeline events={traceEvents} /> : <p className="empty-note">选择运行后加载脱敏 Trace。</p>}
          {candidate && <SkillCandidatePanel candidate={candidate} onDecision={onDecideSkill} />}
        </> : <p className="empty-note">尚无运行。完成一次分析后，这里会保留 Lead、Teammate、SQL、审批和产物链路。</p>}
      </section>}
      {(activeTab === "report" || activeTab === "chart" || activeTab === "data" || activeTab === "code") && <>
        {activeTab === "report" && <section className="report-conclusion" aria-labelledby="report-conclusion-heading">
          <span>核心结论</span><h3 id="report-conclusion-heading">分析摘要</h3>
          <p>{latestConclusion ?? "Agent 完成分析后，结论会沉淀在这里。"}</p></section>}
        {grouped[activeTab].length > 0 ? <div className={`artifact-grid ${activeTab}`}>
          {grouped[activeTab].map((artifact) => <ArtifactCard artifact={artifact} tab={activeTab} key={artifact.id} />)}
        </div> : <div className="artifact-empty"><span aria-hidden="true">{activeTab === "chart" ? "⌁" : activeTab === "data" ? "▦" : activeTab === "code" ? "</>" : "≋"}</span>
          <strong>{EMPTY_LABELS[activeTab]}</strong><p>Agent 生成并保存后，会自动归档到这个标签页。</p></div>}
      </>}
    </div>
  </aside>;
}

function ArtifactCard({ artifact, tab }: { artifact: Artifact; tab: ArtifactTab }) {
  const canPreviewImage = tab === "chart" && artifact.content_type?.startsWith("image/");
  const canPreviewHtml = tab === "chart" && artifact.content_type === "text/html";
  return <article className="artifact-card">
    {canPreviewImage && <img src={artifact.content_url} alt={artifact.title} />}
    {canPreviewHtml && <iframe src={artifact.content_url} title={`${artifact.title} 预览`} loading="lazy"
      sandbox="allow-scripts" referrerPolicy="no-referrer" />}
    <div className="artifact-card-body"><span>{artifact.artifact_type.replaceAll("_", " ")}</span><h3>{artifact.title}</h3>
      <p>{artifact.description || "已登记分析产物"}</p><a href={artifact.content_url} target="_blank" rel="noreferrer" aria-label={`打开 ${artifact.title}`}>
        打开产物 <span aria-hidden="true">→</span></a></div>
  </article>;
}
