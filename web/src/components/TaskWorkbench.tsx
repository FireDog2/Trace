import type { TaskDetail, TaskEvent } from "../types";

interface TaskWorkbenchProps {
  task: TaskDetail;
  events: TaskEvent[];
}

const STATUS_LABELS: Record<string, string> = {
  understanding: "理解问题",
  planning: "制定计划",
  running: "执行分析",
  synthesizing: "综合结论",
  needs_input: "等待输入",
  failed: "执行失败",
  cancelled: "已取消",
  completed: "分析完成",
};

export function TaskWorkbench({ task, events }: TaskWorkbenchProps) {
  return (
    <main className="task-workbench">
      <header className="task-heading">
        <div>
          <div className="section-label">当前任务 · {task.plan ? `计划 v${task.plan.revision}` : "尚未规划"}</div>
          <h1>{task.objective}</h1>
        </div>
        <span className={`task-state ${task.status}`}>{STATUS_LABELS[task.status] ?? task.status}</span>
      </header>

      <div className="workbench-grid">
        <section className="analysis-panel plan-panel">
          <div className="panel-heading">
            <h2>分析计划</h2>
            <span>{task.plan?.steps.length ?? 0} 步</span>
          </div>
          {task.plan ? (
            <ol className="evidence-spine">
              {task.plan.steps.map((step) => (
                <li key={step.id} className={step.status}>
                  <span className="spine-index">{String(step.position).padStart(2, "0")}</span>
                  <div>
                    <strong>{step.title}</strong>
                    <small>{STATUS_LABELS[step.status] ?? step.status}</small>
                  </div>
                </li>
              ))}
            </ol>
          ) : (
            <p className="empty-note">Agent 读取数据后，分析步骤会沉淀在这里。</p>
          )}
        </section>

        <section className="analysis-panel activity-panel">
          <div className="panel-heading">
            <h2>执行记录</h2>
            <span>LIVE</span>
          </div>
          {events.length > 0 ? (
            <ul className="event-log">
              {events.slice(-8).map((event) => (
                <li key={`${event.sequence}-${event.type}`}>
                  <span>{String(event.sequence).padStart(3, "0")}</span>
                  <strong>{event.type.replace(".", " / ")}</strong>
                </li>
              ))}
            </ul>
          ) : (
            <p className="empty-note">新的工具调用和分析进度会按顺序出现。</p>
          )}
        </section>
      </div>

      <section className="artifact-panel">
        <div className="panel-heading">
          <h2>分析产物</h2>
          <span>{task.artifacts.length} 项</span>
        </div>
        {task.artifacts.length > 0 ? (
          <ul className="artifact-table">
            {task.artifacts.map((artifact) => (
              <li key={artifact.id}>
                <a
                  href={artifact.content_url}
                  target="_blank"
                  rel="noreferrer"
                  aria-label={artifact.title}
                >
                  <span className="artifact-type">{artifact.artifact_type.replaceAll("_", " ")}</span>
                  <strong>{artifact.title}</strong>
                  <span className="artifact-description">{artifact.description || "已登记分析产物"}</span>
                  <span aria-hidden="true">↗</span>
                </a>
              </li>
            ))}
          </ul>
        ) : (
          <p className="empty-note">代码、图表、数据表和报告会作为可追溯产物保存在这里。</p>
        )}
      </section>
    </main>
  );
}
