import { useId, useState } from "react";

import { recordDecision } from "../../api/client";
import type { DecisionFinding, DecisionKind } from "../../api/types";

interface DecisionFormProps {
  finding: DecisionFinding;
  runId?: string;
  actor?: string;
  csrfToken?: string;
  onRecorded?: () => void;
}

export function DecisionForm({
  finding,
  runId = "",
  actor = "current user",
  csrfToken = "",
  onRecorded,
}: DecisionFormProps) {
  const errorId = useId();
  const [reason, setReason] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [selected, setSelected] = useState<DecisionKind | null>(null);
  const [validation, setValidation] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const evidence = finding.evidence[0];

  async function choose(kind: DecisionKind) {
    setSelected(kind);
    setStatus(null);
    const canonicalReason = reason.trim();
    if (!canonicalReason) {
      setValidation(
        kind === "ACCEPTED_RISK"
          ? "Explain why this risk is acceptable"
          : "Explain why this release must be blocked",
      );
      return;
    }
    if (!confirmed) {
      setValidation("Confirm that this decision is yours");
      return;
    }
    if (!runId || !csrfToken || evidence === undefined) {
      setValidation("Could not record the decision. Refresh and try again.");
      return;
    }
    setValidation(null);
    setSaving(true);
    try {
      await recordDecision(
        runId,
        {
          finding_id: finding.finding_id,
          fingerprint: finding.decision_fingerprint,
          decision: kind,
          reason: canonicalReason,
        },
        csrfToken,
      );
      setStatus(
        kind === "ACCEPTED_RISK"
          ? "Accepted risk recorded"
          : "Release blocker recorded",
      );
      onRecorded?.();
    } catch {
      setValidation("Could not record the decision. Refresh and try again.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={(event) => event.preventDefault()}>
      <p className="decision-form__title">
        <strong>Human decision required</strong>
      </p>
      <p>{finding.summary}</p>
      <dl>
        <div>
          <dt>Check run</dt>
          <dd>Check run {evidence?.source_id ?? "unknown"}</dd>
        </div>
        <div>
          <dt>Fingerprint</dt>
          <dd>{finding.decision_fingerprint}</dd>
        </div>
      </dl>
      <label>
        Decision reason
        <textarea
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          maxLength={4000}
          aria-invalid={validation?.startsWith("Explain") ?? false}
          aria-describedby={validation ? errorId : undefined}
        />
      </label>
      <label>
        <input
          type="checkbox"
          checked={confirmed}
          onChange={(event) => setConfirmed(event.target.checked)}
        />
        I, {actor}, confirm this human decision
      </label>
      <div>
        <button
          type="button"
          aria-pressed={selected === "ACCEPTED_RISK"}
          disabled={saving}
          onClick={() => void choose("ACCEPTED_RISK")}
        >
          Accept risk
        </button>
        <button
          type="button"
          aria-pressed={selected === "RELEASE_BLOCKER"}
          disabled={saving}
          onClick={() => void choose("RELEASE_BLOCKER")}
        >
          Block release
        </button>
      </div>
      {validation ? (
        <p id={errorId} role="alert">
          {validation}
        </p>
      ) : null}
      {status ? <p role="status">{status}</p> : null}
    </form>
  );
}
