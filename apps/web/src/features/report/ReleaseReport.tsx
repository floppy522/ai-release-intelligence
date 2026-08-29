import { useId } from "react";

import type {
  AIExplanationContent,
  DecisionFinding,
  ReadinessAssessment,
  ReadinessFinding,
  ReleaseStatus,
} from "../../api/types";
import { AIExplanation, type AIExplanationState } from "./AIExplanation";
import { DecisionForm } from "../decisions/DecisionForm";
import { FindingCard } from "./FindingCard";
import { SupportingDetails } from "./SupportingDetails";

interface ReleaseReportProps {
  assessment: ReadinessAssessment;
  releaseName?: string;
  sourceFetchedAt?: string;
  repositoryFullName?: string;
  runId?: string;
  actor?: string;
  csrfToken?: string;
  demo?: boolean;
  onDecisionRecorded?: () => void;
  aiExplanation?: AIExplanationContent;
  aiExplanationState?: AIExplanationState;
  onAIExplanationRequest?: () => void;
}

export function ReleaseReport({
  assessment,
  releaseName = "Release 2026.08.10",
  sourceFetchedAt,
  repositoryFullName,
  runId = "",
  actor,
  csrfToken = "",
  demo = false,
  onDecisionRecorded,
  aiExplanation,
  aiExplanationState,
  onAIExplanationRequest,
}: ReleaseReportProps) {
  const sectionId = useId();
  const attentionHeadingId = `${sectionId}-attention`;
  const actionsHeadingId = `${sectionId}-actions`;
  const decisionsHeadingId = `${sectionId}-decisions`;
  const supportingHeadingId = `${sectionId}-supporting`;
  const decisions =
    assessment.status === "NEEDS_DECISION"
      ? assessment.findings.filter(isDecisionEligible)
      : [];

  return (
    <main className="release-report">
      {demo ? (
        <aside className="demo-banner" aria-label="Demo fixture warning">
          <strong>Demo fixture data</strong> — not a production readiness assessment.
        </aside>
      ) : null}
      <header className="verdict-panel" data-status={assessment.status}>
        <div>
          <p className="eyebrow">Release readiness</p>
          <h1>{releaseName}</h1>
        </div>
        <div className="verdict-panel__status">
          <strong className="status-label">{statusLabel(assessment.status)}</strong>
          <p>{freshnessLabel(sourceFetchedAt)}</p>
        </div>
      </header>

      <section aria-labelledby={attentionHeadingId}>
        <h2 id={attentionHeadingId}>What requires attention</h2>
        {assessment.findings.length > 0 ? (
          <div className="finding-list">
            {assessment.findings.map((finding) => (
              <FindingCard
                key={findingKey(finding)}
                finding={finding}
                repositoryFullName={repositoryFullName}
              />
            ))}
          </div>
        ) : (
          <p className="empty-state">{emptyAttention(assessment.status)}</p>
        )}
      </section>

      <section aria-labelledby={actionsHeadingId}>
        <h2 id={actionsHeadingId}>Required actions</h2>
        {assessment.findings.length > 0 ? (
          <ol className="action-list">
            {assessment.findings.map((finding) => (
              <li key={findingKey(finding)}>{finding.required_action}</li>
            ))}
          </ol>
        ) : (
          <p className="empty-state">{emptyActions(assessment.status)}</p>
        )}
      </section>

      <section aria-labelledby={decisionsHeadingId}>
        <h2 id={decisionsHeadingId}>Decisions</h2>
        {decisions.length > 0 && runId && csrfToken ? (
          <div className="decision-list">
            {decisions.map((finding) => (
              <DecisionForm
                key={finding.finding_id}
                finding={finding}
                runId={runId}
                actor={actor}
                csrfToken={csrfToken}
                onRecorded={onDecisionRecorded}
              />
            ))}
          </div>
        ) : (
          <p className="empty-state">
            {emptyDecisions(assessment.status, decisions.length > 0)}
          </p>
        )}
      </section>

      <SupportingDetails
        findings={assessment.findings}
        status={assessment.status}
        headingId={supportingHeadingId}
      />
      {aiExplanationState ? (
        <AIExplanation
          status={assessment.status}
          state={aiExplanationState}
          explanation={aiExplanation}
          onRequest={onAIExplanationRequest}
        />
      ) : null}
    </main>
  );
}

function isDecisionEligible(finding: ReadinessFinding): finding is DecisionFinding {
  if (
    finding.decision_eligible !== true ||
    finding.severity !== "DECISION_REQUIRED" ||
    !("finding_id" in finding) ||
    typeof finding.finding_id !== "string" ||
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(
      finding.finding_id,
    ) ||
    typeof finding.decision_fingerprint !== "string" ||
    !/^sha256:[0-9a-f]{64}$/.test(finding.decision_fingerprint)
  ) {
    return false;
  }
  return true;
}

function statusLabel(status: ReleaseStatus): string {
  return status.replaceAll("_", " ");
}

function freshnessLabel(sourceFetchedAt: string | undefined): string {
  if (!sourceFetchedAt) return "Source freshness: not provided";
  const timestamp = new Date(sourceFetchedAt);
  if (Number.isNaN(timestamp.valueOf())) {
    return "Source freshness: unavailable";
  }
  return `Source freshness: ${new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone: "UTC",
    timeZoneName: "short",
  }).format(timestamp)}`;
}

function emptyAttention(status: ReleaseStatus): string {
  if (status === "READY") return "No findings require attention for this snapshot.";
  if (status === "NOT_READY") {
    return "Blocker details are unavailable. Refresh the analysis.";
  }
  if (status === "NEEDS_DECISION") {
    return "Decision details are unavailable. Refresh the analysis.";
  }
  return "Mandatory evidence is incomplete or stale. Refresh the analysis.";
}

function emptyActions(status: ReleaseStatus): string {
  if (status === "READY") return "No required actions were recorded.";
  return "Required actions cannot be confirmed until the analysis is refreshed.";
}

function emptyDecisions(status: ReleaseStatus, controlsUnavailable: boolean): string {
  if (controlsUnavailable) {
    return "Decision controls are unavailable. Refresh the analysis.";
  }
  if (status === "READY") return "No human decisions are required for this snapshot.";
  if (status === "INSUFFICIENT_DATA") {
    return "Decision availability cannot be confirmed until evidence is complete.";
  }
  return "No decision-eligible checks are currently queued.";
}

function findingKey(finding: ReadinessFinding): string {
  return `${finding.rule_id}:${finding.severity}:${finding.summary}:${finding.evidence
    .map((evidence) => evidence.fingerprint)
    .join(":")}`;
}
