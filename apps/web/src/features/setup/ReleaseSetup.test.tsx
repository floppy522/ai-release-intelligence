import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { getReleasePolicy, putReleasePolicy } from "../../api/client";
import { renderWithQueryClient } from "../../test/render";
import { ReleaseSetup } from "./ReleaseSetup";

vi.mock("../../api/client", () => ({
  getReleasePolicy: vi.fn(),
  putReleasePolicy: vi.fn(),
}));

const PROPS = {
  repositoryId: "987654",
  csrfToken: "csrf-token",
  discoveredChecks: ["api", "security"] as const,
};

beforeEach(() => {
  vi.mocked(getReleasePolicy).mockResolvedValue(null);
  vi.mocked(putReleasePolicy).mockReset();
});

it("requires labels, candidate branch, and a category for each check", async () => {
  renderWithQueryClient(<ReleaseSetup {...PROPS} />);

  fireEvent.click(await screen.findByRole("button", { name: "Save policy" }));

  expect(
    await screen.findByText("Classify every discovered check"),
  ).toBeInTheDocument();
  expect(screen.getByText("Enter the required release fields")).toBeInTheDocument();
  expect(putReleasePolicy).not.toHaveBeenCalled();
});

it("saves the deliberately minimal policy form", async () => {
  vi.mocked(putReleasePolicy).mockResolvedValue({
    repository_id: "987654",
    version: 1,
    created_at: "2026-08-07T14:30:00Z",
    policy: {
      main_branch: "main",
      candidate_branch: "release/2026-08-10",
      milestone_number: 7,
      code_change_label: "code-change",
      release_ops_label: "release-ops",
      blocker_label: "release-blocker",
      check_categories: { api: "BLOCKING", security: "ADVISORY" },
      previous_milestone_number: null,
      previous_release_branch: null,
    },
  });
  renderWithQueryClient(<ReleaseSetup {...PROPS} />);

  await screen.findByDisplayValue("987654");
  fireEvent.change(screen.getByLabelText("Milestone number"), {
    target: { value: "7" },
  });
  fireEvent.change(screen.getByLabelText("Main branch"), {
    target: { value: "main" },
  });
  fireEvent.change(screen.getByLabelText("Candidate branch"), {
    target: { value: "release/2026-08-10" },
  });
  fireEvent.change(screen.getByLabelText("Code-change label"), {
    target: { value: "code-change" },
  });
  fireEvent.change(screen.getByLabelText("Release-ops label"), {
    target: { value: "release-ops" },
  });
  fireEvent.change(screen.getByLabelText("Blocker label"), {
    target: { value: "release-blocker" },
  });
  fireEvent.change(screen.getByLabelText("api category"), {
    target: { value: "BLOCKING" },
  });
  fireEvent.change(screen.getByLabelText("security category"), {
    target: { value: "ADVISORY" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save policy" }));

  await waitFor(() =>
    expect(putReleasePolicy).toHaveBeenCalledWith(
      "987654",
      expect.objectContaining({
        expected_version: null,
        discovered_checks: ["api", "security"],
        check_categories: { api: "BLOCKING", security: "ADVISORY" },
      }),
      "csrf-token",
    ),
  );
  expect(await screen.findByRole("status")).toHaveTextContent("Policy version 1 saved");
});

it("shows a concise error when saving fails", async () => {
  vi.mocked(putReleasePolicy).mockRejectedValue(new Error("secret database URL"));
  renderWithQueryClient(<ReleaseSetup {...PROPS} />);

  await screen.findByDisplayValue("987654");
  for (const [label, value] of [
    ["Milestone number", "7"],
    ["Main branch", "main"],
    ["Candidate branch", "release/2026-08-10"],
    ["Code-change label", "code-change"],
    ["Release-ops label", "release-ops"],
    ["Blocker label", "release-blocker"],
    ["api category", "BLOCKING"],
    ["security category", "ADVISORY"],
  ]) {
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
  }
  fireEvent.click(screen.getByRole("button", { name: "Save policy" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Could not save policy. Reload and try again.",
  );
  expect(screen.queryByText(/secret database URL/)).not.toBeInTheDocument();
});
