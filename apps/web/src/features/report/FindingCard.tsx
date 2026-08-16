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
      {finding.severity === "INSUFFICIENT_DATA" ? (
        <p className="finding-card__state">
          Readiness cannot be determined from this evidence.
        </p>
      ) : null}
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
  const repositoryPattern = /^[A-Za-z0-9](?:[A-Za-z0-9_.-]*[A-Za-z0-9])?\/[A-Za-z0-9](?:[A-Za-z0-9_.-]*[A-Za-z0-9])?$/;
  if (
    evidence === undefined ||
    typeof value !== "string" ||
    value.length === 0 ||
    value.length > 2_048 ||
    !isVisibleAscii(value) ||
    value.includes("%") ||
    value.includes("\\") ||
    repositoryFullName === undefined ||
    !repositoryPattern.test(repositoryFullName) ||
    !value.startsWith("https://github.com/")
  ) {
    return null;
  }
  const segments = value.slice("https://github.com/".length).split("/");
  if (segments.length < 4 || segments.some((part) => part === "" || part === "." || part === "..")) {
    return null;
  }
  const [owner, repository, ...resource] = segments;
  const observedRepository = `${owner}/${repository}`;
  if (
    !repositoryPattern.test(observedRepository) ||
    observedRepository.toLowerCase() !== repositoryFullName.toLowerCase() ||
    !matchesEvidencePath(evidence.source_type, evidence.source_id, resource)
  ) {
    return null;
  }
  return `https://github.com/${repositoryFullName}/${resource.join("/")}`;
}

function isVisibleAscii(value: string): boolean {
  return Array.from(value).every((character) => {
    const codePoint = character.codePointAt(0);
    return codePoint !== undefined && codePoint >= 0x21 && codePoint <= 0x7e;
  });
}

function isPositiveIdentifier(value: string | undefined): value is string {
  const maximum = "9223372036854775807";
  return (
    value !== undefined &&
    /^[1-9][0-9]*$/.test(value) &&
    (value.length < maximum.length ||
      (value.length === maximum.length && value <= maximum))
  );
}

function isSha(value: string | undefined): value is string {
  return value !== undefined && /^[0-9a-f]{40}$/.test(value);
}

function matchesEvidencePath(
  sourceType: string,
  sourceId: string,
  path: readonly string[],
): boolean {
  if (sourceType === "github_issue") {
    return path.length === 2 && path[0] === "issues" && isPositiveIdentifier(path[1]);
  }
  if (sourceType === "github_pull_request" || sourceType === "github_issue_pr_link") {
    return path.length === 2 && path[0] === "pull" && isPositiveIdentifier(path[1]);
  }
  if (sourceType === "github_milestone_item") {
    return path.length === 2 && path[0] === "pull" && isPositiveIdentifier(path[1]);
  }
  if (sourceType === "github_check_run" || sourceType === "github_check") {
    return (
      (path.length === 2 &&
        path[0] === "runs" &&
        isPositiveIdentifier(path[1]) &&
        path[1] === sourceId) ||
      (path.length === 4 &&
        path[0] === "runs" &&
        isPositiveIdentifier(path[1]) &&
        path[2] === "jobs" &&
        isPositiveIdentifier(path[3])) ||
      (path.length === 5 &&
        path[0] === "actions" &&
        path[1] === "runs" &&
        isPositiveIdentifier(path[2]) &&
        (path[3] === "job" || path[3] === "jobs") &&
        isPositiveIdentifier(path[4])) ||
      (path.length === 3 && path[0] === "commit" && isSha(path[1]) && path[2] === "checks")
    );
  }
  if (sourceType === "github_commit_comparison") {
    if (path.length !== 2) return false;
    if (path[0] === "pull") return isPositiveIdentifier(path[1]);
    if (path[0] !== "compare" || path[1] === undefined) return false;
    const parts = path[1].split("...");
    return parts.length === 2 && isSha(parts[0]) && isSha(parts[1]);
  }
  if (sourceType === "github_commit") {
    return path.length === 2 && path[0] === "commit" && isSha(path[1]);
  }
  if (
    sourceType === "github_milestone" ||
    sourceType === "github_release" ||
    sourceType === "assessment_evidence"
  ) {
    return path.length === 2 && path[0] === "milestone" && isPositiveIdentifier(path[1]);
  }
  return false;
}

function severityLabel(severity: string): string {
  if (severity === "BLOCKING") return "Blocking";
  if (severity === "DECISION_REQUIRED") return "Decision required";
  if (severity === "INSUFFICIENT_DATA") return "Insufficient data";
  return "Advisory";
}
