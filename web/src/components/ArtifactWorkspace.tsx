import { useEffect, useMemo, useState } from "react";

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
  onFailureReview(runId: string): Promise<void>;
  onDecideSkill(id: string, approved: boolean): Promise<void>;
  drawerOpen: boolean;
  onClose(): void;
}

const TAB_LABELS: Record<WorkspaceTab, string> = {
  report: "Reports",
  chart: "Charts",
  data: "Data",
  code: "Code",
  team: "Agent",
  trace: "Trace·Skill",
};
const EMPTY_LABELS: Record<ArtifactTab, string> = {
  report: "No report artifacts",
  chart: "No chart artifacts",
  data: "No data artifacts",
  code: "No code artifacts",
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
  onFailureReview,
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
    result.report.sort((left, right) =>
      right.created_at.localeCompare(left.created_at),
    );
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
      aria-label="Evidence & runs"
    >
      <header className="artifact-workspace-header">
        <div>
          <span>ANALYSIS RECORD</span>
          <h2>Evidence & runs</h2>
        </div>
        <div className="artifact-header-actions">
          <span>
            {task.artifacts.length} evidence · {runs.length} runs
          </span>
          <button
            type="button"
            className="artifact-collapse"
            aria-label="Hide evidence and runs"
            onClick={onClose}
          >
            ›
          </button>
        </div>
      </header>
      <div
        className="workspace-section-switch"
        role="tablist"
        aria-label="Content sections"
      >
        <button
          type="button"
          role="tab"
          aria-label="Evidence"
          aria-selected={activeSection === "evidence"}
          className={activeSection === "evidence" ? "active" : ""}
          onClick={() => selectSection("evidence")}
        >
          <span aria-hidden="true">◇</span>
          <span>
            <strong>Evidence</strong>
            <small>Findings and reproducible artifacts</small>
          </span>
          <b>{task.artifacts.length}</b>
        </button>
        <button
          type="button"
          role="tab"
          aria-label="Runs"
          aria-selected={activeSection === "runtime"}
          className={activeSection === "runtime" ? "active" : ""}
          onClick={() => selectSection("runtime")}
        >
          <span aria-hidden="true">↳</span>
          <span>
            <strong>Runs</strong>
            <small>Agents, replays, and skills</small>
          </span>
          <b>{runs.length}</b>
        </button>
      </div>
      <div
        className={`artifact-tabs ${activeSection}`}
        role="tablist"
        aria-label={
          activeSection === "evidence" ? "Evidence types" : "Run types"
        }
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
            aria-label="Trace replay and skill curation"
          >
            <div className="panel-heading">
              <div>
                <span className="section-label">REPLAY / CURATE</span>
                <h3>Run replay</h3>
              </div>
              <span>{runs.length} RUNS</span>
            </div>
            {runs.length ? (
              <>
                <label className="trace-run-picker">
                  Select a run
                  <select
                    value={selectedRunId}
                    onChange={(event) => void onSelectRun(event.target.value)}
                  >
                    <option value="">Select a run</option>
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
                    {selectedRun.accepted || selectedRun.review_requested ? (
                      <strong>
                        {selectedRun.review_requested
                          ? "Failure review requested. The curator will prepare a review candidate."
                          : "Accepted. The curator will create a skill candidate."}
                      </strong>
                    ) : selectedRun.status === "failed" ? (
                      <button
                        type="button"
                        className="primary-action"
                        disabled={curating}
                        onClick={() => void onFailureReview(selectedRun.id)}
                      >
                        {curating
                          ? "Preparing failure review"
                          : "Review failed run"}
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="primary-action"
                        disabled={
                          selectedRun.status !== "completed" || curating
                        }
                        onClick={() => void onAcceptRun(selectedRun.id)}
                      >
                        {curating
                          ? "Curator is working"
                          : "Accept run and create skill candidate"}
                      </button>
                    )}
                    {selectedRun.accepted && !candidate && (
                      <button
                        type="button"
                        onClick={() => void onSelectRun(selectedRun.id)}
                      >
                        Refresh candidate
                      </button>
                    )}
                    <a
                      className="replay-export"
                      href={`/api/runs/${selectedRun.id}/replay`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Full replay JSON
                    </a>
                  </div>
                )}
                {traceEvents.length ? (
                  <TraceTimeline events={traceEvents} />
                ) : (
                  <p className="empty-note">
                    Select a run to load its sanitized trace.
                  </p>
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
                No runs yet. After an analysis completes, this area will retain
                lead agents, teammates, SQL, approvals, and artifact lineage.
              </p>
            )}
          </section>
        )}
        {(activeTab === "report" ||
          activeTab === "chart" ||
          activeTab === "data" ||
          activeTab === "code") && (
          <>
            {activeTab === "report" && grouped.report.length > 0 && (
              <div className="artifact-grid report">
                {grouped.report.map((artifact) => (
                  <ArtifactCard
                    artifact={artifact}
                    tab="report"
                    key={artifact.id}
                  />
                ))}
              </div>
            )}
            {activeTab === "report" && (
              <section
                className="report-conclusion"
                aria-labelledby="report-conclusion-heading"
              >
                <span>Key findings</span>
                <h3 id="report-conclusion-heading">Analysis summary</h3>
                <Markdown
                  content={
                    latestConclusion ??
                    "Findings will be saved here after the agent completes its analysis."
                  }
                />
              </section>
            )}
            {activeTab !== "report" && grouped[activeTab].length > 0 && (
              <div className={`artifact-grid ${activeTab}`}>
                {grouped[activeTab].map((artifact) => (
                  <ArtifactCard
                    artifact={artifact}
                    tab={activeTab}
                    key={artifact.id}
                  />
                ))}
              </div>
            )}
            {grouped[activeTab].length === 0 && (
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
                <p>
                  Artifacts generated and saved by the agent will be filed under
                  this tab automatically.
                </p>
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
  const [reportContent, setReportContent] = useState("");
  const [reportPreviewState, setReportPreviewState] = useState<
    "idle" | "loading" | "ready" | "failed"
  >("idle");
  const normalizedContentType = artifact.content_type?.toLowerCase() ?? "";
  const canPreviewImage =
    tab === "chart" && normalizedContentType.startsWith("image/");
  const canPreviewHtml =
    tab === "chart" &&
    (normalizedContentType.startsWith("text/html") ||
      /\.html?$/i.test(artifact.title));
  const previewUrl = `${artifact.content_url}${artifact.content_url.includes("?") ? "&" : "?"}preview=true`;
  const canExportReport = artifact.artifact_type === "markdown_report";

  useEffect(() => {
    if (!canExportReport) {
      setReportContent("");
      setReportPreviewState("idle");
      return;
    }
    const controller = new AbortController();
    setReportContent("");
    setReportPreviewState("loading");
    void fetch(artifact.content_url, {
      headers: { Accept: "text/markdown, text/plain;q=0.9" },
      signal: controller.signal,
    })
      .then((response) => {
        if (!response.ok)
          throw new Error(`Report preview failed: ${response.status}`);
        return response.text();
      })
      .then((content) => {
        setReportContent(content);
        setReportPreviewState("ready");
      })
      .catch((cause: unknown) => {
        if (cause instanceof DOMException && cause.name === "AbortError")
          return;
        setReportPreviewState("failed");
      });
    return () => controller.abort();
  }, [artifact.content_url, canExportReport]);

  return (
    <article
      className={
        canExportReport ? "artifact-card report-document" : "artifact-card"
      }
    >
      {canPreviewImage && <img src={previewUrl} alt={artifact.title} />}
      {canPreviewHtml && (
        <iframe
          src={previewUrl}
          title={`${artifact.title} preview`}
          loading="lazy"
          sandbox="allow-scripts"
          referrerPolicy="no-referrer"
        />
      )}
      {canExportReport && (
        <section
          className="report-document-preview"
          aria-label={`${artifact.title} report content`}
          aria-busy={reportPreviewState === "loading"}
        >
          <header>
            <span>RENDERED REPORT</span>
            <strong>{artifact.title}</strong>
          </header>
          {reportPreviewState === "loading" && (
            <p className="report-preview-status">Loading report…</p>
          )}
          {reportPreviewState === "failed" && (
            <p className="report-preview-status failed">
              Report preview is unavailable. Open or export the artifact below.
            </p>
          )}
          {reportPreviewState === "ready" && (
            <Markdown content={reportContent} className="report-markdown" />
          )}
        </section>
      )}
      <div className="artifact-card-body">
        <span>{artifact.artifact_type.replaceAll("_", " ")}</span>
        <h3>{artifact.title}</h3>
        <p>{artifact.description || "Registered analysis artifact"}</p>
        <div className="artifact-card-actions">
          <a
            href={artifact.content_url}
            target="_blank"
            rel="noreferrer"
            aria-label={`Open ${artifact.title}`}
          >
            Open artifact <span aria-hidden="true">→</span>
          </a>
          {canExportReport && (
            <div className="report-export-control">
              <label>
                <span className="sr-only">{artifact.title} export format</span>
                <select
                  value={exportFormat}
                  aria-label={`${artifact.title} export format`}
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
                Export <span aria-hidden="true">↓</span>
              </a>
            </div>
          )}
        </div>
      </div>
    </article>
  );
}
