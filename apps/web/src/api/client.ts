import type {
  AIExplanationContent,
  AIExplanationMetadata,
  AIExplanationResponse,
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

export async function getAIExplanation(
  runId: string,
  csrfToken: string,
): Promise<AIExplanationResponse> {
  const response = await fetch(
    `/api/analyses/${encodeURIComponent(runId)}/explanation`,
    {
      method: "POST",
      credentials: "same-origin",
      headers: { "X-CSRF-Token": csrfToken },
    },
  );
  if (!response.ok) throw new ApiError(response.status);
  const payload: unknown = await response.json();
  if (!isAIExplanationResponse(payload)) {
    throw new Error("AI explanation response was invalid");
  }
  return payload;
}

function isAIExplanationResponse(value: unknown): value is AIExplanationResponse {
  if (!isRecord(value) || value.state === "unavailable") {
    return isRecord(value) &&
      value.state === "unavailable" &&
      Object.keys(value).length === 1;
  }
  return value.state === "available" &&
    isAIExplanationContent(value.explanation) &&
    isAIExplanationMetadata(value.metadata) &&
    Object.keys(value).every((key) =>
      ["state", "explanation", "metadata"].includes(key),
    );
}

function isAIExplanationContent(value: unknown): value is AIExplanationContent {
  if (!isRecord(value)) return false;
  return isBoundedString(value.summary, 2_000) &&
    Array.isArray(value.groups) &&
    value.groups.length > 0 &&
    value.groups.length <= 20 &&
    value.groups.every(
      (group) => isRecord(group) &&
        isBoundedString(group.title, 200) &&
        isBoundedString(group.explanation, 2_000) &&
        isBoundedString(group.severity, 32) &&
        isStringArray(group.finding_ids, 100, 255) &&
        isStringArray(group.evidence_ids, 200, 255),
    ) &&
    Array.isArray(value.actions) &&
    value.actions.length <= 100 &&
    value.actions.every(
      (action) => isRecord(action) &&
        isBoundedString(action.action, 200) &&
        isStringArray(action.finding_ids, 100, 255) &&
        isStringArray(action.evidence_ids, 200, 255),
    ) &&
    isStringArray(value.limitations, 20, 2_000) &&
    ["LOW", "MEDIUM", "HIGH"].includes(String(value.confidence)) &&
    isStringArray(value.finding_ids, 1_000, 255) &&
    isStringArray(value.evidence_ids, 2_000, 255);
}

function isAIExplanationMetadata(value: unknown): value is AIExplanationMetadata {
  return isRecord(value) &&
    isBoundedString(value.model, 200) &&
    isDecimalString(value.latency_seconds) &&
    Number(value.latency_seconds) <= 15 &&
    Number.isInteger(value.input_tokens) &&
    Number(value.input_tokens) >= 0 &&
    Number(value.input_tokens) <= 10_000_000 &&
    Number.isInteger(value.output_tokens) &&
    Number(value.output_tokens) >= 0 &&
    Number(value.output_tokens) <= 10_000_000 &&
    isDecimalString(value.cost);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isBoundedString(value: unknown, maximum: number): value is string {
  return typeof value === "string" && value.length > 0 && value.length <= maximum;
}

function isStringArray(
  value: unknown,
  maximumItems: number,
  maximumLength: number,
): value is string[] {
  return Array.isArray(value) &&
    value.length > 0 &&
    value.length <= maximumItems &&
    value.every((item) => isBoundedString(item, maximumLength));
}

function isDecimalString(value: unknown): value is string {
  return typeof value === "string" &&
    /^(?:0|[1-9]\d*)(?:\.\d{1,6})?$/.test(value) &&
    Number.isFinite(Number(value));
}
