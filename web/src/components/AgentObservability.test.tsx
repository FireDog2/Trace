import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AgentTeamPanel } from "./AgentTeamPanel";
import { SkillCandidatePanel } from "./SkillCandidatePanel";
import { TraceTimeline } from "./TraceTimeline";

describe("agent observability", () => {
  it("shows role, objective, state, and safe failure", () => {
    render(<AgentTeamPanel agents={[{ id: "a", task_id: "t", role: "sql", objective: "查询区域利润", status: "failed", result_summary: null, error_category: "query_timeout", created_at: "", started_at: null, completed_at: null }]} />);
    expect(screen.getByText("SQL Agent")).toBeVisible();
    expect(screen.getByText("查询区域利润")).toBeVisible();
    expect(screen.getByText("query_timeout")).toBeVisible();
  });
  it("orders trace by sequence and exposes retry", () => {
    render(<TraceTimeline events={[{ id: "two", run_id: "r", task_id: "t", agent_id: null, parent_event_id: "one", sequence: 2, event_type: "sql.validated", status: "completed", payload: {}, duration_ms: 10, attempt: 2, error_category: null, artifact_ids: [] }, { id: "one", run_id: "r", task_id: "t", agent_id: null, parent_event_id: null, sequence: 1, event_type: "agent.started", status: "completed", payload: {}, duration_ms: null, attempt: 1, error_category: null, artifact_ids: [] }]} />);
    expect(screen.getAllByRole("listitem")[0]).toHaveTextContent("Agent 启动");
    expect(screen.getByText("第 2 次尝试")).toBeVisible();
  });
  it("approves without enabling a skill", () => {
    const onDecision = vi.fn(async () => undefined);
    render(<SkillCandidatePanel candidate={{ id: "c", source_run_id: "r", name: "Revenue review", version: 1, markdown: "# skill", status: "candidate", enabled: false, generated_by_model: "test", reviewed_at: null, created_at: "" }} onDecision={onDecision} />);
    fireEvent.click(screen.getByRole("button", { name: "批准候选 Skill" }));
    expect(onDecision).toHaveBeenCalledWith("c", true);
    expect(screen.getByText(/始终禁用/)).toBeVisible();
  });
});
