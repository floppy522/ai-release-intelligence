import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { recordDecision } from "../../api/client";
import { renderWithQueryClient } from "../../test/render";
import { DecisionForm } from "./DecisionForm";

vi.mock("../../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/client")>()),
  recordDecision: vi.fn(),
}));

const FINDING = {
  finding_id: "20000000-0000-0000-0000-000000000002",
  rule_id: "checks.advisory_requires_decision",
  severity: "DECISION_REQUIRED",
  summary: "Advisory check security is not successful",
  required_action: "Accept the risk or block the release",
  evidence: [
    {
      evidence_id: "github-check-201",
      source_type: "github_check",
      source_id: "201",
      url: "https://github.com/acme/widgets/runs/201",
      fingerprint: `sha256:${"a".repeat(64)}`,
    },
  ],
} as const;

const PROPS = {
  runId: "10000000-0000-0000-0000-000000000001",
  finding: FINDING,
  actor: "octocat",
  csrfToken: "csrf-token",
};

beforeEach(() => {
  vi.clearAllMocks();
});

it("requires a reason before recording accepted risk", () => {
  renderWithQueryClient(<DecisionForm finding={FINDING} />);

  fireEvent.click(screen.getByRole("button", { name: "Accept risk" }));

  expect(
    screen.getByText("Explain why this risk is acceptable"),
  ).toBeInTheDocument();
  expect(recordDecision).not.toHaveBeenCalled();
});

it("shows fingerprint metadata and the actor confirmation", () => {
  renderWithQueryClient(<DecisionForm {...PROPS} />);

  expect(screen.getByText(FINDING.evidence[0].fingerprint)).toBeInTheDocument();
  expect(screen.getByText("Check run 201")).toBeInTheDocument();
  expect(
    screen.getByRole("checkbox", {
      name: "I, octocat, confirm this human decision",
    }),
  ).not.toBeChecked();
});

it("never preselects accepted risk", () => {
  renderWithQueryClient(<DecisionForm {...PROPS} />);

  expect(screen.getByRole("button", { name: "Accept risk" })).toHaveAttribute(
    "aria-pressed",
    "false",
  );
  expect(screen.getByRole("button", { name: "Block release" })).toHaveAttribute(
    "aria-pressed",
    "false",
  );
});

it("requires actor confirmation before submitting", () => {
  renderWithQueryClient(<DecisionForm {...PROPS} />);
  fireEvent.change(screen.getByLabelText("Decision reason"), {
    target: { value: "Reviewed by the release lead" },
  });

  fireEvent.click(screen.getByRole("button", { name: "Accept risk" }));

  expect(
    screen.getByText("Confirm that this decision is yours"),
  ).toBeInTheDocument();
  expect(recordDecision).not.toHaveBeenCalled();
});

it("records an explicitly confirmed release blocker", async () => {
  vi.mocked(recordDecision).mockResolvedValue({
    id: "30000000-0000-0000-0000-000000000003",
    analysis_run_id: PROPS.runId,
    finding_id: FINDING.finding_id,
    fingerprint: FINDING.evidence[0].fingerprint,
    decision: "RELEASE_BLOCKER",
    reason: "Security review is incomplete",
    actor_id: "github:7",
    decided_at: "2026-08-07T14:30:00Z",
    supersedes_decision_id: null,
    blocks_release: true,
    assessment: { status: "NOT_READY", findings: [FINDING] },
  });
  renderWithQueryClient(<DecisionForm {...PROPS} />);
  fireEvent.change(screen.getByLabelText("Decision reason"), {
    target: { value: "Security review is incomplete" },
  });
  fireEvent.click(
    screen.getByRole("checkbox", {
      name: "I, octocat, confirm this human decision",
    }),
  );

  fireEvent.click(screen.getByRole("button", { name: "Block release" }));

  await waitFor(() =>
    expect(recordDecision).toHaveBeenCalledWith(
      PROPS.runId,
      {
        finding_id: FINDING.finding_id,
        fingerprint: FINDING.evidence[0].fingerprint,
        decision: "RELEASE_BLOCKER",
        reason: "Security review is incomplete",
      },
      PROPS.csrfToken,
    ),
  );
  expect(await screen.findByRole("status")).toHaveTextContent(
    "Release blocker recorded",
  );
});

it("sanitizes submission failures", async () => {
  vi.mocked(recordDecision).mockRejectedValue(
    new Error("postgresql://secret@database"),
  );
  renderWithQueryClient(<DecisionForm {...PROPS} />);
  fireEvent.change(screen.getByLabelText("Decision reason"), {
    target: { value: "Reviewed" },
  });
  fireEvent.click(
    screen.getByRole("checkbox", {
      name: "I, octocat, confirm this human decision",
    }),
  );

  fireEvent.click(screen.getByRole("button", { name: "Accept risk" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Could not record the decision. Refresh and try again.",
  );
  expect(screen.queryByText(/postgresql/)).not.toBeInTheDocument();
});
