import { fireEvent, screen, within } from "@testing-library/react";
import { expect, it } from "vitest";

import type { ReadinessAssessment } from "../../api/types";
import { renderWithQueryClient } from "../../test/render";
import { ReleaseReport } from "./ReleaseReport";

const BLOCKER = {
  finding_id: "20000000-0000-0000-0000-000000000001",
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
      fingerprint: `sha256:${"1".repeat(64)}`,
    },
  ],
} as const;

const DECISION = {
  finding_id: "20000000-0000-0000-0000-000000000002",
  rule_id: "checks.advisory_requires_decision",
  severity: "DECISION_REQUIRED",
  summary: "Advisory check security requires a human decision",
  required_action: "Accept the risk or mark security as a release blocker",
  evidence: [
    {
      evidence_id: "github-check-202",
      source_type: "github_check_run",
      source_id: "202",
      url: "https://github.com/example/release-demo/runs/202",
      fingerprint: `sha256:${"2".repeat(64)}`,
    },
  ],
} as const;

const NOT_READY_ASSESSMENT: ReadinessAssessment = {
  status: "NOT_READY",
  findings: [BLOCKER],
};

it("orders verdict, attention, actions, decisions, then supporting details", () => {
  renderWithQueryClient(
    <ReleaseReport
      assessment={NOT_READY_ASSESSMENT}
      sourceFetchedAt="2026-08-07T14:30:00Z"
    />,
  );

  const headings = screen
    .getAllByRole("heading")
    .map((node) => node.textContent);
  expect(headings).toEqual([
    "Release 2026.08.10",
    "What requires attention",
    "Required actions",
    "Decisions",
    "Supporting details",
  ]);
  expect(screen.queryByText(/readiness score/i)).not.toBeInTheDocument();
  expect(screen.getByText("NOT READY")).toBeInTheDocument();
  expect(screen.getByText(/source freshness/i)).toHaveTextContent(
    "Aug 7, 2026",
  );
});

it("gives every non-pass finding one primary action and one evidence link", () => {
  renderWithQueryClient(
    <ReleaseReport assessment={NOT_READY_ASSESSMENT} />,
  );

  const attention = screen.getByLabelText("What requires attention");
  const finding = within(attention).getByRole("article");
  expect(within(finding).getByText(BLOCKER.summary)).toBeInTheDocument();
  expect(within(finding).getAllByRole("link", { name: "Open evidence" })).toHaveLength(
    1,
  );
  expect(screen.getAllByText(BLOCKER.required_action)).toHaveLength(1);
});

it("uses a native disclosure for supporting evidence", () => {
  renderWithQueryClient(
    <ReleaseReport assessment={NOT_READY_ASSESSMENT} />,
  );

  const disclosure = screen.getByText("Evidence and rule details").closest("details");
  expect(disclosure).not.toBeNull();
  expect(disclosure).not.toHaveAttribute("open");
});

it("renders decision controls only for eligible current check fingerprints", () => {
  const assessment: ReadinessAssessment = {
    status: "NEEDS_DECISION",
    findings: [DECISION, { ...BLOCKER, rule_id: "checks.advisory_requires_decision" }],
  };

  renderWithQueryClient(
    <ReleaseReport
      assessment={assessment}
      runId="10000000-0000-0000-0000-000000000001"
      actor="octocat"
      csrfToken="csrf-token"
    />,
  );

  expect(screen.getAllByRole("button", { name: "Accept risk" })).toHaveLength(1);
  expect(screen.getAllByRole("button", { name: "Block release" })).toHaveLength(1);
  expect(screen.getByText(DECISION.evidence[0].fingerprint)).toBeInTheDocument();
});

it("never offers a decision form for a release blocker decision", () => {
  const blockerDecision = { ...DECISION, severity: "BLOCKING" } as const;

  renderWithQueryClient(
    <ReleaseReport
      assessment={{ status: "NOT_READY", findings: [blockerDecision] }}
      runId="10000000-0000-0000-0000-000000000001"
      csrfToken="csrf-token"
    />,
  );

  expect(screen.queryByRole("button", { name: "Accept risk" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Block release" })).not.toBeInTheDocument();
});

it("handles all statuses and empty sections without a false clean claim", () => {
  const cases = [
    ["READY", "No findings require attention for this snapshot."],
    ["NOT_READY", "Blocker details are unavailable. Refresh the analysis."],
    ["NEEDS_DECISION", "Decision details are unavailable. Refresh the analysis."],
    [
      "INSUFFICIENT_DATA",
      "Mandatory evidence is incomplete or stale. Refresh the analysis.",
    ],
  ] as const;

  for (const [status, message] of cases) {
    const { unmount } = renderWithQueryClient(
      <ReleaseReport assessment={{ status, findings: [] }} />,
    );
    expect(screen.getByText(status.replaceAll("_", " "))).toBeInTheDocument();
    expect(screen.getByText(message)).toBeInTheDocument();
    unmount();
  }
});

it("does not render unsafe evidence URLs or untrusted values as markup", () => {
  const unsafe = {
    ...BLOCKER,
    summary: '<img src=x onerror="alert(1)">',
    evidence: [{ ...BLOCKER.evidence[0], url: "javascript:alert(1)" }],
  };

  const { container } = renderWithQueryClient(
    <ReleaseReport assessment={{ status: "NOT_READY", findings: [unsafe] }} />,
  );

  expect(screen.getByText(unsafe.summary)).toBeInTheDocument();
  expect(container.querySelector("img")).toBeNull();
  expect(screen.queryByRole("link", { name: "Open evidence" })).not.toBeInTheDocument();
  expect(screen.getAllByText("Evidence link unavailable").length).toBeGreaterThan(0);
});

it("keeps generated ARIA IDs unique with multiple decision forms", () => {
  const secondDecision = {
    ...DECISION,
    finding_id: "20000000-0000-0000-0000-000000000003",
    summary: "Advisory check compatibility requires a human decision",
    evidence: [
      {
        ...DECISION.evidence[0],
        evidence_id: "github-check-303",
        source_id: "303",
        url: "https://github.com/example/release-demo/runs/303",
        fingerprint: `sha256:${"3".repeat(64)}`,
      },
    ],
  } as const;
  const { container } = renderWithQueryClient(
    <ReleaseReport
      assessment={{ status: "NEEDS_DECISION", findings: [DECISION, secondDecision] }}
      runId="10000000-0000-0000-0000-000000000001"
      csrfToken="csrf-token"
    />,
  );
  for (const button of screen.getAllByRole("button", { name: "Accept risk" })) {
    fireEvent.click(button);
  }
  const ids = Array.from(container.querySelectorAll("[id]"), (node) => node.id);

  expect(new Set(ids).size).toBe(ids.length);
});

it("keeps section label IDs unique across multiple reports", () => {
  const { container } = renderWithQueryClient(
    <>
      <ReleaseReport assessment={{ status: "READY", findings: [] }} />
      <ReleaseReport assessment={{ status: "READY", findings: [] }} />
    </>,
  );
  const ids = Array.from(container.querySelectorAll("[id]"), (node) => node.id);

  expect(new Set(ids).size).toBe(ids.length);
});
