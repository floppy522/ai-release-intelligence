import { afterEach, expect, it, vi } from "vitest";

import {
  bootstrapE2E,
  createAnalysis,
  getAIExplanation,
  getCsrfBootstrap,
} from "./client";

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

it("bootstraps e2e auth and creates an analysis through same-origin CSRF requests", async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({
      repository_id: "987654",
      repository_full_name: "floppy522/ai-release-intelligence-demo",
      milestone_number: 7,
      candidate_ref: "release/2026-08-10",
    }), { status: 201, headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify({
      run_id: "10000000-0000-0000-0000-000000000001",
    }), { status: 202, headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);

  await bootstrapE2E();
  await createAnalysis(
    {
      repository_id: "987654",
      milestone_number: 7,
      candidate_ref: "release/2026-08-10",
    },
    "csrf-token",
  );

  expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/e2e/bootstrap", {
    method: "POST",
    credentials: "same-origin",
  });
  expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/analyses", {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": "csrf-token",
    },
    body: JSON.stringify({
      repository_id: "987654",
      milestone_number: 7,
      candidate_ref: "release/2026-08-10",
    }),
  });
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

it("accepts a grounded READY explanation with no findings", async () => {
  const payload = {
    state: "available",
    explanation: {
      summary: "0 supplied deterministic findings are organized below; readiness remains READY.",
      groups: [],
      actions: [],
      limitations: ["Only deterministic findings are authoritative."],
      confidence: "HIGH",
      finding_ids: [],
      evidence_ids: [],
    },
    metadata: {
      model: "gpt-5.6-2026-08-20",
      latency_seconds: "0.100000",
      input_tokens: 100,
      output_tokens: 50,
      cost: "0.001000",
    },
  } as const;
  stubExplanation(payload);

  await expect(
    getAIExplanation(
      "10000000-0000-0000-0000-000000000001",
      "csrf-token",
    ),
  ).resolves.toEqual(payload);
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
