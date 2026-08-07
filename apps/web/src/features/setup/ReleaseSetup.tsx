import { useQuery } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";

import { getReleasePolicy, putReleasePolicy } from "../../api/client";
import type {
  CheckCategory,
  PolicyRecord,
  PolicyUpsertPayload,
} from "../../api/types";

interface ReleaseSetupProps {
  repositoryId: string;
  csrfToken: string;
  discoveredChecks: readonly string[];
}

type CheckSelection = CheckCategory | "";

const CHECK_OPTIONS: readonly CheckCategory[] = [
  "BLOCKING",
  "ADVISORY",
  "IGNORED",
];

export function ReleaseSetup({
  repositoryId,
  csrfToken,
  discoveredChecks,
}: ReleaseSetupProps) {
  const query = useQuery({
    queryKey: ["release-policy", repositoryId],
    queryFn: () => getReleasePolicy(repositoryId),
  });

  if (query.isPending) return <p>Loading release policy…</p>;
  if (query.isError) return <p role="alert">Could not load release policy.</p>;

  return (
    <PolicyForm
      key={query.data?.version ?? 0}
      repositoryId={repositoryId}
      csrfToken={csrfToken}
      discoveredChecks={discoveredChecks}
      initial={query.data}
    />
  );
}

interface PolicyFormProps extends ReleaseSetupProps {
  initial: PolicyRecord | null;
}

function PolicyForm({
  repositoryId,
  csrfToken,
  discoveredChecks,
  initial,
}: PolicyFormProps) {
  const policy = initial?.policy;
  const [mainBranch, setMainBranch] = useState(policy?.main_branch ?? "");
  const [candidateBranch, setCandidateBranch] = useState(
    policy?.candidate_branch ?? "",
  );
  const [milestoneNumber, setMilestoneNumber] = useState(
    policy ? String(policy.milestone_number) : "",
  );
  const [codeChangeLabel, setCodeChangeLabel] = useState(
    policy?.code_change_label ?? "",
  );
  const [releaseOpsLabel, setReleaseOpsLabel] = useState(
    policy?.release_ops_label ?? "",
  );
  const [blockerLabel, setBlockerLabel] = useState(
    policy?.blocker_label ?? "",
  );
  const [previousMilestone, setPreviousMilestone] = useState(
    policy?.previous_milestone_number == null
      ? ""
      : String(policy.previous_milestone_number),
  );
  const [previousReleaseBranch, setPreviousReleaseBranch] = useState(
    policy?.previous_release_branch ?? "",
  );
  const [checkCategories, setCheckCategories] = useState<
    Record<string, CheckSelection>
  >(() =>
    Object.fromEntries(
      discoveredChecks.map((check) => [
        check,
        policy?.check_categories[check] ?? "",
      ]),
    ),
  );
  const [expectedVersion, setExpectedVersion] = useState<number | null>(
    initial?.version ?? null,
  );
  const [requiredError, setRequiredError] = useState(false);
  const [checksError, setChecksError] = useState(false);
  const [saveError, setSaveError] = useState(false);
  const [savedVersion, setSavedVersion] = useState<number | null>(null);

  async function savePolicy(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaveError(false);
    setSavedVersion(null);
    const requiredMissing = [
      milestoneNumber,
      mainBranch,
      candidateBranch,
      codeChangeLabel,
      releaseOpsLabel,
      blockerLabel,
    ].some((value) => value.trim() === "");
    const unclassified = discoveredChecks.some(
      (check) => !checkCategories[check],
    );
    setRequiredError(requiredMissing);
    setChecksError(unclassified);
    if (requiredMissing || unclassified) return;

    const categories = Object.fromEntries(
      discoveredChecks.map((check) => [check, checkCategories[check]]),
    ) as Record<string, CheckCategory>;
    const payload: PolicyUpsertPayload = {
      main_branch: mainBranch.trim(),
      candidate_branch: candidateBranch.trim(),
      milestone_number: Number(milestoneNumber),
      code_change_label: codeChangeLabel.trim(),
      release_ops_label: releaseOpsLabel.trim(),
      blocker_label: blockerLabel.trim(),
      discovered_checks: discoveredChecks,
      check_categories: categories,
      previous_milestone_number: previousMilestone
        ? Number(previousMilestone)
        : null,
      previous_release_branch: previousReleaseBranch.trim() || null,
      expected_version: expectedVersion,
    };
    try {
      const saved = await putReleasePolicy(repositoryId, payload, csrfToken);
      setExpectedVersion(saved.version);
      setSavedVersion(saved.version);
    } catch {
      setSaveError(true);
    }
  }

  return (
    <form onSubmit={(event) => void savePolicy(event)}>
      <h1>Release setup</h1>

      <label>
        Repository
        <input value={repositoryId} readOnly />
      </label>
      <label>
        Milestone number
        <input
          type="number"
          min="1"
          value={milestoneNumber}
          onChange={(event) => setMilestoneNumber(event.target.value)}
        />
      </label>
      <label>
        Main branch
        <input
          value={mainBranch}
          onChange={(event) => setMainBranch(event.target.value)}
        />
      </label>
      <label>
        Candidate branch
        <input
          placeholder="release/YYYY-MM-DD"
          value={candidateBranch}
          onChange={(event) => setCandidateBranch(event.target.value)}
        />
      </label>

      <fieldset>
        <legend>Issue labels</legend>
        <label>
          Code-change label
          <input
            value={codeChangeLabel}
            onChange={(event) => setCodeChangeLabel(event.target.value)}
          />
        </label>
        <label>
          Release-ops label
          <input
            value={releaseOpsLabel}
            onChange={(event) => setReleaseOpsLabel(event.target.value)}
          />
        </label>
        <label>
          Blocker label
          <input
            value={blockerLabel}
            onChange={(event) => setBlockerLabel(event.target.value)}
          />
        </label>
      </fieldset>

      <fieldset>
        <legend>Discovered checks</legend>
        {discoveredChecks.map((check) => (
          <label key={check}>
            {check} category
            <select
              value={checkCategories[check] ?? ""}
              onChange={(event) =>
                setCheckCategories((current) => ({
                  ...current,
                  [check]: event.target.value as CheckSelection,
                }))
              }
            >
              <option value="">Choose category</option>
              {CHECK_OPTIONS.map((category) => (
                <option key={category} value={category}>
                  {category}
                </option>
              ))}
            </select>
          </label>
        ))}
      </fieldset>

      <fieldset>
        <legend>Previous release (optional)</legend>
        <label>
          Previous milestone number
          <input
            type="number"
            min="1"
            value={previousMilestone}
            onChange={(event) => setPreviousMilestone(event.target.value)}
          />
        </label>
        <label>
          Previous release branch
          <input
            placeholder="release/YYYY-MM-DD"
            value={previousReleaseBranch}
            onChange={(event) => setPreviousReleaseBranch(event.target.value)}
          />
        </label>
      </fieldset>

      {requiredError ? (
        <p role="alert">Enter the required release fields</p>
      ) : null}
      {checksError ? (
        <p role="alert">Classify every discovered check</p>
      ) : null}
      {saveError ? (
        <p role="alert">Could not save policy. Reload and try again.</p>
      ) : null}
      {savedVersion === null ? null : (
        <p role="status">Policy version {savedVersion} saved</p>
      )}
      <button type="submit">Save policy</button>
    </form>
  );
}
