import { useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";

import {
  ApiError,
  getReleasePolicy,
  putReleasePolicy,
} from "../../api/client";
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
  const queryClient = useQueryClient();
  const validRepositoryId = /^[1-9]\d*$/.test(repositoryId);
  const query = useQuery({
    queryKey: ["release-policy", repositoryId],
    queryFn: () => getReleasePolicy(repositoryId),
    enabled: validRepositoryId,
  });

  if (!validRepositoryId) {
    return <p role="alert">Repository ID is invalid.</p>;
  }

  if (query.isPending) return <p>Loading release policy…</p>;
  if (query.isError && query.data === undefined) {
    return <p role="alert">Could not load release policy.</p>;
  }

  return (
    <PolicyForm
      repositoryId={repositoryId}
      csrfToken={csrfToken}
      discoveredChecks={discoveredChecks}
      initial={query.data}
      onSaved={(record) =>
        queryClient.setQueryData(["release-policy", repositoryId], record)
      }
      reload={async () => {
        const result = await query.refetch();
        if (result.isError) return { kind: "error" };
        if (!result.data) return { kind: "missing" };
        return { kind: "record", record: result.data };
      }}
    />
  );
}

interface PolicyFormProps extends ReleaseSetupProps {
  initial: PolicyRecord | null;
  onSaved: (record: PolicyRecord) => void;
  reload: () => Promise<ReloadResult>;
}

type ReloadResult =
  | { kind: "record"; record: PolicyRecord }
  | { kind: "missing" }
  | { kind: "error" };

function PolicyForm({
  repositoryId,
  csrfToken,
  discoveredChecks,
  initial,
  onSaved,
  reload,
}: PolicyFormProps) {
  const policy = initial?.policy;
  const allChecks = Array.from(
    new Set([...discoveredChecks, ...Object.keys(policy?.check_categories ?? {})]),
  ).sort();
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
      allChecks.map((check) => [
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
  const [fieldError, setFieldError] = useState(false);
  const [saveMessage, setSaveMessage] = useState("");
  const [savedVersion, setSavedVersion] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);

  function applyRecord(record: PolicyRecord) {
    const canonical = record.policy;
    setMainBranch(canonical.main_branch);
    setCandidateBranch(canonical.candidate_branch);
    setMilestoneNumber(String(canonical.milestone_number));
    setCodeChangeLabel(canonical.code_change_label);
    setReleaseOpsLabel(canonical.release_ops_label);
    setBlockerLabel(canonical.blocker_label);
    setPreviousMilestone(
      canonical.previous_milestone_number === null
        ? ""
        : String(canonical.previous_milestone_number),
    );
    setPreviousReleaseBranch(canonical.previous_release_branch ?? "");
    setCheckCategories(
      Object.fromEntries(
        Array.from(
          new Set([...allChecks, ...Object.keys(canonical.check_categories)]),
        )
          .sort()
          .map((check) => [check, canonical.check_categories[check] ?? ""]),
      ),
    );
    setExpectedVersion(record.version);
  }

  async function savePolicy(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaveError(false);
    setFieldError(false);
    setSaveMessage("");
    setSavedVersion(null);
    const requiredMissing = [
      milestoneNumber,
      mainBranch,
      candidateBranch,
      codeChangeLabel,
      releaseOpsLabel,
      blockerLabel,
    ].some((value) => value.trim() === "");
    const unclassified = allChecks.some(
      (check) => !checkCategories[check],
    );
    const semanticError = requiredMissing
      ? ""
      : validatePolicyFields({
          mainBranch,
          candidateBranch,
          milestoneNumber,
          codeChangeLabel,
          releaseOpsLabel,
          blockerLabel,
          previousMilestone,
          previousReleaseBranch,
        });
    setRequiredError(requiredMissing);
    setChecksError(unclassified);
    if (requiredMissing || unclassified || semanticError) {
      if (semanticError) {
        setSaveError(true);
        setFieldError(true);
        setSaveMessage(semanticError);
      }
      return;
    }

    const categories = Object.fromEntries(
      allChecks.map((check) => [check, checkCategories[check]]),
    ) as Record<string, CheckCategory>;
    const payload: PolicyUpsertPayload = {
      main_branch: mainBranch.trim(),
      candidate_branch: candidateBranch.trim(),
      milestone_number: Number(milestoneNumber),
      code_change_label: codeChangeLabel.trim(),
      release_ops_label: releaseOpsLabel.trim(),
      blocker_label: blockerLabel.trim(),
      discovered_checks: allChecks,
      check_categories: categories,
      previous_milestone_number: previousMilestone
        ? Number(previousMilestone)
        : null,
      previous_release_branch: previousReleaseBranch.trim() || null,
      expected_version: expectedVersion,
    };
    setSaving(true);
    try {
      const saved = await putReleasePolicy(repositoryId, payload, csrfToken);
      applyRecord(saved);
      onSaved(saved);
      setSavedVersion(saved.version);
    } catch (error) {
      setSaveError(true);
      if (error instanceof ApiError && error.status === 409) {
        const latest = await reload();
        if (latest.kind === "record") {
          applyRecord(latest.record);
          onSaved(latest.record);
          setSaveMessage("Policy changed. Latest version reloaded.");
        } else if (latest.kind === "missing") {
          setSaveMessage("Policy changed and no latest version was found.");
        } else {
          setSaveMessage("Policy changed, but the latest version could not be loaded.");
        }
      } else if (error instanceof ApiError && error.status === 422) {
        setFieldError(true);
        setSaveMessage("Review the policy fields and try again.");
      } else {
        setSaveMessage("Could not save policy. Reload and try again.");
      }
    } finally {
      setSaving(false);
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
          aria-invalid={requiredError || fieldError}
          aria-describedby={requiredError || saveError ? "policy-error" : undefined}
        />
      </label>
      <label>
        Main branch
        <input
          value={mainBranch}
          onChange={(event) => setMainBranch(event.target.value)}
          aria-invalid={requiredError || fieldError}
          aria-describedby={requiredError || saveError ? "policy-error" : undefined}
        />
      </label>
      <label>
        Candidate branch
        <input
          placeholder="release/YYYY-MM-DD"
          value={candidateBranch}
          onChange={(event) => setCandidateBranch(event.target.value)}
          aria-invalid={requiredError || fieldError}
          aria-describedby={requiredError || saveError ? "policy-error" : undefined}
        />
      </label>

      <fieldset>
        <legend>Issue labels</legend>
        <label>
          Code-change label
          <input
            value={codeChangeLabel}
            onChange={(event) => setCodeChangeLabel(event.target.value)}
            aria-invalid={requiredError || fieldError}
            aria-describedby={requiredError || saveError ? "policy-error" : undefined}
          />
        </label>
        <label>
          Release-ops label
          <input
            value={releaseOpsLabel}
            onChange={(event) => setReleaseOpsLabel(event.target.value)}
            aria-invalid={requiredError || fieldError}
            aria-describedby={requiredError || saveError ? "policy-error" : undefined}
          />
        </label>
        <label>
          Blocker label
          <input
            value={blockerLabel}
            onChange={(event) => setBlockerLabel(event.target.value)}
            aria-invalid={requiredError || fieldError}
            aria-describedby={requiredError || saveError ? "policy-error" : undefined}
          />
        </label>
      </fieldset>

      <fieldset>
        <legend>Discovered checks</legend>
        {allChecks.map((check) => (
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
              aria-invalid={checksError}
              aria-describedby={checksError ? "policy-error" : undefined}
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
            aria-invalid={fieldError}
            aria-describedby={saveError ? "policy-error" : undefined}
          />
        </label>
        <label>
          Previous release branch
          <input
            placeholder="release/YYYY-MM-DD"
            value={previousReleaseBranch}
            onChange={(event) => setPreviousReleaseBranch(event.target.value)}
            aria-invalid={fieldError}
            aria-describedby={saveError ? "policy-error" : undefined}
          />
        </label>
      </fieldset>

      {requiredError ? (
        <p id="policy-error" role="alert">Enter the required release fields</p>
      ) : null}
      {checksError ? (
        <p id={requiredError ? undefined : "policy-error"} role="alert">
          Classify every discovered check
        </p>
      ) : null}
      {saveError ? (
        <p id={requiredError || checksError ? undefined : "policy-error"} role="alert">
          {saveMessage}
        </p>
      ) : null}
      {savedVersion === null ? null : (
        <p role="status">Policy version {savedVersion} saved</p>
      )}
      <button type="submit" disabled={saving}>
        {saving ? "Saving…" : "Save policy"}
      </button>
    </form>
  );
}

interface PolicyFieldValues {
  mainBranch: string;
  candidateBranch: string;
  milestoneNumber: string;
  codeChangeLabel: string;
  releaseOpsLabel: string;
  blockerLabel: string;
  previousMilestone: string;
  previousReleaseBranch: string;
}

function validatePolicyFields(values: PolicyFieldValues): string {
  const main = values.mainBranch.trim();
  const candidate = values.candidateBranch.trim();
  if (!validReleaseBranch(candidate) || candidate === main) {
    return "Use a valid candidate release branch distinct from main.";
  }
  const labels = [
    values.codeChangeLabel,
    values.releaseOpsLabel,
    values.blockerLabel,
  ].map((label) => label.trim().toLowerCase());
  if (new Set(labels).size !== labels.length) {
    return "Use three distinct issue labels.";
  }
  const hasPreviousMilestone = values.previousMilestone.trim() !== "";
  const previousBranch = values.previousReleaseBranch.trim();
  const hasPreviousBranch = previousBranch !== "";
  if (hasPreviousMilestone !== hasPreviousBranch) {
    return "Configure both previous-release fields or leave both empty.";
  }
  if (
    hasPreviousBranch &&
    (!validReleaseBranch(previousBranch) ||
      previousBranch === candidate ||
      previousBranch === main ||
      Number(values.previousMilestone) === Number(values.milestoneNumber))
  ) {
    return "Use distinct previous-release milestone and branch values.";
  }
  return "";
}

function validReleaseBranch(branch: string): boolean {
  const match = /^release\/(\d{4}-\d{2}-\d{2})$/.exec(branch);
  if (!match) return false;
  const date = new Date(`${match[1]}T00:00:00Z`);
  return !Number.isNaN(date.valueOf()) && date.toISOString().slice(0, 10) === match[1];
}
