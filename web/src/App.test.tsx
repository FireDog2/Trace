import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { App } from "./App";
import type { ApiClient } from "./api";

const PROVIDERS = [
  {
    provider: "deepseek",
    label: "DeepSeek",
    base_url: "https://api.deepseek.com",
    models: ["deepseek-v4-flash", "deepseek-v4-pro"],
    default_model: "deepseek-v4-flash",
    custom_url: false,
  },
  {
    provider: "openai",
    label: "OpenAI",
    base_url: "https://api.openai.com/v1",
    models: ["gpt-5.6-terra", "gpt-5.6-sol", "gpt-5.6-luna"],
    default_model: "gpt-5.6-terra",
    custom_url: false,
  },
  {
    provider: "custom",
    label: "第三方中转站",
    base_url: null,
    models: [],
    default_model: "",
    custom_url: true,
  },
];

const CONFIGURED_MODEL = {
  provider: "openai",
  base_url: "https://api.openai.com/v1",
  model: "gpt-5.6-terra",
  configured: true,
  has_api_key: true,
  updated_at: "2026-08-05T00:00:00Z",
  providers: PROVIDERS,
};

const UNCONFIGURED_MODEL = {
  provider: null,
  base_url: "",
  model: "",
  configured: false,
  has_api_key: false,
  updated_at: null,
  providers: PROVIDERS,
};

function fakeApiWithCompletedTask(
  approvalEvent = false,
  decideApproval = vi.fn(async () => ({
    id: "approval-1",
    status: "approved",
    decided_at: "2026-08-05T00:06:00Z",
  })),
): ApiClient {
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
    decideApproval,
    getModelConfiguration: async () => CONFIGURED_MODEL,
    saveModelConfiguration: async () => CONFIGURED_MODEL,
    subscribeToTask: (_taskId, onEvent) => {
      if (approvalEvent) {
        onEvent({
          sequence: 12,
          type: "approval.requested",
          payload: {
            approval_id: "approval-1",
            tool_name: "save_artifact",
            reason: "覆盖已有产物需要确认",
          },
        });
      }
      return () => undefined;
    },
  };
}

describe("analysis workbench", () => {
  it("renders project context, conversation, and artifacts in workspace order", async () => {
    render(<App api={fakeApiWithCompletedTask()} />);

    const projectContext = await screen.findByRole("navigation", { name: "项目上下文" });
    const conversation = await screen.findByRole("main", { name: "分析对话" });
    const artifacts = await screen.findByRole("complementary", { name: "最终产物" });
    const shell = projectContext.parentElement;

    expect(shell).not.toBeNull();
    expect(shell?.querySelectorAll(":scope > nav, :scope > main, :scope > aside")).toHaveLength(3);
    expect(shell?.querySelector(".task-workbench")).not.toBeInTheDocument();

    expect(projectContext.compareDocumentPosition(conversation)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
    expect(conversation.compareDocumentPosition(artifacts)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
  });

  it("uses the TRACE brand and groups model settings by responsibility", async () => {
    render(<App api={fakeApiWithCompletedTask()} />);

    expect(await screen.findByText("TRACE", { selector: ".brand-block strong" })).toBeVisible();
    expect(screen.getByText("LOCAL ANALYSIS SYSTEM")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "更改模型配置" }));
    expect(screen.getByRole("heading", { name: "模型供应商" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "连接配置" })).toBeVisible();
  });

  it("opens and closes the project console for narrow screens", async () => {
    render(<App api={fakeApiWithCompletedTask()} />);

    const toggle = await screen.findByRole("button", { name: "打开项目导航" });
    expect(await screen.findByRole("textbox", { name: "给分析 Agent 留言" })).toBeVisible();
    expect(toggle).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("textbox", { name: "给分析 Agent 留言" })).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "关闭项目导航" }));
    expect(toggle).toHaveAttribute("aria-expanded", "false");
  });

  it("keeps the question, execution trace, and composer in the center conversation", async () => {
    render(<App api={fakeApiWithCompletedTask()} />);

    const conversation = await screen.findByRole("main", { name: "分析对话" });
    expect(within(conversation).getByRole("heading", { name: "华东利润诊断" })).toBeVisible();
    expect(within(conversation).getByRole("heading", { name: "Agent 执行轨迹" })).toBeVisible();
    expect(within(conversation).getByText("检查数据")).toBeVisible();
    expect(within(conversation).getByRole("textbox", { name: "给分析 Agent 留言" })).toBeVisible();
  });

  it("shows only the objective input before a task exists", async () => {
    const api = fakeApiWithCompletedTask();
    api.listTasks = async () => [];
    api.listArtifacts = async () => [];
    render(<App api={api} />);

    expect(await screen.findByRole("textbox", { name: "分析目标" })).toBeVisible();
    expect(
      screen.queryByRole("textbox", { name: "给分析 Agent 留言" }),
    ).not.toBeInTheDocument();
  });

  it("shows approval cards and records a decision", async () => {
    const decideApproval = vi.fn(async () => ({
      id: "approval-1",
      status: "approved",
      decided_at: "2026-08-05T00:06:00Z",
    }));
    render(<App api={fakeApiWithCompletedTask(true, decideApproval)} />);

    expect(await screen.findByText("覆盖已有产物需要确认")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "批准操作" }));

    await waitFor(() => {
      expect(decideApproval).toHaveBeenCalledWith("approval-1", true);
    });
  });

  it("configures a third-party model from the model route", async () => {
    const api = fakeApiWithCompletedTask();
    api.getModelConfiguration = async () => UNCONFIGURED_MODEL;
    const saveModelConfiguration = vi.fn(async (input) => ({
      ...UNCONFIGURED_MODEL,
      ...input,
      configured: true,
      has_api_key: true,
      updated_at: "2026-08-05T00:10:00Z",
    }));
    api.saveModelConfiguration = saveModelConfiguration;
    render(<App api={api} />);

    fireEvent.click(await screen.findByRole("button", { name: "配置模型" }));
    fireEvent.click(screen.getByRole("radio", { name: "第三方中转站" }));
    fireEvent.change(screen.getByLabelText("Base URL"), {
      target: { value: "https://relay.example/v1" },
    });
    fireEvent.change(screen.getByLabelText("模型名称"), {
      target: { value: "relay-model" },
    });
    fireEvent.change(screen.getByLabelText("API Key"), {
      target: { value: "relay-secret" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存模型配置" }));

    await waitFor(() => {
      expect(saveModelConfiguration).toHaveBeenCalledWith({
        provider: "custom",
        base_url: "https://relay.example/v1",
        model: "relay-model",
        api_key: "relay-secret",
      });
    });
    expect(await screen.findByText("relay-model")).toBeVisible();
  });

  it("locks official endpoints and offers current provider models", async () => {
    const api = fakeApiWithCompletedTask();
    api.getModelConfiguration = async () => UNCONFIGURED_MODEL;
    render(<App api={api} />);

    fireEvent.click(await screen.findByRole("button", { name: "配置模型" }));
    fireEvent.click(screen.getByRole("radio", { name: "DeepSeek" }));

    expect(screen.getByDisplayValue("https://api.deepseek.com")).toBeDisabled();
    expect(screen.getByRole("option", { name: "deepseek-v4-flash" })).toBeVisible();
    expect(screen.getByRole("option", { name: "deepseek-v4-pro" })).toBeVisible();

    fireEvent.click(screen.getByRole("radio", { name: "OpenAI" }));
    expect(screen.getByDisplayValue("https://api.openai.com/v1")).toBeDisabled();
    expect(screen.getByRole("option", { name: "gpt-5.6-terra" })).toBeVisible();
    expect(screen.getByRole("option", { name: "gpt-5.6-sol" })).toBeVisible();
    expect(screen.getByRole("option", { name: "gpt-5.6-luna" })).toBeVisible();
  });

  it("requires a new key only after switching providers", async () => {
    render(<App api={fakeApiWithCompletedTask()} />);

    fireEvent.click(await screen.findByRole("button", { name: "更改模型配置" }));
    expect(screen.getByLabelText("API Key")).not.toBeRequired();

    fireEvent.click(screen.getByRole("radio", { name: "DeepSeek" }));
    expect(screen.getByLabelText("API Key")).toBeRequired();
  });

  it("shows projects as a directly switchable list", async () => {
    const api = fakeApiWithCompletedTask();
    api.listProjects = async () => [
      {
        id: "project-1",
        name: "2026 经营复盘",
        status: "active",
        created_at: "2026-08-05T00:00:00Z",
        updated_at: "2026-08-05T00:00:00Z",
      },
      {
        id: "project-2",
        name: "渠道增长诊断",
        status: "active",
        created_at: "2026-08-05T00:00:00Z",
        updated_at: "2026-08-05T00:00:00Z",
      },
    ];
    render(<App api={api} />);

    const currentProject = await screen.findByRole("button", { name: "2026 经营复盘" });
    const nextProject = screen.getByRole("button", { name: "渠道增长诊断" });
    expect(currentProject).toHaveAttribute("aria-current", "page");

    fireEvent.click(nextProject);

    await waitFor(() => {
      expect(nextProject).toHaveAttribute("aria-current", "page");
    });
  });

});
