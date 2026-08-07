from dataclasses import dataclass
from enum import StrEnum


class ReleaseStatus(StrEnum):
    READY = "READY"
    NOT_READY = "NOT_READY"
    NEEDS_DECISION = "NEEDS_DECISION"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True)
class EvidenceRef:
    evidence_id: str
    source_type: str
    source_id: str
    url: str
    fingerprint: str


@dataclass(frozen=True)
class ReleaseSnapshot:
    release_name: str
    issue_number: str
    issue_labels: tuple[str, ...]
    linked_pr_numbers: tuple[str, ...]
    issue_evidence: EvidenceRef


@dataclass(frozen=True)
class ReadinessFinding:
    rule_id: str
    severity: str
    summary: str
    required_action: str
    evidence: tuple[EvidenceRef, ...]


@dataclass(frozen=True)
class ReadinessAssessment:
    status: ReleaseStatus
    findings: tuple[ReadinessFinding, ...]
