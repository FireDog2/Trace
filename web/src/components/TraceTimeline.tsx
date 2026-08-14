import { CSSProperties } from "react";
import type { TraceEvent } from "../types";
const labels: Record<string, string> = {
  "agent.started": "Agent 启动",
  "tool.started": "工具调用开始",
  "tool.completed": "工具调用完成",
  "tool.failed": "工具调用失败",
  "sql.validated": "SQL 校验",
  "approval.requested": "请求人工审批",
};
export function TraceTimeline({ events }: { events: TraceEvent[] }) {
  const byId = new Map(events.map((e) => [e.id, e]));
  const depth = (item: TraceEvent) => {
    let n = 0;
    let parent = item.parent_event_id
      ? byId.get(item.parent_event_id)
      : undefined;
    while (parent && n < 5) {
      n++;
      parent = parent.parent_event_id
        ? byId.get(parent.parent_event_id)
        : undefined;
    }
    return n;
  };
  return (
    <ol className="trace-timeline">
      {[...events]
        .sort((a, b) => a.sequence - b.sequence)
        .map((event) => (
          <li
            key={event.id}
            style={{ "--trace-depth": depth(event) } as CSSProperties}
          >
            <span className="trace-sequence">
              {String(event.sequence).padStart(3, "0")}
            </span>
            <div>
              <strong>{labels[event.event_type] ?? event.event_type}</strong>
              <code>{event.event_type}</code>
              <span className={`trace-status ${event.status}`}>
                {event.status}
              </span>
              {event.attempt > 1 && <span>第 {event.attempt} 次尝试</span>}
              {event.duration_ms != null && <span>{event.duration_ms} ms</span>}
              {event.error_category && <code>{event.error_category}</code>}
              <details>
                <summary>安全 payload</summary>
                <pre>{JSON.stringify(event.payload, null, 2)}</pre>
              </details>
            </div>
          </li>
        ))}
    </ol>
  );
}
