export type ReleaseStatus =
  | "READY"
  | "NOT_READY"
  | "NEEDS_DECISION"
  | "INSUFFICIENT_DATA";

export interface EvidenceRef {
  evidence_id: string;
  source_type: string;
  source_id: string;
  url: string;
  fingerprint: string;
}

export interface ReadinessFinding {
  rule_id: string;
  severity: string;
  summary: string;
  required_action: string;
  evidence: readonly EvidenceRef[];
}

export interface ReadinessAssessment {
  status: ReleaseStatus;
  findings: readonly ReadinessFinding[];
}
