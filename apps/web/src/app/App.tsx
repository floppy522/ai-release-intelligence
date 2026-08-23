import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import {
  ApiError,
  bootstrapE2E,
  createAnalysis,
  getAIExplanation,
  getAnalysisRun,
  getCsrfBootstrap,
  getDemoAnalysis,
  getReleasePolicy,
  putReleasePolicy,
} from "../api/client";
import type {
  AnalysisRun,
  E2EBootstrap,
  PolicyRecord,
  PolicyUpsertPayload,
  ReleasePolicy,
} from "../api/types";
import { ReleaseReport } from "../features/report/ReleaseReport";

export function App() {
  const parameters = new URLSearchParams(window.location.search);
  const [runId, setRunId] = useState(parameters.get("analysis_run_id"));
  const demoMode = !runId && parameters.get("demo") === "fixture";
  const analysisQuery = useQuery({
    queryKey: ["analysis-run", runId],
    queryFn: () => getAnalysisRun(runId ?? ""),
    enabled: Boolean(runId),
  });
  const csrfQuery = useQuery({
    queryKey: ["csrf-bootstrap", runId],
    queryFn: getCsrfBootstrap,
    enabled: Boolean(runId),
    retry: false,
    staleTime: Infinity,
  });
  const demoQuery = useQuery({
    queryKey: ["demo-analysis"],
    queryFn: getDemoAnalysis,
    enabled: demoMode,
  });
  const explanationMutation = useMutation({
    mutationKey: ["ai-explanation", runId],
    mutationFn: () => {
      const csrfToken = csrfQuery.data?.csrf_token;
      if (!runId || !csrfToken) {
        throw new Error("Secure explanation request unavailable");
      }
      return getAIExplanation(runId, csrfToken);
    },
    retry: false,
  });

  if (runId) {
    if (analysisQuery.isPending || csrfQuery.isPending) {
      return <main>Loading authenticated release analysis…</main>;
    }
    if (
      analysisQuery.isError ||
      csrfQuery.isError ||
      !csrfQuery.data.csrf_token
    ) {
      return <main>Secure session unavailable. Sign in again and retry.</main>;
    }
    const analysis = analysisQuery.data;
    if (!isAnalysisRun(analysis)) return <main>Analysis unavailable</main>;
    return (
      <ReleaseReport
        assessment={{ status: analysis.status, findings: analysis.findings }}
        releaseName={analysis.release_name}
        sourceFetchedAt={analysis.source_fetched_at}
        repositoryFullName={analysis.repository_full_name}
        runId={analysis.run_id}
        csrfToken={csrfQuery.data.csrf_token}
        onDecisionRecorded={() => {
          explanationMutation.reset();
          void analysisQuery.refetch();
        }}
        aiExplanation={
          explanationMutation.data?.state === "available"
            ? explanationMutation.data.explanation
            : undefined
        }
        aiExplanationState={aiExplanationState(explanationMutation)}
        onAIExplanationRequest={() => explanationMutation.mutate()}
      />
    );
  }

  if (demoMode) {
    if (demoQuery.isPending) return <main>Loading demo fixture…</main>;
    if (demoQuery.isError) return <main>Demo analysis unavailable</main>;
    const demo = demoQuery.data;
    if (isAnalysisRun(demo)) return <main>Demo analysis unavailable</main>;
    return (
      <ReleaseReport
        assessment={demo}
        repositoryFullName="example/release-demo"
        demo
      />
    );
  }

  return (
    <main className="release-landing">
      <h1>Release intelligence</h1>
      <p>Open an analysis run to review release readiness.</p>
      {import.meta.env.VITE_ENVIRONMENT === "e2e" ? (
        <E2EReleaseStart
          onStarted={(nextRunId) => {
            window.history.replaceState(
              null,
              "",
              `/?analysis_run_id=${encodeURIComponent(nextRunId)}`,
            );
            setRunId(nextRunId);
          }}
        />
      ) : null}
    </main>
  );
}

function E2EReleaseStart({ onStarted }: { onStarted: (runId: string) => void }) {
  const [fixture, setFixture] = useState<E2EBootstrap | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState(false);

  async function connectFixture() {
    setError(false);
    try {
      setFixture(await bootstrapE2E());
    } catch {
      setError(true);
    }
  }

  async function runAnalysis() {
    if (fixture === null) return;
    setRunning(true);
    setError(false);
    try {
      const { csrf_token: csrfToken } = await getCsrfBootstrap();
      await reconcileE2EPolicy(fixture, csrfToken);
      const accepted = await createAnalysis(
        {
          repository_id: fixture.repository_id,
          milestone_number: fixture.milestone_number,
          candidate_ref: fixture.candidate_ref,
        },
        csrfToken,
      );
      onStarted(accepted.run_id);
    } catch {
      setError(true);
    } finally {
      setRunning(false);
    }
  }

  if (fixture === null) {
    return (
      <section aria-label="E2E fixture setup">
        <button type="button" onClick={() => void connectFixture()}>
          Use demo repository
        </button>
        {error ? <p role="alert">Demo repository unavailable.</p> : null}
      </section>
    );
  }

  return (
    <section aria-label="E2E fixture setup">
      <p>{fixture.repository_full_name}</p>
      <label>
        Milestone
        <select defaultValue={String(fixture.milestone_number)}>
          <option value={String(fixture.milestone_number)}>
            {fixture.milestone_number}
          </option>
        </select>
      </label>
      <label>
        Release candidate
        <select defaultValue={fixture.candidate_ref}>
          <option value={fixture.candidate_ref}>{fixture.candidate_ref}</option>
        </select>
      </label>
      <button type="button" disabled={running} onClick={() => void runAnalysis()}>
        {running ? "Running analysis…" : "Run analysis"}
      </button>
      {error ? <p role="alert">Could not run the analysis.</p> : null}
    </section>
  );
}

const E2E_DISCOVERED_CHECKS = ["blocking-suite", "advisory-tests"] as const;

function desiredE2EPolicy(fixture: E2EBootstrap): ReleasePolicy {
  return {
    main_branch: "main",
    candidate_branch: fixture.candidate_ref,
    milestone_number: fixture.milestone_number,
    code_change_label: "code-change",
    release_ops_label: "release-ops",
    blocker_label: "release-blocker",
    check_categories: {
      "blocking-suite": "BLOCKING",
      "advisory-tests": "ADVISORY",
    },
    previous_milestone_number: null,
    previous_release_branch: null,
  };
}

async function reconcileE2EPolicy(
  fixture: E2EBootstrap,
  csrfToken: string,
): Promise<PolicyRecord> {
  const desired = desiredE2EPolicy(fixture);
  const current = await getReleasePolicy(fixture.repository_id);
  if (current !== null && policiesEqual(current.policy, desired)) return current;
  try {
    return await writeE2EPolicy(
      fixture.repository_id,
      desired,
      current?.version ?? null,
      csrfToken,
    );
  } catch (error) {
    if (!(error instanceof ApiError) || error.status !== 409) throw error;
  }

  const latest = await getReleasePolicy(fixture.repository_id);
  if (latest !== null && policiesEqual(latest.policy, desired)) return latest;
  return writeE2EPolicy(
    fixture.repository_id,
    desired,
    latest?.version ?? null,
    csrfToken,
  );
}

function writeE2EPolicy(
  repositoryId: string,
  policy: ReleasePolicy,
  expectedVersion: number | null,
  csrfToken: string,
): Promise<PolicyRecord> {
  const payload: PolicyUpsertPayload = {
    ...policy,
    discovered_checks: E2E_DISCOVERED_CHECKS,
    expected_version: expectedVersion,
  };
  return putReleasePolicy(repositoryId, payload, csrfToken);
}

function policiesEqual(left: ReleasePolicy, right: ReleasePolicy): boolean {
  return JSON.stringify(canonicalPolicy(left)) === JSON.stringify(canonicalPolicy(right));
}

function canonicalPolicy(policy: ReleasePolicy) {
  return {
    main_branch: policy.main_branch,
    candidate_branch: policy.candidate_branch,
    milestone_number: policy.milestone_number,
    code_change_label: policy.code_change_label,
    release_ops_label: policy.release_ops_label,
    blocker_label: policy.blocker_label,
    check_categories: Object.entries(policy.check_categories).sort(
      ([left], [right]) => left.localeCompare(right),
    ),
    previous_milestone_number: policy.previous_milestone_number,
    previous_release_branch: policy.previous_release_branch,
  };
}

function aiExplanationState(mutation: {
  isPending: boolean;
  isError: boolean;
  data?: { state: "available" | "unavailable" };
}): "idle" | "loading" | "available" | "unavailable" {
  if (mutation.isPending) return "loading";
  if (mutation.isError || mutation.data?.state === "unavailable") {
    return "unavailable";
  }
  if (mutation.data?.state === "available") return "available";
  return "idle";
}

function isAnalysisRun(value: object): value is AnalysisRun {
  return "run_id" in value && typeof value.run_id === "string";
}
