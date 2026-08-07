import { useMemo, useState } from "react";

import type { Artifact, TaskDetail } from "../types";

type ArtifactTab = "report" | "chart" | "data" | "code";

interface ArtifactWorkspaceProps {
  task: TaskDetail;
  drawerOpen: boolean;
  onClose(): void;
}

const TAB_LABELS: Record<ArtifactTab, string> = {
  report: "报告",
  chart: "图表",
  data: "数据",
  code: "代码",
};

const EMPTY_LABELS: Record<ArtifactTab, string> = {
  report: "暂无报告产物",
  chart: "暂无图表产物",
  data: "暂无数据产物",
  code: "暂无代码产物",
};

function artifactTab(artifact: Artifact): ArtifactTab {
  const type = artifact.artifact_type.toLowerCase();
  if (type.includes("chart") || type.includes("plot") || type.includes("visual")) return "chart";
  if (type.includes("dataset") || type.includes("table") || type.includes("csv") || type.includes("data")) return "data";
  if (type.includes("code") || type.includes("python") || type.includes("sql") || type.includes("notebook")) return "code";
  return "report";
}

export function ArtifactWorkspace({ task, drawerOpen, onClose }: ArtifactWorkspaceProps) {
  const [activeTab, setActiveTab] = useState<ArtifactTab>("report");
  const latestConclusion = [...task.messages]
    .reverse()
    .find((message) => message.role === "assistant")?.content;

  const grouped = useMemo(() => {
    const result: Record<ArtifactTab, Artifact[]> = { report: [], chart: [], data: [], code: [] };
    for (const artifact of task.artifacts) result[artifactTab(artifact)].push(artifact);
    return result;
  }, [task.artifacts]);

  const currentArtifacts = grouped[activeTab];

  return (
    <aside
      className={drawerOpen ? "artifact-workspace drawer-open" : "artifact-workspace"}
      aria-label="最终产物"
    >
      <header className="artifact-workspace-header">
        <div>
          <span>DELIVERABLES</span>
          <h2>最终产物</h2>
        </div>
        <div className="artifact-header-actions">
          <span>{task.artifacts.length} 项</span>
          <button type="button" className="artifact-close" aria-label="关闭最终产物" onClick={onClose}>×</button>
        </div>
      </header>

      <div className="artifact-tabs" role="tablist" aria-label="产物类型">
        {(Object.keys(TAB_LABELS) as ArtifactTab[]).map((tab) => (
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
            <span>{grouped[tab].length}</span>
          </button>
        ))}
      </div>

      <div
        className="artifact-scroll"
        id="artifact-tab-panel"
        role="tabpanel"
        aria-labelledby={`artifact-tab-${activeTab}`}
      >
        {activeTab === "report" && (
          <section className="report-conclusion" aria-labelledby="report-conclusion-heading">
            <span>核心结论</span>
            <h3 id="report-conclusion-heading">分析摘要</h3>
            <p>{latestConclusion ?? "Agent 完成分析后，结论会沉淀在这里。"}</p>
          </section>
        )}

        {currentArtifacts.length > 0 ? (
          <div className={`artifact-grid ${activeTab}`}>
            {currentArtifacts.map((artifact) => (
              <ArtifactCard artifact={artifact} tab={activeTab} key={artifact.id} />
            ))}
          </div>
        ) : (
          <div className="artifact-empty">
            <span aria-hidden="true">{activeTab === "chart" ? "⌁" : activeTab === "data" ? "▦" : activeTab === "code" ? "</>" : "≡"}</span>
            <strong>{EMPTY_LABELS[activeTab]}</strong>
            <p>Agent 生成并保存后，会自动归档到这个标签页。</p>
          </div>
        )}
      </div>
    </aside>
  );
}

function ArtifactCard({ artifact, tab }: { artifact: Artifact; tab: ArtifactTab }) {
  const canPreviewImage = tab === "chart" && artifact.content_type?.startsWith("image/");
  const canPreviewHtml = tab === "chart" && artifact.content_type === "text/html";

  return (
    <article className="artifact-card">
      {canPreviewImage && <img src={artifact.content_url} alt={artifact.title} />}
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
        <a href={artifact.content_url} target="_blank" rel="noreferrer" aria-label={`打开 ${artifact.title}`}>
          打开产物 <span aria-hidden="true">↗</span>
        </a>
      </div>
    </article>
  );
}
