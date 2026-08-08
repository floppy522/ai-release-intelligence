import type {
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
