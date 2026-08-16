import type { ReadinessFinding } from "../../api/types";

interface FindingCardProps {
  finding: ReadinessFinding;
  repositoryFullName?: string;
}

export function FindingCard({ finding, repositoryFullName }: FindingCardProps) {
  const evidenceUrl = finding.evidence
    .map((evidence) => canonicalEvidenceUrl(evidence, repositoryFullName))
    .find((url) => url !== null);

  return (
    <article className="finding-card" data-severity={finding.severity}>
      <div className="finding-card__meta">
        <span className="severity-label">{severityLabel(finding.severity)}</span>
        <code>{finding.rule_id}</code>
      </div>
      <p className="finding-card__summary">
        <strong>{finding.summary}</strong>
      </p>
      {evidenceUrl ? (
        <a href={evidenceUrl} target="_blank" rel="noreferrer">
          Open evidence
        </a>
      ) : (
        <span className="muted">Evidence link unavailable</span>
      )}
    </article>
  );
}

export function canonicalEvidenceUrl(
  evidence: ReadinessFinding["evidence"][number] | undefined,
  repositoryFullName: string | undefined,
): string | null {
  const value = evidence?.url;
  if (
    evidence === undefined ||
    value === undefined ||
    value.length > 2_048 ||
    repositoryFullName === undefined ||
    !/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repositoryFullName) ||
    !value.startsWith("https://github.com/")
  ) {
    return null;
  }
  try {
    const parsed = new URL(value);
    const segments = parsed.pathname.split("/").filter(Boolean);
    const [expectedOwner, expectedRepository] = repositoryFullName.split("/");
    if (
      parsed.protocol !== "https:" ||
      parsed.hostname !== "github.com" ||
      parsed.port !== "" ||
      parsed.username !== "" ||
      parsed.password !== "" ||
      parsed.search !== "" ||
      parsed.hash !== "" ||
      segments[0] !== expectedOwner ||
      segments[1] !== expectedRepository ||
      !matchesEvidencePath(evidence.source_type, segments.slice(2))
    ) {
      return null;
    }
    return parsed.toString();
  } catch {
    return null;
  }
}

function matchesEvidencePath(sourceType: string, path: readonly string[]): boolean {
  const positiveInteger = /^[1-9][0-9]{0,18}$/;
  const opaqueRef = /^[A-Za-z0-9_.:-]{1,255}(?:\.{3}[A-Za-z0-9_.:-]{1,255})?$/;
  if (sourceType === "github_issue") {
    return path.length === 2 && path[0] === "issues" && positiveInteger.test(path[1] ?? "");
  }
  if (sourceType === "github_pull_request" || sourceType === "github_issue_pr_link") {
    return path.length === 2 && path[0] === "pull" && positiveInteger.test(path[1] ?? "");
  }
  if (sourceType === "github_milestone_item") {
    return path.length === 2 && (path[0] === "issues" || path[0] === "pull") && positiveInteger.test(path[1] ?? "");
  }
  if (sourceType === "github_check_run" || sourceType === "github_check") {
    return (
      (path.length === 2 && path[0] === "runs" && positiveInteger.test(path[1] ?? "")) ||
      (path.length === 4 && path[0] === "runs" && positiveInteger.test(path[1] ?? "") && path[2] === "jobs" && positiveInteger.test(path[3] ?? "")) ||
      (path.length === 3 && path[0] === "actions" && path[1] === "runs" && positiveInteger.test(path[2] ?? "")) ||
      (path.length === 5 && path[0] === "actions" && path[1] === "runs" && positiveInteger.test(path[2] ?? "") && (path[3] === "job" || path[3] === "jobs") && positiveInteger.test(path[4] ?? "")) ||
      (path.length === 3 && path[0] === "commit" && /^[0-9a-f]{40}$/.test(path[1] ?? "") && path[2] === "checks")
    );
  }
  if (sourceType === "github_commit_comparison") {
    return path.length === 2 && (
      (path[0] === "compare" && opaqueRef.test(path[1] ?? "")) ||
      (path[0] === "pull" && positiveInteger.test(path[1] ?? ""))
    );
  }
  if (sourceType === "github_commit") {
    return path.length === 2 && path[0] === "commit" && /^[0-9a-f]{40}$/.test(path[1] ?? "");
  }
  if (
    sourceType === "github_milestone" ||
    sourceType === "github_release" ||
    sourceType === "assessment_evidence"
  ) {
    return path.length === 2 && path[0] === "milestone" && positiveInteger.test(path[1] ?? "");
  }
  return false;
}

function severityLabel(severity: string): string {
  if (severity === "BLOCKING") return "Blocking";
  if (severity === "DECISION_REQUIRED") return "Decision required";
  return "Advisory";
}
