import { screen } from "@testing-library/react";
import { expect, it } from "vitest";

import type { AIExplanationContent, ReadinessAssessment } from "../api/types";
import { ReleaseReport } from "../features/report/ReleaseReport";
import { renderWithQueryClient } from "./render";

const ATTACK = '<img src=x onerror="window.pwned=true"><script>alert(1)</script>';

const assessment: ReadinessAssessment = {
  status: "NOT_READY",
  findings: [
    {
      rule_id: "checks.blocking_not_successful",
      severity: "BLOCKING",
      summary: ATTACK,
      required_action: `Do not execute javascript:alert(1); ${ATTACK}`,
      evidence: [
        {
          evidence_id: "evidence-1",
          source_type: "github_check_run",
          source_id: "1",
          url: "javascript:alert(1)",
          fingerprint: `sha256:${"1".repeat(64)}`,
        },
        {
          evidence_id: "evidence-2",
          source_type: "github_check_run",
          source_id: "2",
          url: "https://github.com/other/repository/runs/2",
          fingerprint: `sha256:${"2".repeat(64)}`,
        },
      ],
    },
  ],
};

const explanation: AIExplanationContent = {
  summary: ATTACK,
  groups: [
    {
      title: ATTACK,
      explanation: ATTACK,
      severity: "BLOCKING",
      finding_ids: ["10000000-0000-0000-0000-000000000001"],
      evidence_ids: ["evidence-1"],
    },
  ],
  actions: [
    {
      action: ATTACK,
      finding_ids: ["10000000-0000-0000-0000-000000000001"],
      evidence_ids: ["evidence-1"],
    },
  ],
  limitations: [ATTACK],
  confidence: "HIGH",
  finding_ids: ["10000000-0000-0000-0000-000000000001"],
  evidence_ids: ["evidence-1"],
};

it("renders hostile deterministic and AI strings only as text", () => {
  const { container } = renderWithQueryClient(
    <ReleaseReport
      assessment={assessment}
      repositoryFullName="acme/widgets"
      aiExplanation={explanation}
      aiExplanationState="available"
    />,
  );

  expect(screen.getAllByText(ATTACK).length).toBeGreaterThan(0);
  expect(container.querySelector("script")).toBeNull();
  expect(container.querySelector("img")).toBeNull();
  expect(container.querySelector("[onerror], [onload]")).toBeNull();
  expect(container.querySelector('a[href^="javascript:"]')).toBeNull();
  expect(screen.queryByRole("link", { name: "Open evidence" })).not.toBeInTheDocument();
});

it("keeps labels unique and accessible under hostile text", () => {
  const { container } = renderWithQueryClient(
    <ReleaseReport
      assessment={assessment}
      repositoryFullName="acme/widgets"
      aiExplanation={explanation}
      aiExplanationState="available"
    />,
  );

  expect(screen.getByRole("main")).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "What requires attention" })).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "AI explanation" })).toBeInTheDocument();
  const ids = Array.from(container.querySelectorAll("[id]"), (node) => node.id);
  expect(new Set(ids).size).toBe(ids.length);
});
