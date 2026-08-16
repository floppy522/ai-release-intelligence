import type { ReadinessFinding, ReleaseStatus } from "../../api/types";
import { canonicalEvidenceUrl } from "./FindingCard";

interface SupportingDetailsProps {
  findings: readonly ReadinessFinding[];
  status: ReleaseStatus;
  headingId: string;
}

export function SupportingDetails({
  findings,
  status,
  headingId,
}: SupportingDetailsProps) {
  return (
    <section aria-labelledby={headingId}>
      <h2 id={headingId}>Supporting details</h2>
      <details className="supporting-details">
        <summary>Evidence and rule details</summary>
        {findings.length === 0 ? (
          <p>{emptyDetails(status)}</p>
        ) : (
          <ol className="supporting-details__list">
            {findings.map((finding) => (
              <li key={findingKey(finding)}>
                <p>
                  <strong>{finding.rule_id}</strong> — {finding.summary}
                </p>
                <ul>
                  {finding.evidence.map((evidence, index) => {
                    const url = canonicalEvidenceUrl(evidence.url);
                    return (
                      <li key={`${evidence.evidence_id}:${evidence.fingerprint}`}>
                        {url ? (
                          <a href={url} target="_blank" rel="noreferrer">
                            View evidence {index + 1}
                          </a>
                        ) : (
                          <span>Evidence link unavailable</span>
                        )}
                        <span className="supporting-details__source">
                          {evidence.source_type} {evidence.source_id}
                        </span>
                      </li>
                    );
                  })}
                </ul>
              </li>
            ))}
          </ol>
        )}
      </details>
    </section>
  );
}

function findingKey(finding: ReadinessFinding): string {
  return `${finding.rule_id}:${finding.severity}:${finding.evidence
    .map((evidence) => evidence.fingerprint)
    .join(":")}`;
}

function emptyDetails(status: ReleaseStatus): string {
  if (status === "READY") return "No rule violations were recorded for this snapshot.";
  return "No supporting finding details are available. Refresh the analysis.";
}
