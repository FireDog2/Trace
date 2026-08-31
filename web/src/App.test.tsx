import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import type { ApiClient } from "./api";
import type { SaveBusinessKnowledge } from "./types";

afterEach(() => vi.restoreAllMocks());

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
    deleteProject: async () => undefined,
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
          content_type: "text/html; charset=utf-8",
          created_at: "2026-08-05T00:04:00Z",
          content_url: "/api/artifacts/chart-1/content",
        },
      ],
    }),
    setTaskPinned: async (taskId, pinned) => ({
      id: taskId,
      project_id: "project-1",
      objective: "华东利润诊断",
      status: "completed",
      created_at: "2026-08-05T00:00:00Z",
      updated_at: "2026-08-05T00:06:00Z",
      pinned,
    }),
    deleteTask: async () => undefined,
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
      name: "Workbench",
    });
    const conversation = await screen.findByRole("main", {
      name: "Analysis conversation",
    });
    const artifacts = await screen.findByRole("complementary", {
      name: "Evidence & runs",
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
    expect(screen.getByText("Analysis Workbench")).toBeVisible();

    fireEvent.click(
      screen.getByRole("button", { name: "Change model configuration" }),
    );
    expect(
      screen.getByRole("heading", { name: "Model provider" }),
    ).toBeVisible();
    expect(
      screen.getByRole("heading", { name: "Connection settings" }),
    ).toBeVisible();
  });

  it("opens and closes the project console for narrow screens", async () => {
    render(<App api={fakeApiWithCompletedTask()} />);

    const toggle = await screen.findByRole("button", {
      name: "Open workbench",
    });
    expect(
      await screen.findByRole("textbox", {
        name: "Message the analysis agent",
      }),
    ).toBeVisible();
    expect(toggle).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(
      screen.getByRole("textbox", { name: "Message the analysis agent" }),
    ).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "Close workbench" }));
    expect(toggle).toHaveAttribute("aria-expanded", "false");
  });

  it("collapses and restores the project workspace on desktop", async () => {
    render(<App api={fakeApiWithCompletedTask()} />);

    expect(
      await screen.findByRole("navigation", { name: "Workbench" }),
    ).toBeVisible();
    const conversation = await screen.findByRole("main", {
      name: "Analysis conversation",
    });
    fireEvent.click(screen.getByRole("button", { name: "Collapse workbench" }));

    expect(
      screen.queryByRole("navigation", { name: "Workbench" }),
    ).not.toBeInTheDocument();
    expect(conversation.parentElement).toHaveClass("project-collapsed");

    fireEvent.click(screen.getByRole("button", { name: "Open workbench" }));
    expect(
      await screen.findByRole("navigation", { name: "Workbench" }),
    ).toBeVisible();
    expect(conversation.parentElement).not.toHaveClass("project-collapsed");
  });

  it("keeps the question, execution trace, and composer in the center conversation", async () => {
    render(<App api={fakeApiWithCompletedTask()} />);

    const conversation = await screen.findByRole("main", {
      name: "Analysis conversation",
    });
    expect(
      within(conversation).getByRole("heading", { name: "华东利润诊断" }),
    ).toBeVisible();
    expect(
      within(conversation).getByRole("heading", {
        name: "Agent execution trace",
      }),
    ).toBeVisible();
    expect(within(conversation).getByText("检查数据")).toBeVisible();
    expect(
      within(conversation).getByRole("textbox", {
        name: "Message the analysis agent",
      }),
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

    expect(
      await screen.findByText("The previous run did not finish"),
    ).toBeVisible();
    const input = screen.getByRole("textbox", {
      name: "Revise the question and retry",
    });
    fireEvent.change(input, { target: { value: "重新列出项目产物" } });
    fireEvent.click(screen.getByRole("button", { name: "Run again" }));

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

    expect(
      await screen.findByText("The agent is working on this question"),
    ).toBeVisible();
    expect(
      screen.getByRole("textbox", { name: "This question is running" }),
    ).toBeDisabled();
    expect(screen.getByRole("button", { name: "Running…" })).toBeDisabled();
    expect(send).not.toHaveBeenCalled();
  });

  it("expands a project into its conversations on double click", async () => {
    render(<App api={fakeApiWithCompletedTask()} />);

    const project = await screen.findByRole("button", {
      name: "2026 Business Review",
    });
    fireEvent.doubleClick(project);

    expect(
      await screen.findByRole("button", { name: "New conversation" }),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "华东利润诊断" })).toBeVisible();
  });

  it("pins a conversation from its overflow menu", async () => {
    const api = fakeApiWithCompletedTask();
    const pin = vi.fn(api.setTaskPinned);
    api.setTaskPinned = pin;
    render(<App api={api} />);

    fireEvent.doubleClick(
      await screen.findByRole("button", { name: "2026 Business Review" }),
    );
    fireEvent.click(
      await screen.findByRole("button", {
        name: "Conversation actions for 华东利润诊断",
      }),
    );
    fireEvent.click(screen.getByRole("menuitem", { name: "Pin" }));

    await waitFor(() => expect(pin).toHaveBeenCalledWith("task-1", true));
    expect(await screen.findByLabelText("Pinned")).toBeVisible();
  });

  it("requires confirmation before deleting a conversation", async () => {
    const api = fakeApiWithCompletedTask();
    const remove = vi.fn(api.deleteTask);
    api.deleteTask = remove;
    render(<App api={api} />);

    fireEvent.doubleClick(
      await screen.findByRole("button", { name: "2026 Business Review" }),
    );
    fireEvent.click(
      await screen.findByRole("button", {
        name: "Conversation actions for 华东利润诊断",
      }),
    );
    fireEvent.click(screen.getByRole("menuitem", { name: "Delete" }));

    expect(
      screen.getByRole("alertdialog", { name: "Delete this conversation?" }),
    ).toBeVisible();
    expect(remove).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Confirm deletion" }));

    await waitFor(() => expect(remove).toHaveBeenCalledWith("task-1"));
    expect(
      screen.queryByRole("button", {
        name: "Conversation actions for 华东利润诊断",
      }),
    ).not.toBeInTheDocument();
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
      name: "New conversation",
    });
    fireEvent.click(newConversation);

    expect(
      await screen.findByRole("heading", { name: "Start a new conversation" }),
    ).toBeVisible();
    expect(
      screen.getByText(
        "Once you enter your first question, this conversation will appear under the project on the left.",
      ),
    ).toBeVisible();
    expect(newConversation).toHaveAttribute("aria-current", "page");
    expect(
      screen.getByRole("textbox", { name: "Analysis objective" }),
    ).toHaveFocus();
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
        name: "Project actions for 2026 Business Review",
      }),
    );
    fireEvent.click(screen.getByRole("menuitem", { name: "Rename" }));
    const input = screen.getByRole("textbox", {
      name: "Rename project 2026 Business Review",
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

  it("requires confirmation before permanently deleting a project", async () => {
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
        name: "Supply Review",
        status: "active",
        created_at: "2026-08-06T00:00:00Z",
        updated_at: "2026-08-06T00:00:00Z",
      },
    ];
    const remove = vi.fn(async () => undefined);
    api.deleteProject = remove;
    render(<App api={api} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Project actions for 2026 Business Review",
      }),
    );
    fireEvent.click(screen.getByRole("menuitem", { name: "Delete project" }));

    expect(
      screen.getByRole("alertdialog", {
        name: "Permanently delete this project?",
      }),
    ).toBeVisible();
    expect(remove).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Delete permanently" }));

    await waitFor(() => expect(remove).toHaveBeenCalledWith("project-1"));
    expect(
      screen.queryByRole("button", { name: "2026 Business Review" }),
    ).not.toBeInTheDocument();
    expect(
      await screen.findByRole("button", { name: "Supply Review" }),
    ).toHaveAttribute("aria-current", "page");
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

    fireEvent.change(await screen.findByLabelText("Upload analysis file"), {
      target: {
        files: [
          new File(["region,revenue"], "sales.csv", { type: "text/csv" }),
        ],
      },
    });

    await waitFor(() =>
      expect(upload).toHaveBeenCalledWith("project-1", expect.any(File)),
    );
    expect(await screen.findByText("Added sales.csv")).toBeVisible();
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

    fireEvent.change(await screen.findByLabelText("Upload analysis file"), {
      target: {
        files: [new File(["# 营业利润"], "口径.md", { type: "text/markdown" })],
      },
    });

    await waitFor(() =>
      expect(importDocument).toHaveBeenCalledWith(expect.any(File)),
    );
    expect(await screen.findByText("Added 口径.md")).toBeVisible();
  });

  it("shows model and shared configuration above the project tree", async () => {
    render(<App api={fakeApiWithCompletedTask()} />);

    const projectContext = await screen.findByRole("navigation", {
      name: "Workbench",
    });
    expect(
      within(projectContext).getByText("Model configuration"),
    ).toBeVisible();
    expect(
      within(projectContext).getByText("Shared by all projects"),
    ).toBeVisible();
    expect(
      within(projectContext).getByRole("button", {
        name: "Configure global data sources",
      }),
    ).toBeVisible();
    expect(
      within(projectContext).getByRole("button", {
        name: "Configure global business definitions",
      }),
    ).toBeVisible();
  });

  it("opens PostgreSQL governance from the project context", async () => {
    render(<App api={fakeApiWithCompletedTask()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Configure global data sources",
      }),
    );

    expect(
      screen.getByRole("dialog", { name: "Database governance" }),
    ).toBeVisible();
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
      await screen.findByRole("button", {
        name: "Configure global business definitions",
      }),
    );
    expect(
      screen.getByRole("dialog", { name: "Business knowledge base" }),
    ).toBeVisible();
    fireEvent.click(screen.getByRole("tab", { name: "Add manually" }));
    fireEvent.change(screen.getByLabelText("Knowledge name"), {
      target: { value: "经营利润" },
    });
    fireEvent.change(screen.getByLabelText("Knowledge definition"), {
      target: { value: "收入减去全部经营成本。" },
    });
    fireEvent.change(screen.getByLabelText("Knowledge formula"), {
      target: { value: "revenue - operating_cost" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Add to knowledge base" }),
    );

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
      await screen.findByRole("button", {
        name: "Configure global business definitions",
      }),
    );
    fireEvent.change(
      screen.getByLabelText("Choose a business definitions document"),
      {
        target: {
          files: [
            new File(["# 经营利润"], "财务口径.md", { type: "text/markdown" }),
          ],
        },
      },
    );

    expect(
      await screen.findByText("财务口径.md imported: 1 metric."),
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
      await screen.findByRole("button", {
        name: "Configure global data sources",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: /经营库/ }));
    const tablePolicy = await screen.findByLabelText("orders.* access");
    expect(tablePolicy).toBeDisabled();
    expect(screen.getByLabelText("orders.amount access")).toHaveValue(
      "approval_required",
    );
    fireEvent.click(screen.getByRole("button", { name: "Edit access policy" }));
    fireEvent.change(tablePolicy, { target: { value: "allowed" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith("database-1", expect.any(Array), 3),
    );
    expect(
      await screen.findByText("Access policy changes saved as v4."),
    ).toBeVisible();
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
      await screen.findByRole("button", {
        name: "Configure global data sources",
      }),
    );
    const nameInput = await screen.findByLabelText("Data source name");
    const dsnInput = screen.getByLabelText("PostgreSQL DSN");
    await waitFor(() => expect(nameInput).toHaveValue("经营库"));
    expect(dsnInput).toHaveAttribute(
      "placeholder",
      "•••••••••••• (configured)",
    );
    expect(dsnInput).not.toBeRequired();
    expect(
      screen.getByText(
        "DSN is stored in encrypted form. Leave blank to keep the current value, or enter a new address to replace it.",
      ),
    ).toBeVisible();

    fireEvent.change(nameInput, { target: { value: "经营分析库" } });
    fireEvent.click(
      screen.getByRole("button", { name: "Save connection changes" }),
    );

    await waitFor(() =>
      expect(update).toHaveBeenCalledWith("database-1", { name: "经营分析库" }),
    );
    expect(
      await screen.findByText(
        "Connection settings updated; the DSN is stored securely.",
      ),
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
      await screen.findByRole("button", {
        name: "Configure global data sources",
      }),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Delete data source" }),
    );

    expect(remove).not.toHaveBeenCalled();
    expect(
      screen.getByRole("alertdialog", { name: "Delete 经营库?" }),
    ).toBeVisible();
    expect(
      screen.getByText(
        "After deletion, no project will be able to use this connection. Existing query history and audit records will be retained.",
      ),
    ).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Confirm deletion" }));

    await waitFor(() => expect(remove).toHaveBeenCalledWith("database-1"));
    expect(
      await screen.findByText(
        "Deleted data source 经营库; historical queries and audit records are retained.",
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
      await screen.findByRole("button", {
        name: "Configure global data sources",
      }),
    );
    expect(
      await screen.findByText("Unable to connect to the database right now"),
    ).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Edit access policy" }));
    fireEvent.change(screen.getByLabelText("Manually add table name"), {
      target: { value: "orders" },
    });
    fireEvent.change(screen.getByLabelText("Manually add field name"), {
      target: { value: "customer_email" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add rule" }));

    expect(screen.getByLabelText("orders.* access")).toBeEnabled();
    expect(screen.getByLabelText("orders.customer_email access")).toBeEnabled();
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
      name: "Evidence & runs",
    });
    fireEvent.click(within(workspace).getByRole("tab", { name: "Runs" }));
    fireEvent.click(within(workspace).getByRole("tab", { name: /Agent/ }));
    expect(await within(workspace).findByText("执行受控 SQL")).toBeVisible();

    fireEvent.click(within(workspace).getByRole("tab", { name: /Trace/ }));
    fireEvent.change(
      within(workspace).getByRole("combobox", { name: "Select a run" }),
      {
        target: { value: "run-1" },
      },
    );
    expect(await within(workspace).findByText("SQL validated")).toBeVisible();
    expect(
      within(workspace).getByRole("button", {
        name: "Accept run and create skill candidate",
      }),
    ).toBeVisible();
    expect(
      within(workspace).getByRole("link", { name: "Full replay JSON" }),
    ).toHaveAttribute("href", "/api/runs/run-1/replay");
  });

  it("sorts final artifacts into accessible tabs and keeps the real link", async () => {
    render(<App api={fakeApiWithCompletedTask()} />);

    const artifacts = await screen.findByRole("complementary", {
      name: "Evidence & runs",
    });
    expect(
      within(artifacts).getByText("物流成本增长是主要变化。"),
    ).toBeVisible();

    fireEvent.click(within(artifacts).getByRole("tab", { name: /Charts/ }));
    const artifactLink = within(artifacts).getByRole("link", {
      name: "Open 物流成本趋势",
    });
    expect(artifactLink).toHaveAttribute(
      "href",
      "/api/artifacts/chart-1/content",
    );
    expect(
      within(artifacts).getByTitle("物流成本趋势 preview"),
    ).toHaveAttribute("src", "/api/artifacts/chart-1/content?preview=true");

    fireEvent.click(within(artifacts).getByRole("tab", { name: /Data/ }));
    expect(within(artifacts).getByText("No data artifacts")).toBeVisible();
  });

  it("renders the analysis summary as Markdown and exports reports as PDF or Markdown", async () => {
    const reportFetch = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        new Response(
          "# East Region March Profit Review\n\n> Investigate logistics first.\n\n## Driver bridge\n\n| Driver | Effect |\n| --- | ---: |\n| Logistics | -27 |",
          { status: 200, headers: { "Content-Type": "text/markdown" } },
        ),
      );
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
      name: "Evidence & runs",
    });
    expect(
      await within(workspace).findByRole("heading", { name: "关键发现" }),
    ).toBeVisible();
    expect(within(workspace).getByText("利润下滑").tagName).toBe("STRONG");
    expect(
      within(workspace).queryByText("**利润下滑**"),
    ).not.toBeInTheDocument();
    const renderedReport = await within(workspace).findByRole("region", {
      name: "华东利润诊断报告 report content",
    });
    expect(
      within(renderedReport).getByRole("heading", {
        name: "East Region March Profit Review",
      }),
    ).toBeVisible();
    expect(
      within(renderedReport).getByText("Investigate logistics first."),
    ).toBeVisible();
    expect(within(renderedReport).getByRole("table")).toBeVisible();
    const summary = within(workspace).getByRole("heading", {
      name: "Analysis summary",
    });
    expect(
      renderedReport.compareDocumentPosition(summary) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(reportFetch).toHaveBeenCalledWith(
      "/api/artifacts/report-1/content",
      expect.objectContaining({
        headers: { Accept: "text/markdown, text/plain;q=0.9" },
      }),
    );

    const format = within(workspace).getByRole("combobox", {
      name: "华东利润诊断报告 export format",
    });
    const exportLink = within(workspace).getByRole("link", { name: /Export/ });
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

  it("keeps report actions available when inline preview fails", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("Unavailable", { status: 503 }),
    );
    const api = fakeApiWithCompletedTask();
    const getTask = api.getTask.bind(api);
    api.getTask = async (taskId) => ({
      ...(await getTask(taskId)),
      artifacts: [
        {
          id: "report-failed",
          project_id: "project-1",
          task_id: "task-1",
          artifact_type: "markdown_report",
          title: "Operational review",
          description: "Evidence-linked report",
          content_type: "text/markdown",
          created_at: "2026-08-05T00:05:00Z",
          content_url: "/api/artifacts/report-failed/content",
        },
      ],
    });

    render(<App api={api} />);

    const workspace = await screen.findByRole("complementary", {
      name: "Evidence & runs",
    });
    expect(
      await within(workspace).findByText(
        "Report preview is unavailable. Open or export the artifact below.",
      ),
    ).toBeVisible();
    expect(
      within(workspace).getByRole("link", { name: "Open Operational review" }),
    ).toHaveAttribute("href", "/api/artifacts/report-failed/content");
    expect(
      within(workspace).getByRole("link", { name: /Export/ }),
    ).toBeVisible();
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

    const conversation = await screen.findByRole("main", {
      name: "Analysis conversation",
    });
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
      name: "Evidence & runs",
    });
    expect(
      within(workspace).getByRole("tab", { name: "Evidence" }),
    ).toHaveAttribute("aria-selected", "true");
    expect(
      within(workspace).queryByRole("tab", { name: /Agent/ }),
    ).not.toBeInTheDocument();
    fireEvent.click(within(workspace).getByRole("tab", { name: "Runs" }));
    expect(within(workspace).getByRole("tab", { name: /Agent/ })).toBeVisible();

    fireEvent.click(
      within(workspace).getByRole("button", { name: "Hide evidence and runs" }),
    );
    expect(
      screen.queryByRole("complementary", { name: "Evidence & runs" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("main", { name: "Analysis conversation" }).parentElement,
    ).toHaveClass("artifact-collapsed");
    const toggle = screen.getByRole("button", {
      name: "Open evidence and runs",
    });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(
      await screen.findByRole("complementary", { name: "Evidence & runs" }),
    ).toBeVisible();
    expect(
      screen.getByRole("main", { name: "Analysis conversation" }).parentElement,
    ).not.toHaveClass("artifact-collapsed");
  });

  it("shows only the objective input before a task exists", async () => {
    const api = fakeApiWithCompletedTask();
    api.listTasks = async () => [];
    api.listArtifacts = async () => [];
    render(<App api={api} />);

    expect(
      await screen.findByRole("textbox", { name: "Analysis objective" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("textbox", { name: "Message the analysis agent" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByLabelText("Upload an analysis file before asking a question"),
    ).toBeVisible();
    expect(
      screen.getByRole("list", { name: "Example analysis deliverables" }),
    ).toHaveTextContent("Comparison chart");
    fireEvent.click(screen.getByRole("button", { name: /Use this brief/ }));
    expect(
      screen.getByRole("textbox", { name: "Analysis objective" }),
    ).toHaveValue(
      "Why did profit in the East region decline in March versus February? Quantify the revenue, product-cost, and logistics-cost drivers by category; create a comparison chart; and publish an evidence-linked management report with the first issue to investigate.",
    );
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

    fireEvent.change(
      await screen.findByLabelText(
        "Upload an analysis file before asking a question",
      ),
      {
        target: {
          files: [
            new File(["region,revenue"], "first.csv", { type: "text/csv" }),
          ],
        },
      },
    );

    await waitFor(() =>
      expect(upload).toHaveBeenCalledWith("project-1", expect.any(File)),
    );
    expect(await screen.findByText("Added first.csv")).toBeVisible();
  });

  it("shows approval cards and records a decision", async () => {
    const decideApproval = vi.fn(async () => ({
      id: "approval-1",
      status: "approved",
      decided_at: "2026-08-05T00:06:00Z",
    }));
    render(<App api={fakeApiWithCompletedTask(true, decideApproval)} />);

    expect(await screen.findByText("覆盖已有产物需要确认")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Approve action" }));

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

    fireEvent.click(
      await screen.findByRole("button", { name: "Configure model" }),
    );
    fireEvent.click(screen.getByRole("radio", { name: "第三方中转站" }));
    fireEvent.change(screen.getByLabelText("Base URL"), {
      target: { value: "https://relay.example/v1" },
    });
    fireEvent.change(screen.getByLabelText("Model name"), {
      target: { value: "relay-model" },
    });
    fireEvent.change(screen.getByLabelText("API Key"), {
      target: { value: "relay-secret" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Save model configuration" }),
    );

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

    fireEvent.click(
      await screen.findByRole("button", { name: "Configure model" }),
    );
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
      await screen.findByRole("button", { name: "Change model configuration" }),
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
