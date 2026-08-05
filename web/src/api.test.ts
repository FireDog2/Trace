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
});
