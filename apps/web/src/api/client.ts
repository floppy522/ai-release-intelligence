import type { ReadinessAssessment } from "./types";

export async function getDemoAnalysis(): Promise<ReadinessAssessment> {
  const response = await fetch("/api/demo/analysis");
  if (!response.ok) {
    throw new Error("Demo analysis request failed");
  }

  return (await response.json()) as ReadinessAssessment;
}
