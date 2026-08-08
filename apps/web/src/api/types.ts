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

export type DecisionKind = "ACCEPTED_RISK" | "RELEASE_BLOCKER";

export interface DecisionFinding extends ReadinessFinding {
  finding_id: string;
}

export interface DecisionCreatePayload {
  finding_id: string;
  fingerprint: string;
  decision: DecisionKind;
  reason: string;
}

export interface HumanDecisionRecord {
  id: string;
  analysis_run_id: string;
  finding_id: string;
  fingerprint: string;
  decision: DecisionKind;
  reason: string;
  actor_id: string;
  decided_at: string;
  supersedes_decision_id: string | null;
  blocks_release: boolean;
  assessment: ReadinessAssessment;
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
