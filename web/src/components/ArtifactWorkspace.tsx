import { useMemo, useState } from "react";

import type {
  AgentRun,
  AnalysisRun,
  Artifact,
  SkillCandidate,
  TaskDetail,
  TraceEvent,
} from "../types";
import { AgentTeamPanel } from "./AgentTeamPanel";
import { Markdown } from "./Markdown";
import { SkillCandidatePanel } from "./SkillCandidatePanel";
import { TraceTimeline } from "./TraceTimeline";

type WorkspaceTab = "report" | "chart" | "data" | "code" | "team" | "trace";
type ArtifactTab = Exclude<WorkspaceTab, "team" | "trace">;
type WorkspaceSection = "evidence" | "runtime";

interface ArtifactWorkspaceProps {
  task: TaskDetail;
  agents: AgentRun[];
  runs: AnalysisRun[];
  traceEvents: TraceEvent[];
  selectedRunId: string;
  candidate: SkillCandidate | null;
  curating: boolean;
  onSelectRun(runId: string): Promise<void>;
  onAcceptRun(runId: string): Promise<void>;
  onDecideSkill(id: string, approved: boolean): Promise<void>;
  drawerOpen: boolean;
  onClose(): void;
}

const TAB_LABELS: Record<WorkspaceTab, string> = {
  report: "报告",
  chart: "图表",
  data: "数据",
  code: "代码",
  team: "Agent",
  trace: "Trace·Skill",
};
const EMPTY_LABELS: Record<ArtifactTab, string> = {
  report: "暂无报告产物",
  chart: "暂无图表产物",
  data: "暂无数据产物",
  code: "暂无代码产物",
};
const SECTION_TABS: Record<WorkspaceSection, WorkspaceTab[]> = {
  evidence: ["report", "chart", "data", "code"],
  runtime: ["team", "trace"],
};

function artifactTab(artifact: Artifact): ArtifactTab {
  const type = artifact.artifact_type.toLowerCase();
  if (
    type.includes("chart") ||
    type.includes("plot") ||
    type.includes("visual")
  )
    return "chart";
  if (
    type.includes("dataset") ||
    type.includes("table") ||
    type.includes("csv") ||
    type.includes("data")
  )
    return "data";
  if (
    type.includes("code") ||
    type.includes("python") ||
    type.includes("sql") ||
    type.includes("notebook")
  )
    return "code";
  return "report";
}

export function ArtifactWorkspace({
  task,
  agents,
  runs,
  traceEvents,
  selectedRunId,
  candidate,
  curating,
  onSelectRun,
  onAcceptRun,
  onDecideSkill,
  drawerOpen,
  onClose,
}: ArtifactWorkspaceProps) {
  const [activeTab, setActiveTab] = useState<WorkspaceTab>("report");
  const [activeSection, setActiveSection] =
    useState<WorkspaceSection>("evidence");
  const latestConclusion = [...task.messages]
    .reverse()
    .find((message) => message.role === "assistant")?.content;
  const grouped = useMemo(() => {
    const result: Record<ArtifactTab, Artifact[]> = {
      report: [],
      chart: [],
      data: [],
      code: [],
    };
    for (const artifact of task.artifacts)
      result[artifactTab(artifact)].push(artifact);
    return result;
  }, [task.artifacts]);
  const selectedRun = runs.find((run) => run.id === selectedRunId);
  const tabCount = (tab: WorkspaceTab) =>
    tab === "team"
      ? agents.length
      : tab === "trace"
        ? runs.length
        : grouped[tab].length;

  function selectSection(section: WorkspaceSection) {
    setActiveSection(section);
    if (!SECTION_TABS[section].includes(activeTab)) {
      setActiveTab(section === "evidence" ? "report" : "team");
    }
  }

  return (
    <aside
      className={
        drawerOpen ? "artifact-workspace drawer-open" : "artifact-workspace"
      }
      aria-label="证据与运行"
    >
      <header className="artifact-workspace-header">
        <div>
          <span>ANALYSIS RECORD</span>
          <h2>证据与运行</h2>
        </div>
        <div className="artifact-header-actions">
          <span>
            {task.artifacts.length} 证据 · {runs.length} 运行
          </span>
          <button
            type="button"
            className="artifact-collapse"
            aria-label="隐藏证据与运行"
            onClick={onClose}
          >
            ›
          </button>
        </div>
      </header>
      <div
        className="workspace-section-switch"
        role="tablist"
        aria-label="内容分区"
      >
        <button
          type="button"
          role="tab"
          aria-label="证据"
          aria-selected={activeSection === "evidence"}
          className={activeSection === "evidence" ? "active" : ""}
          onClick={() => selectSection("evidence")}
        >
          <span aria-hidden="true">◇</span>
          <span>
            <strong>证据</strong>
            <small>结论与可复现产物</small>
          </span>
          <b>{task.artifacts.length}</b>
        </button>
        <button
          type="button"
          role="tab"
          aria-label="运行"
          aria-selected={activeSection === "runtime"}
          className={activeSection === "runtime" ? "active" : ""}
          onClick={() => selectSection("runtime")}
        >
          <span aria-hidden="true">↳</span>
          <span>
            <strong>运行</strong>
            <small>Agent、回放与 Skill</small>
          </span>
          <b>{runs.length}</b>
        </button>
      </div>
      <div
        className={`artifact-tabs ${activeSection}`}
        role="tablist"
        aria-label={activeSection === "evidence" ? "证据类型" : "运行类型"}
      >
        {SECTION_TABS[activeSection].map((tab) => (
          <button
            type="button"
            role="tab"
            id={`artifact-tab-${tab}`}
            aria-controls="artifact-tab-panel"
            aria-selected={activeTab === tab}
            className={activeTab === tab ? "active" : ""}
            key={tab}
            onClick={() => setActiveTab(tab)}
          >
            {TAB_LABELS[tab]}
            <span>{tabCount(tab)}</span>
          </button>
        ))}
      </div>
      <div
        className="artifact-scroll"
        id="artifact-tab-panel"
        role="tabpanel"
        aria-labelledby={`artifact-tab-${activeTab}`}
      >
        {activeTab === "team" && <AgentTeamPanel agents={agents} />}
        {activeTab === "trace" && (
          <section
            className="runtime-trace"
            aria-label="Trace 回放与 Skill 沉淀"
          >
            <div className="panel-heading">
              <div>
                <span className="section-label">REPLAY / CURATE</span>
                <h3>运行回放</h3>
              </div>
              <span>{runs.length} RUNS</span>
            </div>
            {runs.length ? (
              <>
                <label className="trace-run-picker">
                  选择运行
                  <select
                    value={selectedRunId}
                    onChange={(event) => void onSelectRun(event.target.value)}
                  >
                    <option value="">选择运行</option>
                    {runs.map((run) => (
                      <option key={run.id} value={run.id}>
                        {run.id.slice(0, 8)} · {run.status}
                        {run.accepted ? " · accepted" : ""}
                      </option>
                    ))}
                  </select>
                </label>
                {selectedRun && (
                  <div className="run-review-bar">
                    <span className={`run-state ${selectedRun.status}`}>
                      {selectedRun.status}
                    </span>
                    {selectedRun.accepted ? (
                      <strong>已接受，Curator 将沉淀候选 Skill</strong>
                    ) : (
                      <button
                        type="button"
                        className="primary-action"
                        disabled={
                          selectedRun.status !== "completed" || curating
                        }
                        onClick={() => void onAcceptRun(selectedRun.id)}
                      >
                        {curating ? "Curator 处理中" : "接受运行并沉淀 Skill"}
                      </button>
                    )}
                    {selectedRun.accepted && !candidate && (
                      <button
                        type="button"
                        onClick={() => void onSelectRun(selectedRun.id)}
                      >
                        刷新 Candidate
                      </button>
                    )}
                    <a
                      className="replay-export"
                      href={`/api/runs/${selectedRun.id}/replay`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      完整回放 JSON
                    </a>
                  </div>
                )}
                {traceEvents.length ? (
                  <TraceTimeline events={traceEvents} />
                ) : (
                  <p className="empty-note">选择运行后加载脱敏 Trace。</p>
                )}
                {candidate && (
                  <SkillCandidatePanel
                    candidate={candidate}
                    onDecision={onDecideSkill}
                  />
                )}
              </>
            ) : (
              <p className="empty-note">
                尚无运行。完成一次分析后，这里会保留
                Lead、Teammate、SQL、审批和产物链路。
              </p>
            )}
          </section>
        )}
        {(activeTab === "report" ||
          activeTab === "chart" ||
          activeTab === "data" ||
          activeTab === "code") && (
          <>
            {activeTab === "report" && (
              <section
                className="report-conclusion"
                aria-labelledby="report-conclusion-heading"
              >
                <span>核心结论</span>
                <h3 id="report-conclusion-heading">分析摘要</h3>
                <Markdown
                  content={
                    latestConclusion ?? "Agent 完成分析后，结论会沉淀在这里。"
                  }
                />
              </section>
            )}
            {grouped[activeTab].length > 0 ? (
              <div className={`artifact-grid ${activeTab}`}>
                {grouped[activeTab].map((artifact) => (
                  <ArtifactCard
                    artifact={artifact}
                    tab={activeTab}
                    key={artifact.id}
                  />
                ))}
              </div>
            ) : (
              <div className="artifact-empty">
                <span aria-hidden="true">
                  {activeTab === "chart"
                    ? "⌁"
                    : activeTab === "data"
                      ? "▦"
                      : activeTab === "code"
                        ? "</>"
                        : "≋"}
                </span>
                <strong>{EMPTY_LABELS[activeTab]}</strong>
                <p>Agent 生成并保存后，会自动归档到这个标签页。</p>
              </div>
            )}
          </>
        )}
      </div>
    </aside>
  );
}

function ArtifactCard({
  artifact,
  tab,
}: {
  artifact: Artifact;
  tab: ArtifactTab;
}) {
  const [exportFormat, setExportFormat] = useState<"markdown" | "pdf">("pdf");
  const canPreviewImage =
    tab === "chart" && artifact.content_type?.startsWith("image/");
  const canPreviewHtml =
    tab === "chart" && artifact.content_type === "text/html";
  const canExportReport = artifact.artifact_type === "markdown_report";
  return (
    <article className="artifact-card">
      {canPreviewImage && (
        <img src={artifact.content_url} alt={artifact.title} />
      )}
      {canPreviewHtml && (
        <iframe
          src={artifact.content_url}
          title={`${artifact.title} 预览`}
          loading="lazy"
          sandbox="allow-scripts"
          referrerPolicy="no-referrer"
        />
      )}
      <div className="artifact-card-body">
        <span>{artifact.artifact_type.replaceAll("_", " ")}</span>
        <h3>{artifact.title}</h3>
        <p>{artifact.description || "已登记分析产物"}</p>
        <div className="artifact-card-actions">
          <a
            href={artifact.content_url}
            target="_blank"
            rel="noreferrer"
            aria-label={`打开 ${artifact.title}`}
          >
            打开产物 <span aria-hidden="true">→</span>
          </a>
          {canExportReport && (
            <div className="report-export-control">
              <label>
                <span className="sr-only">{artifact.title} 导出格式</span>
                <select
                  value={exportFormat}
                  aria-label={`${artifact.title} 导出格式`}
                  onChange={(event) =>
                    setExportFormat(event.target.value as "markdown" | "pdf")
                  }
                >
                  <option value="pdf">PDF</option>
                  <option value="markdown">Markdown</option>
                </select>
              </label>
              <a
                className="report-export-link"
                href={`/api/artifacts/${artifact.id}/export?format=${exportFormat}`}
                download
              >
                导出 <span aria-hidden="true">↓</span>
              </a>
            </div>
          )}
        </div>
      </div>
    </article>
  );
}
