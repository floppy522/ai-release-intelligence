import { useQuery } from "@tanstack/react-query";

import { getAnalysisRun, getDemoAnalysis } from "../api/client";
import type { AnalysisRun } from "../api/types";
import { ReleaseReport } from "../features/report/ReleaseReport";

export function App() {
  const runId = new URLSearchParams(window.location.search).get("analysis_run_id");
  const query = useQuery({
    queryKey: runId ? ["analysis-run", runId] : ["demo-analysis"],
    queryFn: () => (runId ? getAnalysisRun(runId) : getDemoAnalysis()),
  });

  if (query.isPending) return <main>Analyzing release…</main>;
  if (query.isError) return <main>Analysis unavailable</main>;

  if (runId) {
    const analysis = query.data;
    if (!isAnalysisRun(analysis)) return <main>Analysis unavailable</main>;
    return (
      <ReleaseReport
        assessment={{ status: analysis.status, findings: analysis.findings }}
        releaseName={analysis.release_name}
        sourceFetchedAt={analysis.source_fetched_at}
        repositoryFullName={analysis.repository_full_name}
        runId={analysis.run_id}
        csrfToken={csrfToken()}
        onDecisionRecorded={() => void query.refetch()}
      />
    );
  }

  const demo = query.data;
  if (isAnalysisRun(demo)) return <main>Analysis unavailable</main>;
  return (
    <ReleaseReport
      assessment={demo}
      repositoryFullName="example/release-demo"
    />
  );
}

function csrfToken(): string {
  return document.querySelector<HTMLMetaElement>('meta[name="csrf-token"]')?.content ?? "";
}

function isAnalysisRun(value: object): value is AnalysisRun {
  return "run_id" in value && typeof value.run_id === "string";
}
