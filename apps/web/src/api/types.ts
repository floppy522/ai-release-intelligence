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

export type CheckCategory = "BLOCKING" | "ADVISORY" | "IGNORED";

export interface ReleasePolicy {
  main_branch: string;
  candidate_branch: string;
  milestone_number: number;
  code_change_label: string;
  release_ops_label: string;
  blocker_label: string;
  check_categories: Readonly<Record<string, CheckCategory>>;
  previous_milestone_number: number | null;
  previous_release_branch: string | null;
}

export interface PolicyRecord {
  repository_id: string;
  version: number;
  policy: ReleasePolicy;
  created_at: string;
}

export interface PolicyUpsertPayload extends ReleasePolicy {
  discovered_checks: readonly string[];
  expected_version: number | null;
}
