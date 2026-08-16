import { render, screen, within } from "@testing-library/react";
import { expect, it } from "vitest";

import type { AIExplanationContent } from "../../api/types";
import { AIExplanation } from "./AIExplanation";

const EXPLANATION: AIExplanationContent = {
  summary: "The blocking check must be resolved before release.",
  groups: [
    {
      title: "Blocking checks",
      explanation: "The deterministic report marks this check as blocking.",
      severity: "BLOCKING",
      finding_ids: ["10000000-0000-0000-0000-000000000001"],
      evidence_ids: ["evidence-1"],
    },
  ],
  actions: [
    {
      action: "Resolve blocking check",
      finding_ids: ["10000000-0000-0000-0000-000000000001"],
      evidence_ids: ["evidence-1"],
    },
  ],
  limitations: ["Only supplied deterministic facts were used."],
  confidence: "HIGH",
  finding_ids: ["10000000-0000-0000-0000-000000000001"],
  evidence_ids: ["evidence-1"],
};

it("labels AI content and leaves deterministic status unchanged", () => {
  render(<AIExplanation explanation={EXPLANATION} status="NOT_READY" />);

  const region = screen.getByRole("region", { name: "AI explanation" });
  expect(within(region).getByRole("heading", { name: "AI explanation" })).toBeInTheDocument();
  expect(within(region).getByText("NOT READY")).toBeInTheDocument();
  expect(within(region).getByText(/does not change the deterministic readiness/i)).toBeInTheDocument();
  expect(within(region).getByText(EXPLANATION.summary)).toBeInTheDocument();
});

it("renders grounded actions as text without inventing evidence links", () => {
  render(<AIExplanation explanation={EXPLANATION} status="NOT_READY" />);

  const region = screen.getByRole("region", { name: "AI explanation" });
  expect(within(region).getByText("Resolve blocking check")).toBeInTheDocument();
  expect(within(region).queryByRole("link")).not.toBeInTheDocument();
  expect(within(region).getAllByText("Evidence IDs: evidence-1")).toHaveLength(2);
});

it("renders untrusted model text as escaped text", () => {
  const malicious = {
    ...EXPLANATION,
    summary: '<script data-secret="api-key">steal()</script>',
  };

  const { container } = render(
    <AIExplanation explanation={malicious} status="NOT_READY" />,
  );

  expect(screen.getByText(malicious.summary)).toBeInTheDocument();
  expect(container.querySelector("script")).toBeNull();
});

it.each([
  ["loading", "Generating optional AI explanation…"],
  ["unavailable", "AI explanation unavailable."],
  ["disabled", "AI explanations are disabled."],
] as const)("handles the %s state", (state, message) => {
  render(<AIExplanation state={state} status="NEEDS_DECISION" />);

  const region = screen.getByRole("region", { name: "AI explanation" });
  expect(within(region).getByText(message)).toBeInTheDocument();
  expect(within(region).getByText("NEEDS DECISION")).toBeInTheDocument();
});
