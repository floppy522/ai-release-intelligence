import { screen } from "@testing-library/react";
import { expect, it } from "vitest";

import type { AIExplanationContent, ReadinessAssessment } from "../api/types";
import { canonicalEvidenceUrl } from "../features/report/FindingCard";
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

const evidence = (
  sourceType: string,
  url: string,
  sourceId = "1",
): ReadinessAssessment["findings"][number]["evidence"][number] => ({
  evidence_id: "evidence-security",
  source_type: sourceType,
  source_id: sourceId,
  url,
  fingerprint: `sha256:${"a".repeat(64)}`,
});

it.each([
  "https://github.com/acme/widgets/issues/../issues/1",
  "https://github.com/acme/widgets/issues/%2e%2e/issues/1",
  "https://github.com/acme/widgets/issues//1",
  "https://github.com/acme/widgets\\issues/1",
  "https://github.com/acme/widgets/issues/1%2fextra",
  "https://github.com/acme/widgets/issues/9223372036854775808",
  "https://github.com/acme/widgets/issues/01",
  "https://github.com/other/widgets/issues/1",
])("rejects raw noncanonical issue evidence before browser normalization: %s", (url) => {
  expect(canonicalEvidenceUrl(evidence("github_issue", url), "acme/widgets")).toBeNull();
});

it("rejects workflow-run and generic check identity confusion", () => {
  expect(
    canonicalEvidenceUrl(
      evidence("github_check_run", "https://github.com/acme/widgets/actions/runs/800", "9400002"),
      "acme/widgets",
    ),
  ).toBeNull();
  expect(
    canonicalEvidenceUrl(
      evidence("github_check_run", "https://github.com/acme/widgets/runs/101", "102"),
      "acme/widgets",
    ),
  ).toBeNull();
});

it.each([
  `https://github.com/acme/widgets/compare/${"a".repeat(40)}....${"b".repeat(40)}`,
  `https://github.com/acme/widgets/compare/${"a".repeat(39)}...${"b".repeat(40)}`,
  `https://github.com/acme/widgets/compare/${"A".repeat(40)}...${"b".repeat(40)}`,
])("rejects malformed compare topology: %s", (url) => {
  expect(
    canonicalEvidenceUrl(evidence("github_commit_comparison", url), "acme/widgets"),
  ).toBeNull();
});

it("accepts exact bounded producer-supported evidence families", () => {
  const maximum = "9223372036854775807";
  const shaA = "a".repeat(40);
  const shaB = "b".repeat(40);
  const cases = [
    evidence("github_issue", `https://github.com/acme/widgets/issues/${maximum}`),
    evidence("github_pull_request", `https://github.com/acme/widgets/pull/${maximum}`),
    evidence(
      "github_check_run",
      "https://github.com/acme/widgets/actions/runs/8800001/jobs/7700001",
      "9400002",
    ),
    evidence(
      "github_check_run",
      `https://github.com/acme/widgets/commit/${shaA}/checks`,
      "missing:deterministic",
    ),
    evidence(
      "github_commit_comparison",
      `https://github.com/acme/widgets/compare/${shaA}...${shaB}`,
    ),
  ];

  expect(cases.map((item) => canonicalEvidenceUrl(item, "acme/widgets"))).toEqual(
    cases.map((item) => item.url),
  );
});
