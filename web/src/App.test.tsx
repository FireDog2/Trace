import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { App } from "./App";
import type { ApiClient } from "./api";
import type { SaveBusinessKnowledge } from "./types";

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
        name: "2026 Business Review",
        status: "active",
        created_at: "2026-08-05T00:00:00Z",
        updated_at: "2026-08-05T00:00:00Z",
      },
    ],
    createProject: async () => {
      throw new Error("Not used in this test");
    },
    updateProject: async () => {
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
    listDataSources: async () => [],
    createDataSource: async () => {
      throw new Error("Not used in this test");
    },
    updateDataSource: async () => {
      throw new Error("Not used in this test");
    },
    deleteDataSource: async () => {
      throw new Error("Not used in this test");
    },
    getDataSourceSchema: async () => ({ tables: {} }),
    getDataSourcePolicies: async () => ({
      data_source_id: "source-1",
      version: 1,
      rules: [],
    }),
    replaceDataSourcePolicies: async () => ({
      data_source_id: "source-1",
      version: 1,
      rules: [],
    }),
    listBusinessKnowledge: async () => [],
    importBusinessKnowledge: async () => {
      throw new Error("Not used in this test");
    },
    createBusinessKnowledge: async () => {
      throw new Error("Not used in this test");
    },
    updateBusinessKnowledge: async () => {
      throw new Error("Not used in this test");
    },
    deleteBusinessKnowledge: async () => {
      throw new Error("Not used in this test");
    },
    listAgents: async () => [],
    listRuns: async () => [],
    getTrace: async () => [],
    acceptRun: async () => {
      throw new Error("Not used in this test");
    },
    getSkillCandidateForRun: async () => {
      throw new Error("Not used in this test");
    },
    createSkillCandidate: async () => {
      throw new Error("Not used in this test");
    },
    decideSkillCandidate: async () => {
      throw new Error("Not used in this test");
    },
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

    const projectContext = await screen.findByRole("navigation", {
      name: "任务台",
    });
    const conversation = await screen.findByRole("main", { name: "分析对话" });
    const artifacts = await screen.findByRole("complementary", {
      name: "证据与运行",
    });
    const shell = projectContext.parentElement;

    expect(shell).not.toBeNull();
    expect(
      shell?.querySelectorAll(":scope > nav, :scope > main, :scope > aside"),
    ).toHaveLength(3);
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

    expect(
      await screen.findByText("TRACE", { selector: ".brand-block strong" }),
    ).toBeVisible();
    expect(screen.getByText("分析任务台")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "更改模型配置" }));
    expect(screen.getByRole("heading", { name: "模型供应商" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "连接配置" })).toBeVisible();
  });

  it("opens and closes the project console for narrow screens", async () => {
    render(<App api={fakeApiWithCompletedTask()} />);

    const toggle = await screen.findByRole("button", { name: "打开任务台" });
    expect(
      await screen.findByRole("textbox", { name: "给分析 Agent 留言" }),
    ).toBeVisible();
    expect(toggle).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(
      screen.getByRole("textbox", { name: "给分析 Agent 留言" }),
    ).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "关闭任务台" }));
    expect(toggle).toHaveAttribute("aria-expanded", "false");
  });

  it("collapses and restores the project workspace on desktop", async () => {
    render(<App api={fakeApiWithCompletedTask()} />);

    expect(
      await screen.findByRole("navigation", { name: "任务台" }),
    ).toBeVisible();
    const conversation = await screen.findByRole("main", { name: "分析对话" });
    fireEvent.click(screen.getByRole("button", { name: "收起任务台" }));

    expect(
      screen.queryByRole("navigation", { name: "任务台" }),
    ).not.toBeInTheDocument();
    expect(conversation.parentElement).toHaveClass("project-collapsed");

    fireEvent.click(screen.getByRole("button", { name: "打开任务台" }));
    expect(
      await screen.findByRole("navigation", { name: "任务台" }),
    ).toBeVisible();
    expect(conversation.parentElement).not.toHaveClass("project-collapsed");
  });

  it("keeps the question, execution trace, and composer in the center conversation", async () => {
    render(<App api={fakeApiWithCompletedTask()} />);

    const conversation = await screen.findByRole("main", { name: "分析对话" });
    expect(
      within(conversation).getByRole("heading", { name: "华东利润诊断" }),
    ).toBeVisible();
    expect(
      within(conversation).getByRole("heading", { name: "Agent 执行轨迹" }),
    ).toBeVisible();
    expect(within(conversation).getByText("检查数据")).toBeVisible();
    expect(
      within(conversation).getByRole("textbox", { name: "给分析 Agent 留言" }),
    ).toBeVisible();
  });

  it("offers a real retry action when a task has failed", async () => {
    const api = fakeApiWithCompletedTask();
    const getTask = api.getTask.bind(api);
    api.getTask = async (taskId) => ({
      ...(await getTask(taskId)),
      status: "failed",
    });
    const retry = vi.fn(async () => ({
      id: "task-1",
      project_id: "project-1",
      objective: "华东利润诊断",
      status: "failed" as const,
      created_at: "",
      updated_at: "",
    }));
    api.sendMessage = retry;
    render(<App api={api} />);

    expect(await screen.findByText("上次执行未完成")).toBeVisible();
    const input = screen.getByRole("textbox", { name: "修改问题并重试" });
    fireEvent.change(input, { target: { value: "重新列出项目产物" } });
    fireEvent.click(screen.getByRole("button", { name: "重新执行" }));

    await waitFor(() =>
      expect(retry).toHaveBeenCalledWith("task-1", "重新列出项目产物"),
    );
  });

  it("shows progress and prevents duplicate messages while a task is running", async () => {
    const api = fakeApiWithCompletedTask();
    const getTask = api.getTask.bind(api);
    api.getTask = async (taskId) => ({
      ...(await getTask(taskId)),
      status: "running",
    });
    const send = vi.fn(api.sendMessage);
    api.sendMessage = send;
    render(<App api={api} />);

    expect(await screen.findByText("Agent 正在处理这个问题")).toBeVisible();
    expect(
      screen.getByRole("textbox", { name: "当前问题正在执行" }),
    ).toBeDisabled();
    expect(screen.getByRole("button", { name: "执行中…" })).toBeDisabled();
    expect(send).not.toHaveBeenCalled();
  });

  it("expands a project into its conversations on double click", async () => {
    render(<App api={fakeApiWithCompletedTask()} />);

    const project = await screen.findByRole("button", {
      name: "2026 Business Review",
    });
    fireEvent.doubleClick(project);

    expect(await screen.findByRole("button", { name: "新对话" })).toBeVisible();
    expect(screen.getByRole("button", { name: "华东利润诊断" })).toBeVisible();
  });

  it("opens a visibly selected and focused new conversation draft", async () => {
    const api = fakeApiWithCompletedTask();
    const create = vi.fn(api.createTask);
    api.createTask = create;
    render(<App api={api} />);

    const project = await screen.findByRole("button", {
      name: "2026 Business Review",
    });
    fireEvent.doubleClick(project);
    const newConversation = await screen.findByRole("button", {
      name: "新对话",
    });
    fireEvent.click(newConversation);

    expect(
      await screen.findByRole("heading", { name: "开始新对话" }),
    ).toBeVisible();
    expect(
      screen.getByText("输入第一个问题后，这条对话会出现在左侧项目中。"),
    ).toBeVisible();
    expect(newConversation).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("textbox", { name: "分析目标" })).toHaveFocus();
    expect(create).not.toHaveBeenCalled();
  });

  it("renames a project inline from the task tree", async () => {
    const api = fakeApiWithCompletedTask();
    const rename = vi.fn(async (projectId: string, name: string) => ({
      id: projectId,
      name,
      status: "active",
      created_at: "",
      updated_at: "",
    }));
    api.updateProject = rename;
    render(<App api={api} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "重命名项目 2026 Business Review",
      }),
    );
    const input = screen.getByRole("textbox", {
      name: "修改项目名称 2026 Business Review",
    });
    fireEvent.change(input, { target: { value: "华东经营复盘" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() =>
      expect(rename).toHaveBeenCalledWith("project-1", "华东经营复盘"),
    );
    expect(
      await screen.findByRole("button", { name: "华东经营复盘" }),
    ).toBeVisible();
  });

  it("uploads a dataset from the conversation plus button", async () => {
    const api = fakeApiWithCompletedTask();
    const upload = vi.fn(async () => ({
      id: "source-2",
      project_id: "project-1",
      task_id: null,
      artifact_type: "source_dataset",
      title: "sales.csv",
      description: "上传数据集",
      content_type: "text/csv",
      created_at: "",
      content_url: "/api/artifacts/source-2/content",
    }));
    api.uploadDataset = upload;
    render(<App api={api} />);

    fireEvent.change(await screen.findByLabelText("上传分析文件"), {
      target: {
        files: [
          new File(["region,revenue"], "sales.csv", { type: "text/csv" }),
        ],
      },
    });

    await waitFor(() =>
      expect(upload).toHaveBeenCalledWith("project-1", expect.any(File)),
    );
    expect(await screen.findByText("已添加 sales.csv")).toBeVisible();
  });

  it("imports a knowledge document from the conversation plus button", async () => {
    const api = fakeApiWithCompletedTask();
    const importDocument = vi.fn(async () => ({
      file_name: "口径.md",
      created: [],
      skipped_names: [],
      category_counts: { metric: 0, term: 0, rule: 0 },
    }));
    api.importBusinessKnowledge = importDocument;
    render(<App api={api} />);

    fireEvent.change(await screen.findByLabelText("上传分析文件"), {
      target: {
        files: [new File(["# 营业利润"], "口径.md", { type: "text/markdown" })],
      },
    });

    await waitFor(() =>
      expect(importDocument).toHaveBeenCalledWith(expect.any(File)),
    );
    expect(await screen.findByText("已添加 口径.md")).toBeVisible();
  });

  it("shows model and shared configuration above the project tree", async () => {
    render(<App api={fakeApiWithCompletedTask()} />);

    const projectContext = await screen.findByRole("navigation", {
      name: "任务台",
    });
    expect(within(projectContext).getByText("模型配置")).toBeVisible();
    expect(within(projectContext).getByText("所有项目通用")).toBeVisible();
    expect(
      within(projectContext).getByRole("button", { name: "配置全局数据源" }),
    ).toBeVisible();
    expect(
      within(projectContext).getByRole("button", { name: "配置全局业务口径" }),
    ).toBeVisible();
  });

  it("opens PostgreSQL governance from the project context", async () => {
    render(<App api={fakeApiWithCompletedTask()} />);

    fireEvent.click(
      await screen.findByRole("button", { name: "配置全局数据源" }),
    );

    expect(screen.getByRole("dialog", { name: "数据库治理" })).toBeVisible();
    expect(screen.getByLabelText("PostgreSQL DSN")).toHaveAttribute(
      "type",
      "password",
    );
  });

  it("creates a versioned business metric from the knowledge drawer", async () => {
    const api = fakeApiWithCompletedTask();
    const create = vi.fn(async (input: SaveBusinessKnowledge) => ({
      id: "knowledge-1",
      project_id: "project-1",
      ...input,
      formula: input.formula ?? null,
      source: input.source ?? null,
      version: 1,
      created_at: "",
      updated_at: "",
    }));
    api.createBusinessKnowledge = create;
    render(<App api={api} />);

    fireEvent.click(
      await screen.findByRole("button", { name: "配置全局业务口径" }),
    );
    expect(screen.getByRole("dialog", { name: "业务知识库" })).toBeVisible();
    fireEvent.click(screen.getByRole("tab", { name: "逐条添加" }));
    fireEvent.change(screen.getByLabelText("知识名称"), {
      target: { value: "经营利润" },
    });
    fireEvent.change(screen.getByLabelText("知识定义"), {
      target: { value: "收入减去全部经营成本。" },
    });
    fireEvent.change(screen.getByLabelText("知识公式"), {
      target: { value: "revenue - operating_cost" },
    });
    fireEvent.click(screen.getByRole("button", { name: "添加到知识库" }));

    await waitFor(() => expect(create).toHaveBeenCalled());
    expect(
      await screen.findByRole("heading", { name: "经营利润" }),
    ).toBeVisible();
    expect(screen.getByText("revenue - operating_cost")).toBeVisible();
  });

  it("imports a knowledge document and shows its automatic categories", async () => {
    const api = fakeApiWithCompletedTask();
    const imported = {
      id: "knowledge-import-1",
      project_id: "project-1",
      kind: "metric" as const,
      name: "经营利润",
      definition: "收入减去全部经营成本。",
      formula: null,
      dimensions: [],
      synonyms: [],
      source: "文档导入 · 财务口径.md",
      version: 1,
      created_at: "",
      updated_at: "",
    };
    api.importBusinessKnowledge = vi.fn(async () => ({
      file_name: "财务口径.md",
      created: [imported],
      skipped_names: [],
      category_counts: { metric: 1, term: 0, rule: 0 },
    }));
    render(<App api={api} />);

    fireEvent.click(
      await screen.findByRole("button", { name: "配置全局业务口径" }),
    );
    fireEvent.change(screen.getByLabelText("选择业务口径文档"), {
      target: {
        files: [
          new File(["# 经营利润"], "财务口径.md", { type: "text/markdown" }),
        ],
      },
    });

    expect(
      await screen.findByText("财务口径.md 已导入：1 个指标。"),
    ).toBeVisible();
    expect(screen.getByRole("heading", { name: "经营利润" })).toBeVisible();
  });

  it("loads an existing policy in read-only mode and saves an edited version", async () => {
    const api = fakeApiWithCompletedTask();
    api.listDataSources = async () => [
      {
        id: "database-1",
        project_id: "project-1",
        name: "经营库",
        dialect: "postgres",
        policy_version: 3,
        created_at: "",
        updated_at: "",
      },
    ];
    api.getDataSourceSchema = async () => ({
      tables: { orders: ["id", "amount"] },
    });
    api.getDataSourcePolicies = async () => ({
      data_source_id: "database-1",
      version: 3,
      rules: [{ table: "orders", column: null, access: "approval_required" }],
    });
    const replace = vi.fn(async () => ({
      data_source_id: "database-1",
      version: 4,
      rules: [],
    }));
    api.replaceDataSourcePolicies = replace;
    render(<App api={api} />);

    fireEvent.click(
      await screen.findByRole("button", { name: "配置全局数据源" }),
    );
    fireEvent.click(await screen.findByRole("button", { name: /经营库/ }));
    const tablePolicy = await screen.findByLabelText("orders.* 权限");
    expect(tablePolicy).toBeDisabled();
    expect(screen.getByLabelText("orders.amount 权限")).toHaveValue(
      "approval_required",
    );
    fireEvent.click(screen.getByRole("button", { name: "修改访问策略" }));
    fireEvent.change(tablePolicy, { target: { value: "allowed" } });
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));

    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith("database-1", expect.any(Array), 3),
    );
    expect(await screen.findByText("访问策略修改已保存为 v4。")).toBeVisible();
  });

  it("shows a masked saved DSN and updates an existing connection", async () => {
    const api = fakeApiWithCompletedTask();
    const existing = {
      id: "database-1",
      project_id: "project-1",
      name: "经营库",
      dialect: "postgres" as const,
      policy_version: 3,
      created_at: "",
      updated_at: "",
    };
    api.listDataSources = async () => [existing];
    api.getDataSourceSchema = async () => ({ tables: { orders: ["id"] } });
    api.getDataSourcePolicies = async () => ({
      data_source_id: existing.id,
      version: 3,
      rules: [],
    });
    const update = vi.fn(
      async (_sourceId: string, input: { name: string; dsn?: string }) => ({
        ...existing,
        name: input.name,
      }),
    );
    api.updateDataSource = update;
    render(<App api={api} />);

    fireEvent.click(
      await screen.findByRole("button", { name: "配置全局数据源" }),
    );
    const nameInput = await screen.findByLabelText("数据源名称");
    const dsnInput = screen.getByLabelText("PostgreSQL DSN");
    await waitFor(() => expect(nameInput).toHaveValue("经营库"));
    expect(dsnInput).toHaveAttribute("placeholder", "••••••••••••（已配置）");
    expect(dsnInput).not.toBeRequired();
    expect(
      screen.getByText("DSN 已加密保存；留空保持原配置，输入新地址可替换。"),
    ).toBeVisible();

    fireEvent.change(nameInput, { target: { value: "经营分析库" } });
    fireEvent.click(screen.getByRole("button", { name: "保存连接修改" }));

    await waitFor(() =>
      expect(update).toHaveBeenCalledWith("database-1", { name: "经营分析库" }),
    );
    expect(
      await screen.findByText("连接配置已更新；DSN 已安全保存。"),
    ).toBeVisible();
  });

  it("deletes a data source only after an explicit confirmation", async () => {
    const api = fakeApiWithCompletedTask();
    const existing = {
      id: "database-1",
      project_id: "project-1",
      name: "经营库",
      dialect: "postgres" as const,
      policy_version: 3,
      created_at: "",
      updated_at: "",
    };
    api.listDataSources = async () => [existing];
    api.getDataSourceSchema = async () => ({ tables: { orders: ["id"] } });
    api.getDataSourcePolicies = async () => ({
      data_source_id: existing.id,
      version: 3,
      rules: [],
    });
    const remove = vi.fn(async () => undefined);
    api.deleteDataSource = remove;
    render(<App api={api} />);

    fireEvent.click(
      await screen.findByRole("button", { name: "配置全局数据源" }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "删除数据源" }));

    expect(remove).not.toHaveBeenCalled();
    expect(
      screen.getByRole("alertdialog", { name: "删除 经营库？" }),
    ).toBeVisible();
    expect(
      screen.getByText(
        "删除后，所有项目都无法再使用此连接；已产生的历史查询与审计记录会继续保留。",
      ),
    ).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));

    await waitFor(() => expect(remove).toHaveBeenCalledWith("database-1"));
    expect(
      await screen.findByText(
        "已删除数据源 经营库；历史查询与审计记录仍保留。",
      ),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: /经营库/ }),
    ).not.toBeInTheDocument();
  });

  it("keeps policy editing usable when the database schema is unavailable", async () => {
    const api = fakeApiWithCompletedTask();
    api.listDataSources = async () => [
      {
        id: "database-1",
        project_id: "project-1",
        name: "离线经营库",
        dialect: "postgres",
        policy_version: 2,
        created_at: "",
        updated_at: "",
      },
    ];
    api.getDataSourceSchema = async () => {
      throw new Error("connection timeout");
    };
    api.getDataSourcePolicies = async () => ({
      data_source_id: "database-1",
      version: 2,
      rules: [],
    });
    render(<App api={api} />);

    fireEvent.click(
      await screen.findByRole("button", { name: "配置全局数据源" }),
    );
    expect(await screen.findByText("暂时无法连接数据库")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "修改访问策略" }));
    fireEvent.change(screen.getByLabelText("手动添加表名"), {
      target: { value: "orders" },
    });
    fireEvent.change(screen.getByLabelText("手动添加字段名"), {
      target: { value: "customer_email" },
    });
    fireEvent.click(screen.getByRole("button", { name: "添加规则" }));

    expect(screen.getByLabelText("orders.* 权限")).toBeEnabled();
    expect(screen.getByLabelText("orders.customer_email 权限")).toBeEnabled();
  });

  it("shows durable agents and correlated trace in the evidence workspace", async () => {
    const api = fakeApiWithCompletedTask();
    api.listAgents = async () => [
      {
        id: "agent-sql",
        task_id: "task-1",
        role: "sql",
        objective: "执行受控 SQL",
        status: "completed",
        result_summary: "artifact-1",
        error_category: null,
        created_at: "",
        started_at: "",
        completed_at: "",
      },
    ];
    api.listRuns = async () => [
      {
        id: "run-1",
        task_id: "task-1",
        status: "completed",
        accepted: false,
        started_at: "",
        completed_at: "",
        error_category: null,
      },
    ];
    api.getTrace = async () => [
      {
        id: "trace-1",
        run_id: "run-1",
        task_id: "task-1",
        agent_id: "agent-sql",
        parent_event_id: null,
        sequence: 1,
        event_type: "sql.validated",
        status: "completed",
        payload: { sql_hash: "safe-hash" },
        duration_ms: 2,
        attempt: 1,
        error_category: null,
        artifact_ids: [],
      },
    ];
    render(<App api={api} />);

    const workspace = await screen.findByRole("complementary", {
      name: "证据与运行",
    });
    fireEvent.click(within(workspace).getByRole("tab", { name: "运行" }));
    fireEvent.click(within(workspace).getByRole("tab", { name: /Agent/ }));
    expect(await within(workspace).findByText("执行受控 SQL")).toBeVisible();

    fireEvent.click(within(workspace).getByRole("tab", { name: /Trace/ }));
    fireEvent.change(
      within(workspace).getByRole("combobox", { name: "选择运行" }),
      {
        target: { value: "run-1" },
      },
    );
    expect(await within(workspace).findByText("SQL 校验")).toBeVisible();
    expect(
      within(workspace).getByRole("button", { name: "接受运行并沉淀 Skill" }),
    ).toBeVisible();
    expect(
      within(workspace).getByRole("link", { name: "完整回放 JSON" }),
    ).toHaveAttribute("href", "/api/runs/run-1/replay");
  });

  it("sorts final artifacts into accessible tabs and keeps the real link", async () => {
    render(<App api={fakeApiWithCompletedTask()} />);

    const artifacts = await screen.findByRole("complementary", {
      name: "证据与运行",
    });
    expect(
      within(artifacts).getByText("物流成本增长是主要变化。"),
    ).toBeVisible();

    fireEvent.click(within(artifacts).getByRole("tab", { name: /图表/ }));
    const artifactLink = within(artifacts).getByRole("link", {
      name: "打开 物流成本趋势",
    });
    expect(artifactLink).toHaveAttribute(
      "href",
      "/api/artifacts/chart-1/content",
    );

    fireEvent.click(within(artifacts).getByRole("tab", { name: /数据/ }));
    expect(within(artifacts).getByText("暂无数据产物")).toBeVisible();
  });

  it("renders the analysis summary as Markdown and exports reports as PDF or Markdown", async () => {
    const api = fakeApiWithCompletedTask();
    const getTask = api.getTask.bind(api);
    api.getTask = async (taskId) => {
      const detail = await getTask(taskId);
      return {
        ...detail,
        messages: [
          {
            id: "summary-markdown",
            role: "assistant",
            content: "## 关键发现\n\n**利润下滑** 12%。\n\n- 物流成本上升",
            created_at: "2026-08-05T00:05:00Z",
          },
        ],
        artifacts: [
          ...detail.artifacts,
          {
            id: "report-1",
            project_id: "project-1",
            task_id: "task-1",
            artifact_type: "markdown_report",
            title: "华东利润诊断报告",
            description: "证据关联分析报告",
            content_type: "text/markdown",
            created_at: "2026-08-05T00:05:00Z",
            content_url: "/api/artifacts/report-1/content",
          },
        ],
      };
    };
    render(<App api={api} />);

    const workspace = await screen.findByRole("complementary", {
      name: "证据与运行",
    });
    expect(
      await within(workspace).findByRole("heading", { name: "关键发现" }),
    ).toBeVisible();
    expect(within(workspace).getByText("利润下滑").tagName).toBe("STRONG");
    expect(
      within(workspace).queryByText("**利润下滑**"),
    ).not.toBeInTheDocument();

    const format = within(workspace).getByRole("combobox", {
      name: "华东利润诊断报告 导出格式",
    });
    const exportLink = within(workspace).getByRole("link", { name: /导出/ });
    expect(format).toHaveValue("pdf");
    expect(exportLink).toHaveAttribute(
      "href",
      "/api/artifacts/report-1/export?format=pdf",
    );
    fireEvent.change(format, { target: { value: "markdown" } });
    expect(exportLink).toHaveAttribute(
      "href",
      "/api/artifacts/report-1/export?format=markdown",
    );
  });

  it("renders assistant Markdown inside the center conversation", async () => {
    const api = fakeApiWithCompletedTask();
    const getTask = api.getTask.bind(api);
    api.getTask = async (taskId) => ({
      ...(await getTask(taskId)),
      messages: [
        {
          id: "conversation-markdown",
          role: "assistant",
          content:
            "## 我的定位\n\n- **身份**：Lead Agent\n- **工作方式**：证据优先\n\n| 项目 | 状态 |\n| --- | --- |\n| 趋势报告 | 已完成 |",
          created_at: "2026-08-05T00:05:00Z",
        },
      ],
    });
    render(<App api={api} />);

    const conversation = await screen.findByRole("main", { name: "分析对话" });
    const markdown = conversation.querySelector<HTMLElement>(
      ".conversation-markdown",
    );
    expect(markdown).not.toBeNull();
    expect(
      within(conversation).getByRole("heading", { name: "我的定位" }),
    ).toBeVisible();
    expect(within(conversation).getByText("身份").tagName).toBe("STRONG");
    expect(within(markdown!).getByRole("list")).toBeVisible();
    expect(within(markdown!).getByRole("table")).toBeVisible();
    expect(
      within(conversation).queryByText("## 我的定位"),
    ).not.toBeInTheDocument();
  });

  it("hides the evidence and runtime workspace and restores it on demand", async () => {
    render(<App api={fakeApiWithCompletedTask()} />);

    const workspace = await screen.findByRole("complementary", {
      name: "证据与运行",
    });
    expect(
      within(workspace).getByRole("tab", { name: "证据" }),
    ).toHaveAttribute("aria-selected", "true");
    expect(
      within(workspace).queryByRole("tab", { name: /Agent/ }),
    ).not.toBeInTheDocument();
    fireEvent.click(within(workspace).getByRole("tab", { name: "运行" }));
    expect(within(workspace).getByRole("tab", { name: /Agent/ })).toBeVisible();

    fireEvent.click(
      within(workspace).getByRole("button", { name: "隐藏证据与运行" }),
    );
    expect(
      screen.queryByRole("complementary", { name: "证据与运行" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("main", { name: "分析对话" }).parentElement,
    ).toHaveClass("artifact-collapsed");
    const toggle = screen.getByRole("button", { name: "打开证据与运行" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(
      await screen.findByRole("complementary", { name: "证据与运行" }),
    ).toBeVisible();
    expect(
      screen.getByRole("main", { name: "分析对话" }).parentElement,
    ).not.toHaveClass("artifact-collapsed");
  });

  it("shows only the objective input before a task exists", async () => {
    const api = fakeApiWithCompletedTask();
    api.listTasks = async () => [];
    api.listArtifacts = async () => [];
    render(<App api={api} />);

    expect(
      await screen.findByRole("textbox", { name: "分析目标" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("textbox", { name: "给分析 Agent 留言" }),
    ).not.toBeInTheDocument();
    expect(screen.getByLabelText("在提出问题前上传分析文件")).toBeVisible();
  });

  it("uploads CSV before the first question is submitted", async () => {
    const api = fakeApiWithCompletedTask();
    api.listTasks = async () => [];
    api.listArtifacts = async () => [];
    const upload = vi.fn(async () => ({
      id: "source-first",
      project_id: "project-1",
      task_id: null,
      artifact_type: "source_dataset",
      title: "first.csv",
      description: "上传数据集",
      content_type: "text/csv",
      created_at: "",
      content_url: "/api/artifacts/source-first/content",
    }));
    api.uploadDataset = upload;
    render(<App api={api} />);

    fireEvent.change(await screen.findByLabelText("在提出问题前上传分析文件"), {
      target: {
        files: [
          new File(["region,revenue"], "first.csv", { type: "text/csv" }),
        ],
      },
    });

    await waitFor(() =>
      expect(upload).toHaveBeenCalledWith("project-1", expect.any(File)),
    );
    expect(await screen.findByText("已添加 first.csv")).toBeVisible();
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
    expect(
      screen.getByRole("option", { name: "deepseek-v4-flash" }),
    ).toBeVisible();
    expect(
      screen.getByRole("option", { name: "deepseek-v4-pro" }),
    ).toBeVisible();

    fireEvent.click(screen.getByRole("radio", { name: "OpenAI" }));
    expect(
      screen.getByDisplayValue("https://api.openai.com/v1"),
    ).toBeDisabled();
    expect(screen.getByRole("option", { name: "gpt-5.6-terra" })).toBeVisible();
    expect(screen.getByRole("option", { name: "gpt-5.6-sol" })).toBeVisible();
    expect(screen.getByRole("option", { name: "gpt-5.6-luna" })).toBeVisible();
  });

  it("requires a new key only after switching providers", async () => {
    render(<App api={fakeApiWithCompletedTask()} />);

    fireEvent.click(
      await screen.findByRole("button", { name: "更改模型配置" }),
    );
    expect(screen.getByLabelText("API Key")).not.toBeRequired();

    fireEvent.click(screen.getByRole("radio", { name: "DeepSeek" }));
    expect(screen.getByLabelText("API Key")).toBeRequired();
  });

  it("shows projects as a directly switchable list", async () => {
    const api = fakeApiWithCompletedTask();
    api.listProjects = async () => [
      {
        id: "project-1",
        name: "2026 Business Review",
        status: "active",
        created_at: "2026-08-05T00:00:00Z",
        updated_at: "2026-08-05T00:00:00Z",
      },
      {
        id: "project-2",
        name: "Channel Growth Diagnosis",
        status: "active",
        created_at: "2026-08-05T00:00:00Z",
        updated_at: "2026-08-05T00:00:00Z",
      },
    ];
    render(<App api={api} />);

    const currentProject = await screen.findByRole("button", {
      name: "2026 Business Review",
    });
    const nextProject = screen.getByRole("button", {
      name: "Channel Growth Diagnosis",
    });
    expect(currentProject).toHaveAttribute("aria-current", "page");

    fireEvent.click(nextProject);

    await waitFor(() => {
      expect(nextProject).toHaveAttribute("aria-current", "page");
    });
  });
});
