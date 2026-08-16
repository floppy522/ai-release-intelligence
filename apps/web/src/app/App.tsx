import { useQuery } from "@tanstack/react-query";

import { getAnalysisRun, getCsrfBootstrap, getDemoAnalysis } from "../api/client";
import type { AnalysisRun } from "../api/types";
import { ReleaseReport } from "../features/report/ReleaseReport";

export function App() {
  const parameters = new URLSearchParams(window.location.search);
  const runId = parameters.get("analysis_run_id");
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
        onDecisionRecorded={() => void analysisQuery.refetch()}
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
    </main>
  );
}

function isAnalysisRun(value: object): value is AnalysisRun {
  return "run_id" in value && typeof value.run_id === "string";
}
