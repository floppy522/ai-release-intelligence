import { screen } from "@testing-library/react";
import { vi } from "vitest";

import { getDemoAnalysis } from "../api/client";
import { renderWithQueryClient } from "../test/render";
import { App } from "./App";

vi.mock("../api/client", () => ({ getDemoAnalysis: vi.fn() }));

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
