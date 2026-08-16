import type { ReadinessFinding } from "../../api/types";

interface FindingCardProps {
  finding: ReadinessFinding;
}

export function FindingCard({ finding }: FindingCardProps) {
  const evidenceUrl = canonicalEvidenceUrl(finding.evidence[0]?.url);

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

export function canonicalEvidenceUrl(value: string | undefined): string | null {
  if (value === undefined || value.length > 2_048) return null;
  try {
    const parsed = new URL(value);
    const segments = parsed.pathname.split("/").filter(Boolean);
    const safeSegment = /^[A-Za-z0-9_.:-]+$/;
    const allowedResource = new Set([
      "actions",
      "commit",
      "compare",
      "issues",
      "milestone",
      "pull",
      "runs",
    ]);
    if (
      parsed.protocol !== "https:" ||
      parsed.host !== "github.com" ||
      parsed.username !== "" ||
      parsed.password !== "" ||
      parsed.search !== "" ||
      parsed.hash !== "" ||
      segments.length < 4 ||
      segments.length > 7 ||
      !segments.every((segment) => safeSegment.test(segment)) ||
      !allowedResource.has(segments[2] ?? "")
    ) {
      return null;
    }
    return parsed.toString();
  } catch {
    return null;
  }
}

function severityLabel(severity: string): string {
  if (severity === "BLOCKING") return "Blocking";
  if (severity === "DECISION_REQUIRED") return "Decision required";
  return "Advisory";
}
