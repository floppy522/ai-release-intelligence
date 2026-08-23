import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, vi } from "vitest";

import {
  ApiError,
  bootstrapE2E,
  createAnalysis,
  getAnalysisRun,
  getAIExplanation,
  getCsrfBootstrap,
  getDemoAnalysis,
  getReleasePolicy,
  putReleasePolicy,
  recordDecision,
} from "../api/client";
import { renderWithQueryClient } from "../test/render";
import type { ReleasePolicy } from "../api/types";
import { App } from "./App";

vi.mock("../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/client")>()),
  bootstrapE2E: vi.fn(),
  createAnalysis: vi.fn(),
  getDemoAnalysis: vi.fn(),
  getAnalysisRun: vi.fn(),
  getCsrfBootstrap: vi.fn(),
  getAIExplanation: vi.fn(),
  getReleasePolicy: vi.fn(),
  putReleasePolicy: vi.fn(),
  recordDecision: vi.fn(),
}));

afterEach(() => {
  window.history.replaceState(null, "", "/");
  vi.clearAllMocks();
  vi.unstubAllEnvs();
});

const NOT_READY_FIXTURE = {
  status: "NOT_READY",
  findings: [
    {
      rule_id: "scope.code_change_requires_pr",
      severity: "BLOCKING",
      summary: "Issue #142 has no linked PR",
      required_action: "Link a merged PR to Issue #142",
      evidence: [
        {
          evidence_id: "github-issue-142",
          source_type: "github_issue",
          source_id: "142",
          url: "https://github.com/example/release-demo/issues/142",
          fingerprint: "github:issue:142",
        },
      ],
    },
  ],
} as const;

const E2E_POLICY = {
  main_branch: "main",
  candidate_branch: "release/2026-08-10",
  milestone_number: 7,
  code_change_label: "code-change",
  release_ops_label: "release-ops",
  blocker_label: "release-blocker",
  check_categories: {
    "blocking-suite": "BLOCKING",
    "advisory-tests": "ADVISORY",
  },
  previous_milestone_number: null,
  previous_release_branch: null,
} as const;

function policyRecord(version: number, policy: ReleasePolicy = E2E_POLICY) {
  return {
    repository_id: "987654",
    version,
    policy,
    created_at: "2026-08-16T20:00:00Z",
  };
}

function arrangeE2EStart(runId = "10000000-0000-0000-0000-000000000001") {
  vi.stubEnv("VITE_ENVIRONMENT", "e2e");
  vi.mocked(bootstrapE2E).mockResolvedValue({
    repository_id: "987654",
    repository_full_name: "floppy522/ai-release-intelligence-demo",
    milestone_number: 7,
    candidate_ref: "release/2026-08-10",
  });
  vi.mocked(getCsrfBootstrap).mockResolvedValue({ csrf_token: "real-csrf-token" });
  vi.mocked(createAnalysis).mockResolvedValue({ run_id: runId });
  vi.mocked(getAnalysisRun).mockResolvedValue({
    run_id: runId,
    status: "NEEDS_DECISION",
    release_name: "Milestone 7",
    repository_id: "987654",
    repository_full_name: "floppy522/ai-release-intelligence-demo",
    source_fetched_at: "2026-08-16T20:00:00Z",
    findings: [],
  });
}

async function startE2EAnalysis() {
  renderWithQueryClient(<App />);
  fireEvent.click(screen.getByRole("button", { name: "Use demo repository" }));
  expect(await screen.findByLabelText("Milestone")).toHaveValue("7");
  fireEvent.click(screen.getByRole("button", { name: "Run analysis" }));
  await waitFor(() => expect(createAnalysis).toHaveBeenCalledOnce());
}

it("shows explicitly requested demo data with a visible fixture warning", async () => {
  window.history.replaceState(null, "", "/?demo=fixture");
  vi.mocked(getDemoAnalysis).mockResolvedValue(NOT_READY_FIXTURE);

  renderWithQueryClient(<App />);

  expect(await screen.findByText("NOT READY")).toBeInTheDocument();
  expect(screen.getByText("Issue #142 has no linked PR")).toBeInTheDocument();
  expect(screen.getByText(/demo fixture data/i)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Open evidence" })).toHaveAttribute(
    "href",
    "https://github.com/example/release-demo/issues/142",
  );
});

it("does not silently load fixture data on the default route", () => {
  renderWithQueryClient(<App />);

  expect(screen.getByRole("heading", { name: "Release intelligence" })).toBeInTheDocument();
  expect(screen.getByText(/open an analysis run/i)).toBeInTheDocument();
  expect(getDemoAnalysis).not.toHaveBeenCalled();
  expect(getAnalysisRun).not.toHaveBeenCalled();
  expect(screen.queryByText("READY")).toBeNull();
});

it("loads a real analysis-run DTO and wires authoritative decision controls", async () => {
  const runId = "10000000-0000-0000-0000-000000000001";
  const findingId = "20000000-0000-0000-0000-000000000002";
  const fingerprint = `sha256:${"a".repeat(64)}`;
  window.history.replaceState(null, "", `/?analysis_run_id=${runId}`);
  vi.mocked(getCsrfBootstrap).mockResolvedValue({ csrf_token: "real-csrf-token" });
  vi.mocked(getAnalysisRun).mockResolvedValue({
    run_id: runId,
    status: "NEEDS_DECISION",
    release_name: "Milestone 7",
    repository_id: "987654",
    repository_full_name: "acme/widgets",
    source_fetched_at: "2026-08-07T14:30:00Z",
    findings: [
      {
        finding_id: findingId,
        decision_eligible: true,
        decision_fingerprint: fingerprint,
        rule_id: "checks.advisory_requires_decision",
        severity: "DECISION_REQUIRED",
        summary: "Advisory check security requires a human decision",
        required_action: "Accept the risk or mark security as a release blocker",
        evidence: [
          {
            evidence_id: "github-check-201",
            source_type: "github_check_run",
            source_id: "201",
            url: "https://github.com/acme/widgets/runs/201",
            fingerprint,
          },
        ],
      },
    ],
  });
  vi.mocked(recordDecision).mockResolvedValue({
    id: "30000000-0000-0000-0000-000000000003",
    analysis_run_id: runId,
    finding_id: findingId,
    fingerprint,
    decision: "ACCEPTED_RISK",
    reason: "Reviewed",
    actor_id: "github:7",
    decided_at: "2026-08-07T14:31:00Z",
    supersedes_decision_id: null,
    blocks_release: false,
    assessment: { status: "READY", findings: [] },
  });

  renderWithQueryClient(<App />);

  expect(await screen.findAllByText("NEEDS DECISION")).toHaveLength(2);
  expect(getAnalysisRun).toHaveBeenCalledWith(runId);
  expect(getCsrfBootstrap).toHaveBeenCalledOnce();
  expect(screen.getByRole("button", { name: "Accept risk" })).toBeInTheDocument();
  expect(screen.getByText(fingerprint)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Accept risk" }));
  fireEvent.change(screen.getByLabelText("Reason"), {
    target: { value: "Reviewed" },
  });
  fireEvent.click(
    screen.getByRole("checkbox", {
      name: "I, current user, confirm this human decision",
    }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Record decision" }));
  await waitFor(() =>
    expect(recordDecision).toHaveBeenCalledWith(
      runId,
      {
        finding_id: findingId,
        fingerprint,
        decision: "ACCEPTED_RISK",
        reason: "Reviewed",
      },
      "real-csrf-token",
    ),
  );
});

it("creates the first e2e policy before starting a real analysis", async () => {
  arrangeE2EStart();
  vi.mocked(getReleasePolicy).mockResolvedValue(null);
  vi.mocked(putReleasePolicy).mockResolvedValue(policyRecord(1));

  await startE2EAnalysis();

  expect(getReleasePolicy).toHaveBeenCalledWith("987654");
  expect(putReleasePolicy).toHaveBeenCalledWith(
    "987654",
    {
      ...E2E_POLICY,
      discovered_checks: ["blocking-suite", "advisory-tests"],
      expected_version: null,
    },
    "real-csrf-token",
  );
  expect(await screen.findAllByText("NEEDS DECISION")).toHaveLength(2);
});

it("reuses an identical persisted policy on a repeated e2e start", async () => {
  arrangeE2EStart();
  vi.mocked(getReleasePolicy).mockResolvedValue(policyRecord(4));

  await startE2EAnalysis();

  expect(putReleasePolicy).not.toHaveBeenCalled();
  expect(createAnalysis).toHaveBeenCalledWith(
    {
      repository_id: "987654",
      milestone_number: 7,
      candidate_ref: "release/2026-08-10",
    },
    "real-csrf-token",
  );
});

it("updates a changed e2e policy with the current expected version", async () => {
  arrangeE2EStart();
  vi.mocked(getReleasePolicy).mockResolvedValue(policyRecord(7, {
    ...E2E_POLICY,
    blocker_label: "old-release-blocker",
  }));
  vi.mocked(putReleasePolicy).mockResolvedValue(policyRecord(8));

  await startE2EAnalysis();

  expect(putReleasePolicy).toHaveBeenCalledWith(
    "987654",
    expect.objectContaining({ blocker_label: "release-blocker", expected_version: 7 }),
    "real-csrf-token",
  );
});

it("refetches and reconciles one stale e2e policy conflict", async () => {
  arrangeE2EStart();
  vi.mocked(getReleasePolicy)
    .mockResolvedValueOnce(null)
    .mockResolvedValueOnce(policyRecord(2, {
      ...E2E_POLICY,
      blocker_label: "concurrent-release-blocker",
    }));
  vi.mocked(putReleasePolicy)
    .mockRejectedValueOnce(new ApiError(409))
    .mockResolvedValueOnce(policyRecord(3));

  await startE2EAnalysis();

  expect(getReleasePolicy).toHaveBeenCalledTimes(2);
  expect(putReleasePolicy).toHaveBeenNthCalledWith(
    2,
    "987654",
    expect.objectContaining({ expected_version: 2 }),
    "real-csrf-token",
  );
  expect(createAnalysis).toHaveBeenCalledOnce();
});

it.each(["bootstrap failure", "empty bootstrap token"] as const)("fails closed when %s prevents authenticated CSRF bootstrap", async (caseName) => {
  window.history.replaceState(
    null,
    "",
    "/?analysis_run_id=10000000-0000-0000-0000-000000000001",
  );
  vi.mocked(getAnalysisRun).mockResolvedValue({
    run_id: "10000000-0000-0000-0000-000000000001",
    status: "READY",
    release_name: "Milestone 7",
    repository_id: "987654",
    repository_full_name: "acme/widgets",
    source_fetched_at: "2026-08-07T14:30:00Z",
    findings: [],
  });
  if (caseName === "bootstrap failure") {
    vi.mocked(getCsrfBootstrap).mockRejectedValue(
      new Error("session unavailable"),
    );
  } else {
    vi.mocked(getCsrfBootstrap).mockResolvedValue({ csrf_token: "" });
  }

  renderWithQueryClient(<App />);

  expect(await screen.findByText(/secure session unavailable/i)).toBeInTheDocument();
  expect(screen.queryByText("READY")).toBeNull();
  expect(screen.queryByRole("button", { name: "Accept risk" })).toBeNull();
});

it("generates an opt-in AI explanation without replacing deterministic status", async () => {
  const runId = "10000000-0000-0000-0000-000000000001";
  window.history.replaceState(null, "", `/?analysis_run_id=${runId}`);
  vi.mocked(getCsrfBootstrap).mockResolvedValue({ csrf_token: "real-csrf-token" });
  vi.mocked(getAnalysisRun).mockResolvedValue({
    run_id: runId,
    status: "NOT_READY",
    release_name: "Milestone 7",
    repository_id: "987654",
    repository_full_name: "acme/widgets",
    source_fetched_at: "2026-08-07T14:30:00Z",
    findings: NOT_READY_FIXTURE.findings,
  });
  vi.mocked(getAIExplanation).mockResolvedValue({
    state: "available",
    explanation: {
      summary: "The existing blocker must be resolved.",
      groups: [
        {
          title: "Blocking scope",
          explanation: "The deterministic report identifies a blocker.",
          severity: "BLOCKING",
          finding_ids: ["10000000-0000-0000-0000-000000000010"],
          evidence_ids: ["github-issue-142"],
        },
      ],
      actions: [
        {
          action: "Link a merged PR to Issue #142",
          finding_ids: ["10000000-0000-0000-0000-000000000010"],
          evidence_ids: ["github-issue-142"],
        },
      ],
      limitations: ["Only deterministic findings were supplied."],
      confidence: "HIGH",
      finding_ids: ["10000000-0000-0000-0000-000000000010"],
      evidence_ids: ["github-issue-142"],
    },
    metadata: {
      model: "gpt-5.6-2026-08-01",
      latency_seconds: "0.250000",
      input_tokens: 1000,
      output_tokens: 500,
      cost: "0.007500",
    },
  });

  renderWithQueryClient(<App />);

  fireEvent.click(
    await screen.findByRole("button", { name: "Generate AI explanation" }),
  );
  expect(await screen.findByText("The existing blocker must be resolved.")).toBeInTheDocument();
  expect(getAIExplanation).toHaveBeenCalledWith(runId, "real-csrf-token");
  expect(screen.getAllByText("NOT READY")).toHaveLength(2);
  expect(screen.getByText(/does not change the deterministic readiness/i)).toBeInTheDocument();
});

it("keeps the deterministic report when AI is unavailable", async () => {
  const runId = "10000000-0000-0000-0000-000000000001";
  window.history.replaceState(null, "", `/?analysis_run_id=${runId}`);
  vi.mocked(getCsrfBootstrap).mockResolvedValue({ csrf_token: "real-csrf-token" });
  vi.mocked(getAnalysisRun).mockResolvedValue({
    run_id: runId,
    status: "NOT_READY",
    release_name: "Milestone 7",
    repository_id: "987654",
    repository_full_name: "acme/widgets",
    source_fetched_at: "2026-08-07T14:30:00Z",
    findings: NOT_READY_FIXTURE.findings,
  });
  vi.mocked(getAIExplanation).mockResolvedValue({ state: "unavailable" });

  renderWithQueryClient(<App />);
  fireEvent.click(
    await screen.findByRole("button", { name: "Generate AI explanation" }),
  );

  expect(await screen.findByText("AI explanation unavailable.")).toBeInTheDocument();
  expect(screen.getByText("Issue #142 has no linked PR")).toBeInTheDocument();
});
