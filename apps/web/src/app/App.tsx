import { useQuery } from "@tanstack/react-query";

import { getDemoAnalysis } from "../api/client";

export function App() {
  const query = useQuery({
    queryKey: ["demo-analysis"],
    queryFn: getDemoAnalysis,
  });

  if (query.isPending) return <main>Analyzing release…</main>;
  if (query.isError) return <main>Analysis unavailable</main>;

  const finding = query.data.findings[0];
  return (
    <main>
      <h1>Release 2026.08.10</h1>
      <strong>{query.data.status.replace("_", " ")}</strong>
      <h2>{finding.summary}</h2>
      <p>{finding.required_action}</p>
      <a href={finding.evidence[0].url}>Open evidence</a>
    </main>
  );
}
