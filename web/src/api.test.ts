import { afterEach, describe, expect, it, vi } from "vitest";

import { HttpApiClient } from "./api";

describe("HttpApiClient", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("surfaces the message from a structured API error", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      JSON.stringify({
        detail: {
          category: "model_not_configured",
          message: "Configure a model before starting analysis",
        },
      }),
      {
        status: 409,
        headers: { "Content-Type": "application/json" },
      },
    )));
    const api = new HttpApiClient();

    await expect(api.createTask("project-1", "Diagnose profit")).rejects.toThrow(
      "Configure a model before starting analysis",
    );
  });

  it("lists teammate runs for a task", async () => {
    const runs = [{
      id: "agent-1",
      task_id: "task-1",
      role: "sql",
      objective: "Aggregate profit",
      status: "working",
      result_summary: null,
      error_category: null,
      created_at: "2026-08-06T00:00:00+00:00",
      started_at: "2026-08-06T00:00:00+00:00",
      completed_at: null,
    }];
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(runs), {
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(new HttpApiClient().listAgents("task-1")).resolves.toEqual(runs);
    expect(fetchMock).toHaveBeenCalledWith("/api/tasks/task-1/agents", undefined);
  });

  it("handles an empty successful delete response", async () => {
    const fetchMock = vi.fn(async () => new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(new HttpApiClient().deleteBusinessKnowledge("entry-1")).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledWith("/api/knowledge/entry-1", { method: "DELETE" });
  });
});
