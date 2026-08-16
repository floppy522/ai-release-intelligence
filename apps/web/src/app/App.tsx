import { useQuery } from "@tanstack/react-query";

import { getDemoAnalysis } from "../api/client";
import { ReleaseReport } from "../features/report/ReleaseReport";

export function App() {
  const query = useQuery({
    queryKey: ["demo-analysis"],
    queryFn: getDemoAnalysis,
  });

  if (query.isPending) return <main>Analyzing release…</main>;
  if (query.isError) return <main>Analysis unavailable</main>;

  return <ReleaseReport assessment={query.data} />;
}
