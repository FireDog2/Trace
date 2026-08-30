import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { TaskDetail, TaskEvent } from "../types";
import { Conversation } from "./Conversation";

const task: TaskDetail = {
  id: "task-1",
  project_id: "project-1",
  objective: "region_operations.logistics_cost 我想访问这个字段",
  status: "completed",
  created_at: "2026-08-14T00:00:00Z",
  updated_at: "2026-08-14T00:05:00Z",
  messages: [
    {
      id: "tool-1",
      role: "tool",
      content: "SQL agent started",
      created_at: "2026-08-14T00:01:00Z",
    },
    {
      id: "answer-1",
      role: "assistant",
      content: "已确认可以访问 logistics_cost 字段。",
      created_at: "2026-08-14T00:05:00Z",
    },
  ],
  plan: null,
  artifacts: [],
};

const events: TaskEvent[] = [
  {
    sequence: 6,
    type: "approval.decided",
    payload: { approval_id: "approval-1", status: "approved" },
  },
  {
    sequence: 3,
    type: "message.created",
    payload: { message_id: "tool-1", role: "tool" },
  },
  {
    sequence: 7,
    type: "message.created",
    payload: { message_id: "answer-1", role: "assistant" },
  },
  {
    sequence: 4,
    type: "approval.requested",
    payload: {
      approval_id: "approval-1",
      tool_name: "query_database",
      reason: "Restricted fields require one-time approval",
    },
  },
];

describe("Conversation timeline", () => {
  it("shows the original objective as the first chat message", () => {
    const { container } = render(
      <Conversation
        task={task}
        events={events}
        onUpload={vi.fn()}
        onSend={vi.fn()}
        onApproval={vi.fn()}
      />,
    );

    expect(
      container.querySelector(".conversation-opening .message.user p"),
    ).toHaveTextContent("region_operations.logistics_cost 我想访问这个字段");
  });

  it("keeps a decided approval at its request position before the final answer", () => {
    render(
      <Conversation
        task={task}
        events={events}
        onUpload={vi.fn()}
        onSend={vi.fn()}
        onApproval={vi.fn()}
      />,
    );

    const approval = screen
      .getByText("Restricted fields require one-time approval")
      .closest(".approval-card");
    const answer = screen.getByText("已确认可以访问 logistics_cost 字段。");

    expect(approval).toHaveClass("approved");
    expect(approval?.compareDocumentPosition(answer)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
  });
});
