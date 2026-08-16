import type {
  AnalysisRun,
  CsrfBootstrap,
  DecisionCreatePayload,
  HumanDecisionRecord,
  PolicyRecord,
  PolicyUpsertPayload,
  ReadinessAssessment,
} from "./types";

export class ApiError extends Error {
  constructor(readonly status: number) {
    super(`API request failed with status ${String(status)}`);
    this.name = "ApiError";
  }
}

export async function getDemoAnalysis(): Promise<ReadinessAssessment> {
  const response = await fetch("/api/demo/analysis");
  if (!response.ok) {
    throw new Error("Demo analysis request failed");
  }

  return (await response.json()) as ReadinessAssessment;
}

export async function getAnalysisRun(runId: string): Promise<AnalysisRun> {
  const response = await fetch(`/api/analyses/${encodeURIComponent(runId)}`, {
    credentials: "same-origin",
  });
  if (!response.ok) throw new ApiError(response.status);
  return (await response.json()) as AnalysisRun;
}

export async function getCsrfBootstrap(): Promise<CsrfBootstrap> {
  const response = await fetch("/api/auth/csrf", {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!response.ok) throw new ApiError(response.status);
  const payload: unknown = await response.json();
  if (
    typeof payload !== "object" ||
    payload === null ||
    !("csrf_token" in payload) ||
    typeof payload.csrf_token !== "string" ||
    payload.csrf_token.length === 0 ||
    payload.csrf_token.length > 1_024
  ) {
    throw new Error("CSRF bootstrap response was invalid");
  }
  return { csrf_token: payload.csrf_token };
}

export async function getReleasePolicy(
  repositoryId: string,
): Promise<PolicyRecord | null> {
  const response = await fetch(
    `/api/repositories/${encodeURIComponent(repositoryId)}/policy`,
    { credentials: "same-origin" },
  );
  if (response.status === 404) return null;
  if (!response.ok) throw new ApiError(response.status);
  return (await response.json()) as PolicyRecord;
}

export async function putReleasePolicy(
  repositoryId: string,
  policy: PolicyUpsertPayload,
  csrfToken: string,
): Promise<PolicyRecord> {
  const response = await fetch(
    `/api/repositories/${encodeURIComponent(repositoryId)}/policy`,
    {
      method: "PUT",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
      },
      body: JSON.stringify(policy),
    },
  );
  if (!response.ok) throw new ApiError(response.status);
  return (await response.json()) as PolicyRecord;
}

export async function recordDecision(
  runId: string,
  payload: DecisionCreatePayload,
  csrfToken: string,
): Promise<HumanDecisionRecord> {
  const response = await fetch(
    `/api/analyses/${encodeURIComponent(runId)}/decisions`,
    {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
      },
      body: JSON.stringify(payload),
    },
  );
  if (!response.ok) throw new ApiError(response.status);
  return (await response.json()) as HumanDecisionRecord;
}
