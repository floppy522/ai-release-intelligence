import { fireEvent, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { beforeEach, expect, it, vi } from "vitest";

import { ApiError, getReleasePolicy, putReleasePolicy } from "../../api/client";
import { renderWithQueryClient } from "../../test/render";
import { ReleaseSetup } from "./ReleaseSetup";

vi.mock("../../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/client")>()),
  getReleasePolicy: vi.fn(),
  putReleasePolicy: vi.fn(),
}));

const PROPS = {
  repositoryId: "987654",
  csrfToken: "csrf-token",
  discoveredChecks: ["api", "security"] as const,
};

beforeEach(() => {
  vi.clearAllMocks();
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
  fireEvent.change(screen.getByLabelText("api category"), {
    target: { value: "BLOCKING" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save policy" }));
  expect(screen.getByLabelText("api category")).toHaveAttribute(
    "aria-invalid",
    "false",
  );
  expect(screen.getByLabelText("api category")).not.toHaveAttribute(
    "aria-describedby",
  );
  expect(screen.getByLabelText("security category")).toHaveAttribute(
    "aria-invalid",
    "true",
  );
  expect(screen.getByLabelText("security category")).toHaveAttribute(
    "aria-describedby",
    "checks-error",
  );
  expect(screen.getByLabelText("Candidate branch")).toHaveAttribute(
    "aria-describedby",
    "required-error",
  );
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

it("preserves a configured blocking check that is absent from discovery", async () => {
  vi.mocked(getReleasePolicy).mockResolvedValue({
    repository_id: "987654",
    version: 4,
    created_at: "2026-08-07T14:30:00Z",
    policy: {
      main_branch: "main",
      candidate_branch: "release/2026-08-10",
      milestone_number: 7,
      code_change_label: "code-change",
      release_ops_label: "release-ops",
      blocker_label: "release-blocker",
      check_categories: { api: "ADVISORY", "required-legacy": "BLOCKING" },
      previous_milestone_number: null,
      previous_release_branch: null,
    },
  });
  vi.mocked(putReleasePolicy).mockResolvedValue({
    repository_id: "987654",
    version: 5,
    created_at: "2026-08-07T14:31:00Z",
    policy: {
      main_branch: "main",
      candidate_branch: "release/2026-08-10",
      milestone_number: 7,
      code_change_label: "code-change",
      release_ops_label: "release-ops",
      blocker_label: "release-blocker",
      check_categories: { api: "ADVISORY", "required-legacy": "BLOCKING" },
      previous_milestone_number: null,
      previous_release_branch: null,
    },
  });

  renderWithQueryClient(
    <ReleaseSetup {...PROPS} discoveredChecks={["api"]} />,
  );

  expect(await screen.findByLabelText("required-legacy category")).toHaveValue(
    "BLOCKING",
  );
  fireEvent.click(screen.getByRole("button", { name: "Save policy" }));

  await waitFor(() =>
    expect(putReleasePolicy).toHaveBeenCalledWith(
      "987654",
      expect.objectContaining({
        discovered_checks: ["api", "required-legacy"],
        check_categories: { api: "ADVISORY", "required-legacy": "BLOCKING" },
      }),
      "csrf-token",
    ),
  );
});

it("refetches the latest policy after a version conflict", async () => {
  vi.mocked(putReleasePolicy).mockRejectedValue(new ApiError(409));
  renderWithQueryClient(<ReleaseSetup {...PROPS} />);
  await fillRequiredPolicy();
  fireEvent.click(screen.getByRole("button", { name: "Save policy" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Policy changed and no latest version was found.",
  );
  expect(getReleasePolicy).toHaveBeenCalledTimes(2);
});

it("applies only a freshly fetched record after conflict", async () => {
  vi.mocked(getReleasePolicy)
    .mockResolvedValueOnce(null)
    .mockResolvedValueOnce(policyRecord(9, "trunk"));
  vi.mocked(putReleasePolicy).mockRejectedValue(new ApiError(409));
  renderWithQueryClient(<ReleaseSetup {...PROPS} />);
  await fillRequiredPolicy();
  fireEvent.click(screen.getByRole("button", { name: "Save policy" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Policy changed. Latest version reloaded.",
  );
  expect(screen.getByLabelText("Main branch")).toHaveValue("trunk");
});

it("reports a conflict refetch error without claiming reload", async () => {
  vi.mocked(getReleasePolicy)
    .mockResolvedValueOnce(null)
    .mockRejectedValueOnce(new ApiError(503));
  vi.mocked(putReleasePolicy).mockRejectedValue(new ApiError(409));
  renderWithQueryClient(<ReleaseSetup {...PROPS} />);
  await fillRequiredPolicy();
  fireEvent.click(screen.getByRole("button", { name: "Save policy" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Policy changed, but the latest version could not be loaded.",
  );
  expect(screen.getByLabelText("Main branch")).toHaveValue("main");
});

it("applies the canonical policy returned by the server", async () => {
  vi.mocked(putReleasePolicy).mockResolvedValue({
    repository_id: "987654",
    version: 2,
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
  await fillRequiredPolicy();
  fireEvent.change(screen.getByLabelText("Main branch"), {
    target: { value: " main " },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save policy" }));

  expect(await screen.findByRole("status")).toHaveTextContent(
    "Policy version 2 saved",
  );
  expect(screen.getByLabelText("Main branch")).toHaveValue("main");
});

it("shows validation guidance for a server 422", async () => {
  vi.mocked(putReleasePolicy).mockRejectedValue(new ApiError(422));
  renderWithQueryClient(<ReleaseSetup {...PROPS} />);
  await fillRequiredPolicy();
  fireEvent.click(screen.getByRole("button", { name: "Save policy" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Review the policy fields and try again.",
  );
});

it("validates calendar branches before sending and marks fields invalid", async () => {
  renderWithQueryClient(<ReleaseSetup {...PROPS} />);
  await fillRequiredPolicy();
  fireEvent.change(screen.getByLabelText("Candidate branch"), {
    target: { value: "release/2026-02-30" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save policy" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Use a valid candidate release branch distinct from main.",
  );
  expect(screen.getByLabelText("Candidate branch")).toHaveAttribute(
    "aria-invalid",
    "true",
  );
  expect(putReleasePolicy).not.toHaveBeenCalled();
});

it("links both colliding branch fields to the semantic field error", async () => {
  renderWithQueryClient(<ReleaseSetup {...PROPS} />);
  await fillRequiredPolicy();
  fireEvent.change(screen.getByLabelText("Main branch"), {
    target: { value: "release/2026-08-10" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save policy" }));

  for (const label of ["Main branch", "Candidate branch"]) {
    expect(screen.getByLabelText(label)).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText(label)).toHaveAttribute(
      "aria-describedby",
      "field-error",
    );
  }
  expect(screen.getByText(/valid candidate release branch/)).toHaveAttribute(
    "id",
    "field-error",
  );
});

it("keeps configured checks when discovery changes after mount", async () => {
  vi.mocked(putReleasePolicy).mockResolvedValue(policyRecord(1, "main"));

  function Harness() {
    const [checks, setChecks] = useState<readonly string[]>(["api", "security"]);
    return (
      <>
        <button onClick={() => setChecks([...checks, "new-scan"])}>Add check</button>
        <ReleaseSetup {...PROPS} discoveredChecks={checks} />
      </>
    );
  }

  renderWithQueryClient(<Harness />);
  await fillRequiredPolicy();
  fireEvent.click(screen.getByRole("button", { name: "Add check" }));
  expect(await screen.findByLabelText("new-scan category")).toHaveValue("");
  fireEvent.click(screen.getByRole("button", { name: "Save policy" }));
  expect(await screen.findByText("Classify every discovered check")).toBeInTheDocument();
  expect(putReleasePolicy).not.toHaveBeenCalled();

  fireEvent.change(screen.getByLabelText("new-scan category"), {
    target: { value: "BLOCKING" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save policy" }));
  await waitFor(() =>
    expect(putReleasePolicy).toHaveBeenCalledWith(
      "987654",
      expect.objectContaining({
        check_categories: {
          api: "BLOCKING",
          security: "ADVISORY",
          "new-scan": "BLOCKING",
        },
      }),
      "csrf-token",
    ),
  );
});

async function fillRequiredPolicy() {
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
}

function policyRecord(version: number, mainBranch: string) {
  return {
    repository_id: "987654",
    version,
    created_at: "2026-08-07T14:30:00Z",
    policy: {
      main_branch: mainBranch,
      candidate_branch: "release/2026-08-10",
      milestone_number: 7,
      code_change_label: "code-change",
      release_ops_label: "release-ops",
      blocker_label: "release-blocker",
      check_categories: { api: "BLOCKING" as const, security: "ADVISORY" as const },
      previous_milestone_number: null,
      previous_release_branch: null,
    },
  };
}
