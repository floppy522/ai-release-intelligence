import { afterEach, expect, it, vi } from "vitest";

import { getAIExplanation, getCsrfBootstrap } from "./client";

afterEach(() => {
  vi.unstubAllGlobals();
});

const AVAILABLE_EXPLANATION = {
  state: "available",
  explanation: {
    summary: "Grounded summary",
    groups: [{
      title: "BLOCKING findings",
      explanation: "1 supplied deterministic finding has severity BLOCKING.",
      severity: "BLOCKING",
      finding_ids: ["10000000-0000-0000-0000-000000000001"],
      evidence_ids: ["evidence-1"],
    }],
    actions: [{
      action: "Resolve check 1",
      finding_ids: ["10000000-0000-0000-0000-000000000001"],
      evidence_ids: ["evidence-1"],
    }],
    limitations: ["Only deterministic facts are authoritative."],
    confidence: "HIGH",
    finding_ids: ["10000000-0000-0000-0000-000000000001"],
    evidence_ids: ["evidence-1"],
  },
  metadata: {
    model: "gpt-5.6-2026-08-01",
    latency_seconds: "0.100000",
    input_tokens: 100,
    output_tokens: 50,
    cost: "0.001000",
  },
};

function stubExplanation(payload: unknown) {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
    new Response(JSON.stringify(payload), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  ));
}

it("loads an authenticated same-origin CSRF bootstrap without caching", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ csrf_token: "server-issued-token" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);

  await expect(getCsrfBootstrap()).resolves.toEqual({
    csrf_token: "server-issued-token",
  });
  expect(fetchMock).toHaveBeenCalledWith("/api/auth/csrf", {
    credentials: "same-origin",
    cache: "no-store",
  });
});

it.each([
  new Response("unauthorized", { status: 401 }),
  new Response(JSON.stringify({ csrf_token: "" }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  }),
])("rejects failed or tokenless CSRF bootstrap responses", async (response) => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));

  await expect(getCsrfBootstrap()).rejects.toThrow();
});

it("requests an optional explanation with same-origin credentials and CSRF", async () => {
  const payload = { state: "unavailable" } as const;
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(payload), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);

  await expect(
    getAIExplanation(
      "10000000-0000-0000-0000-000000000001",
      "csrf-token",
    ),
  ).resolves.toEqual(payload);
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/analyses/10000000-0000-0000-0000-000000000001/explanation",
    {
      method: "POST",
      credentials: "same-origin",
      headers: { "X-CSRF-Token": "csrf-token" },
    },
  );
});

it.each(["\u0000", "\u202e", "\ud800", "\u2028", "\u2029"])(
  "rejects unsafe Unicode category %s in AI content",
  async (unsafe) => {
    const payload = structuredClone(AVAILABLE_EXPLANATION);
    payload.explanation.groups[0].title = `Grounded${unsafe}title`;
    stubExplanation(payload);

    await expect(
      getAIExplanation("10000000-0000-0000-0000-000000000001", "csrf-token"),
    ).rejects.toThrow("invalid");
  },
);

it("counts AI content bounds by Unicode code point", async () => {
  const exact = structuredClone(AVAILABLE_EXPLANATION);
  exact.explanation.groups[0].title = "🚀".repeat(200);
  stubExplanation(exact);

  await expect(
    getAIExplanation("10000000-0000-0000-0000-000000000001", "csrf-token"),
  ).resolves.toEqual(exact);

  const tooLong = structuredClone(AVAILABLE_EXPLANATION);
  tooLong.explanation.groups[0].title = "🚀".repeat(201);
  stubExplanation(tooLong);

  await expect(
    getAIExplanation("10000000-0000-0000-0000-000000000001", "csrf-token"),
  ).rejects.toThrow("invalid");
});

it("rejects non-NFC AI content at the client trust boundary", async () => {
  const payload = structuredClone(AVAILABLE_EXPLANATION);
  payload.explanation.summary = "Cafe\u0301";
  stubExplanation(payload);

  await expect(
    getAIExplanation("10000000-0000-0000-0000-000000000001", "csrf-token"),
  ).rejects.toThrow("invalid");
});
