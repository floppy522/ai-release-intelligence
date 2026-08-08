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


class SnapshotVersion(StrEnum):
    LEGACY = "legacy"
    GITHUB_V1 = "github-v1"


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
    source_id: str = ""
    created_at: datetime | None = None


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
    snapshot_version: SnapshotVersion = SnapshotVersion.LEGACY
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
    previous_milestone_number: int | None = None
    previous_release_branch: str | None = None


@dataclass(frozen=True)
class ReadinessFinding:
    rule_id: str
    severity: str
    summary: str
    required_action: str
    evidence: tuple[EvidenceRef, ...]

    @property
    def blocks_release(self) -> bool:
        return self.severity == "BLOCKING"

    @property
    def requires_decision(self) -> bool:
        return self.severity == "DECISION_REQUIRED"

    @property
    def decision_allowed(self) -> bool:
        return self.requires_decision


@dataclass(frozen=True)
class ReadinessAssessment:
    status: ReleaseStatus
    findings: tuple[ReadinessFinding, ...]
