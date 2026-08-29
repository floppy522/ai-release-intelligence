import { useId } from "react";

import type {
  AIExplanationContent,
  ReleaseStatus,
} from "../../api/types";

export type AIExplanationState =
  | "available"
  | "idle"
  | "loading"
  | "unavailable"
  | "disabled";

interface AIExplanationProps {
  status: ReleaseStatus;
  explanation?: AIExplanationContent;
  state?: AIExplanationState;
  onRequest?: () => void;
}

export function AIExplanation({
  status,
  explanation,
  state,
  onRequest,
}: AIExplanationProps) {
  const headingId = `${useId()}-ai-explanation`;
  const effectiveState = state ?? (explanation ? "available" : "disabled");
  const available = effectiveState === "available" && explanation !== undefined;

  return (
    <section
      className="ai-explanation"
      aria-labelledby={headingId}
      aria-live={effectiveState === "loading" ? "polite" : undefined}
    >
      <header className="ai-explanation__header">
        <div>
          <p className="eyebrow">Optional context</p>
          <h2 id={headingId}>AI explanation</h2>
        </div>
        <span className="ai-explanation__status" aria-label="Deterministic readiness">
          {status.replaceAll("_", " ")}
        </span>
      </header>
      <p className="ai-explanation__boundary">
        This optional explanation does not change the deterministic readiness,
        evidence, severity, or decisions shown above.
      </p>

      {available ? (
        <div className="ai-explanation__content">
          <p>{explanation.summary}</p>
          <div className="ai-explanation__groups">
            {explanation.groups.map((group) => (
              <article
                key={`${group.severity}:${group.finding_ids.join(":")}`}
                className="ai-explanation__group"
              >
                <h3>{group.title}</h3>
                <p>{group.explanation}</p>
                <p className="muted">Severity: {group.severity.replaceAll("_", " ")}</p>
                <ReferenceText label="Evidence IDs" values={group.evidence_ids} />
              </article>
            ))}
          </div>
          {explanation.actions.length > 0 ? (
            <div>
              <h3>Existing deterministic actions</h3>
              <ol className="action-list">
                {explanation.actions.map((action) => (
                  <li key={`${action.action}:${action.finding_ids.join(":")}`}>
                    <span>{action.action}</span>
                    <ReferenceText label="Evidence IDs" values={action.evidence_ids} />
                  </li>
                ))}
              </ol>
            </div>
          ) : null}
          <details>
            <summary>AI limitations</summary>
            <ul>
              {explanation.limitations.map((limitation) => (
                <li key={limitation}>{limitation}</li>
              ))}
            </ul>
            <p>Grounding confidence: {explanation.confidence}</p>
          </details>
        </div>
      ) : (
        <div>
          <p role={effectiveState === "loading" ? "status" : undefined}>
            {stateMessage(effectiveState)}
          </p>
          {effectiveState === "idle" && onRequest ? (
            <button type="button" onClick={onRequest}>
              Generate AI explanation
            </button>
          ) : null}
        </div>
      )}
    </section>
  );
}

function ReferenceText({
  label,
  values,
}: {
  label: string;
  values: readonly string[];
}) {
  return <p className="muted">{label}: {values.join(", ")}</p>;
}

function stateMessage(state: AIExplanationState): string {
  if (state === "idle") return "Generate optional context from the deterministic report.";
  if (state === "loading") return "Generating optional AI explanation…";
  if (state === "unavailable") return "AI explanation unavailable.";
  if (state === "disabled") return "AI explanations are disabled.";
  return "AI explanation unavailable.";
}
