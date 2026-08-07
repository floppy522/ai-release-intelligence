from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from release_intelligence.ports.github import (
    CommitComparison,
    GitHubCheck,
    GitHubItem,
    GitHubPullRequest,
)


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


@dataclass(frozen=True, slots=True)
class SourceError:
    code: str
    message: str
    reset_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ReleaseLink:
    issue_number: int
    pull_request_number: int
    url: str


@dataclass(frozen=True, slots=True)
class PullRequestComparison:
    pull_request_number: int
    comparison: CommitComparison


@dataclass(frozen=True)
class ReleaseSnapshot:
    release_name: str
    issue_number: str
    milestone_number: int
    issue_labels: tuple[str, ...]
    linked_pr_numbers: tuple[str, ...]
    issue_evidence: EvidenceRef
    repository_id: str = "fixture"
    repository_full_name: str = "example/release-intelligence"
    fetch_started_at: datetime | None = None
    fetched_at: datetime | None = None
    complete: bool = True
    source_errors: tuple[SourceError, ...] = ()
    candidate_ref: str = ""
    candidate_sha: str = ""
    items: tuple[GitHubItem, ...] = ()
    links: tuple[ReleaseLink, ...] = ()
    pull_requests: tuple[GitHubPullRequest, ...] = ()
    checks: tuple[GitHubCheck, ...] = ()
    comparisons: tuple[PullRequestComparison, ...] = ()


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
