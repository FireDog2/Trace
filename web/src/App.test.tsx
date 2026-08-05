import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { App } from "./App";
import type { ApiClient } from "./api";

function fakeApiWithCompletedTask(): ApiClient {
  return {
    listProjects: async () => [
      {
        id: "project-1",
        name: "2026 经营复盘",
        status: "active",
        created_at: "2026-08-05T00:00:00Z",
        updated_at: "2026-08-05T00:00:00Z",
      },
    ],
    createProject: async () => {
      throw new Error("Not used in this test");
    },
    uploadDataset: async () => {
      throw new Error("Not used in this test");
    },
    listArtifacts: async () => [
      {
        id: "source-1",
        project_id: "project-1",
        task_id: null,
        artifact_type: "source_dataset",
        title: "operations.csv",
        description: "经营数据",
        content_type: "text/csv",
        created_at: "2026-08-05T00:00:00Z",
        content_url: "/api/artifacts/source-1/content",
      },
    ],
    listTasks: async () => [
      {
        id: "task-1",
        project_id: "project-1",
        objective: "华东利润诊断",
        status: "completed",
        created_at: "2026-08-05T00:00:00Z",
        updated_at: "2026-08-05T00:05:00Z",
      },
    ],
    createTask: async () => {
      throw new Error("Not used in this test");
    },
    getTask: async () => ({
      id: "task-1",
      project_id: "project-1",
      objective: "华东利润诊断",
      status: "completed",
      created_at: "2026-08-05T00:00:00Z",
      updated_at: "2026-08-05T00:05:00Z",
      messages: [
        {
          id: "message-1",
          role: "assistant",
          content: "物流成本增长是主要变化。",
          created_at: "2026-08-05T00:05:00Z",
        },
      ],
      plan: {
        id: "plan-1",
        revision: 1,
        reason: "初始分析计划",
        steps: [
          { id: "step-1", position: 1, title: "检查数据", status: "completed" },
        ],
      },
      artifacts: [
        {
          id: "chart-1",
          project_id: "project-1",
          task_id: "task-1",
          artifact_type: "chart",
          title: "物流成本趋势",
          description: "华东物流成本变化",
          content_type: "text/html",
          created_at: "2026-08-05T00:04:00Z",
          content_url: "/api/artifacts/chart-1/content",
        },
      ],
    }),
    sendMessage: async () => {
      throw new Error("Not used in this test");
    },
    subscribeToTask: () => () => undefined,
  };
}

describe("analysis workbench", () => {
  it("keeps conversation separate from durable task artifacts", async () => {
    render(<App api={fakeApiWithCompletedTask()} />);

    expect(await screen.findByRole("heading", { name: "华东利润诊断" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "分析计划" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "分析产物" })).toBeVisible();
    expect(screen.getByRole("textbox", { name: "给分析 Agent 留言" })).toBeVisible();
    expect(screen.getByRole("link", { name: "物流成本趋势" })).toHaveAttribute(
      "href",
      "/api/artifacts/chart-1/content",
    );
  });
});
