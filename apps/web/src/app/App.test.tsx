import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, vi } from "vitest";

import { getAnalysisRun, getDemoAnalysis, recordDecision } from "../api/client";
import { renderWithQueryClient } from "../test/render";
import { App } from "./App";

vi.mock("../api/client", () => ({
  getDemoAnalysis: vi.fn(),
  getAnalysisRun: vi.fn(),
  recordDecision: vi.fn(),
}));

afterEach(() => {
  window.history.replaceState(null, "", "/");
  document.querySelector('meta[name="csrf-token"]')?.remove();
  vi.clearAllMocks();
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

it("shows the verdict, blocker, action, and evidence link", async () => {
  vi.mocked(getDemoAnalysis).mockResolvedValue(NOT_READY_FIXTURE);

  renderWithQueryClient(<App />);

  expect(await screen.findByText("NOT READY")).toBeInTheDocument();
  expect(screen.getByText("Issue #142 has no linked PR")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Open evidence" })).toHaveAttribute(
    "href",
    "https://github.com/example/release-demo/issues/142",
  );
});

it("loads a real analysis-run DTO and wires authoritative decision controls", async () => {
  const runId = "10000000-0000-0000-0000-000000000001";
  const findingId = "20000000-0000-0000-0000-000000000002";
  const fingerprint = `sha256:${"a".repeat(64)}`;
  window.history.replaceState(null, "", `/?analysis_run_id=${runId}`);
  const csrf = document.createElement("meta");
  csrf.name = "csrf-token";
  csrf.content = "real-csrf-token";
  document.head.append(csrf);
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

  expect(await screen.findByText("NEEDS DECISION")).toBeInTheDocument();
  expect(getAnalysisRun).toHaveBeenCalledWith(runId);
  expect(screen.getByRole("button", { name: "Accept risk" })).toBeInTheDocument();
  expect(screen.getByText(fingerprint)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Decision reason"), {
    target: { value: "Reviewed" },
  });
  fireEvent.click(
    screen.getByRole("checkbox", {
      name: "I, current user, confirm this human decision",
    }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Accept risk" }));
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
