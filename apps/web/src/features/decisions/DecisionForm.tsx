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

  function choose(kind: DecisionKind) {
    setSelected(kind);
    setStatus(null);
    setValidation(null);
  }

  async function submit() {
    if (selected === null) {
      setValidation("Choose whether to accept the risk or block the release");
      return;
    }
    const canonicalReason = reason.trim();
    if (!canonicalReason) {
      setValidation(
        selected === "ACCEPTED_RISK"
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
          decision: selected,
          reason: canonicalReason,
        },
        csrfToken,
      );
      setStatus(
        selected === "ACCEPTED_RISK"
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
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
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
        Reason
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
          onClick={() => choose("ACCEPTED_RISK")}
        >
          Accept risk
        </button>
        <button
          type="button"
          aria-pressed={selected === "RELEASE_BLOCKER"}
          disabled={saving}
          onClick={() => choose("RELEASE_BLOCKER")}
        >
          Block release
        </button>
      </div>
      <button type="submit" disabled={saving}>
        Record decision
      </button>
      {validation ? (
        <p id={errorId} role="alert">
          {validation}
        </p>
      ) : null}
      {status ? <p role="status">{status}</p> : null}
    </form>
  );
}
