from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from release_intelligence.adapters.fixtures.github_source import load_demo_release
from release_intelligence.domain.assessment import assess_release
from release_intelligence.domain.models import (
    EvidenceRef,
    PullRequestComparison,
    ReadinessAssessment,
    ReleaseLink,
    ReleaseSnapshot,
    ReleaseStatus,
    SnapshotVersion,
    SourceError,
)
from release_intelligence.ports.github import (
    GitHubCheck,
    GitHubError,
    GitHubItem,
    GitHubItemKind,
    GitHubMilestone,
    GitHubNotFound,
    GitHubPartialData,
    GitHubPullRequest,
    GitHubRateLimited,
    GitHubSource,
    GitHubUnauthorized,
    RepoRef,
)
from release_intelligence.ports.repositories import (
    AnalysisRepositoryPort,
    StoredAnalysisRun,
)

MAX_SNAPSHOT_AGE = timedelta(minutes=10)
MAX_MILESTONE_ITEMS = 100
MAX_RELATED_PULL_REQUESTS = 200
MAX_CANDIDATE_CHECKS = 100


@dataclass(frozen=True, slots=True)
class AnalysisRequest:
    repository_id: str
    repository: RepoRef
    installation_id: int
    milestone_number: int
    candidate_ref: str


class MissingMilestone(Exception):
    """The requested milestone does not exist in the authorized repository."""


class MissingCandidateRef(Exception):
    """The requested release-candidate branch does not exist."""


class ReleaseLoader(Protocol):
    async def load(self, request: AnalysisRequest) -> ReleaseSnapshot: ...


LoaderFactory = Callable[[AnalysisRequest], Awaitable[ReleaseLoader]]


class AnalysisService:
    def __init__(
        self,
        *,
        loader_factory: LoaderFactory,
        repository: AnalysisRepositoryPort,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._loader_factory = loader_factory
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))

    async def run(self, request: AnalysisRequest, actor: str) -> UUID:
        if not actor:
            raise ValueError("actor is required")
        bootstrap_started_at = self._now()
        try:
            loader = await self._loader_factory(request)
            snapshot = await loader.load(request)
        except GitHubRateLimited as error:
            snapshot = _unavailable_snapshot(
                request,
                bootstrap_started_at,
                self._now(),
                SourceError(
                    code="github.rate_limited",
                    message="GitHub rate limit prevented a complete snapshot",
                    reset_at=error.reset_at,
                ),
            )
        except GitHubPartialData:
            snapshot = _unavailable_snapshot(
                request,
                bootstrap_started_at,
                self._now(),
                SourceError(
                    code="github.partial_data",
                    message="GitHub returned incomplete release evidence",
                ),
            )
        now = self._now()
        assessment = assess(snapshot, policy=None, decisions=(), now=now)
        source_fetched_at = snapshot.fetched_at
        if source_fetched_at is None:
            raise ValueError("loaded snapshot requires fetched_at")
        return await self._repository.create_run(
            snapshot=snapshot,
            findings=assessment.findings,
            assessment=assessment,
            policy_version="default-v1",
            source_fetched_at=source_fetched_at,
        )

    async def get(self, run_id: UUID) -> StoredAnalysisRun:
        return await self._repository.get_run(run_id)

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class _EvidenceWindow:
    milestone: GitHubMilestone
    items: tuple[GitHubItem, ...]
    candidate_sha: str
    links: tuple[ReleaseLink, ...]
    pull_requests: tuple[GitHubPullRequest, ...]
    checks: tuple[GitHubCheck, ...]
    comparisons: tuple[PullRequestComparison, ...]


class GitHubReleaseLoader:
    """Load one bounded, internally consistent, normalized GitHub evidence window."""

    def __init__(
        self,
        source: GitHubSource,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._source = source
        self._clock = clock or (lambda: datetime.now(UTC))

    async def load(self, request: AnalysisRequest) -> ReleaseSnapshot:
        fetch_started_at = self._now()
        latest: _EvidenceWindow | None = None
        for _attempt in range(2):
            try:
                first = await self._collect_window(request)
                second = await self._collect_window(request)
            except GitHubRateLimited as error:
                return self._incomplete(
                    request,
                    fetch_started_at,
                    SourceError(
                        code="github.rate_limited",
                        message="GitHub rate limit prevented a complete snapshot",
                        reset_at=error.reset_at,
                    ),
                )
            except GitHubUnauthorized:
                raise
            except GitHubError:
                return self._incomplete(
                    request,
                    fetch_started_at,
                    SourceError(
                        code="github.partial_data",
                        message="GitHub returned incomplete release evidence",
                    ),
                )
            latest = second
            if first == second:
                return self._snapshot(request, fetch_started_at, second)
        assert latest is not None
        return replace(
            self._snapshot(request, fetch_started_at, latest),
            complete=False,
            source_errors=(
                SourceError(
                    code="github.inconsistent_state",
                    message="GitHub release state changed during both fetch windows",
                ),
            ),
            fetched_at=self._now(),
        )

    async def _collect_window(self, request: AnalysisRequest) -> _EvidenceWindow:
        milestone = await self._get_milestone(request)
        items = tuple(
            sorted(
                await self._source.list_milestone_items(
                    request.repository, request.milestone_number
                ),
                key=lambda item: (item.kind.value, item.source_id, item.number),
            )
        )
        if len(items) > MAX_MILESTONE_ITEMS:
            raise GitHubPartialData()
        candidate_sha = await self._resolve_candidate(request)

        links_by_key: dict[tuple[int, int, str], ReleaseLink] = {}
        event_count = 0
        pull_numbers = {
            item.number
            for item in items
            if item.kind is GitHubItemKind.PULL_REQUEST
        }
        for item in items:
            if item.kind is not GitHubItemKind.ISSUE:
                continue
            events = await self._source.list_issue_timeline(
                request.repository, item.number
            )
            for event in events:
                event_count += 1
                if event_count > MAX_RELATED_PULL_REQUESTS:
                    raise GitHubPartialData()
                link_key = (
                    item.number,
                    event.pull_request_number,
                    event.pull_request_url,
                )
                candidate = ReleaseLink(
                    source_id=event.source_id,
                    issue_number=item.number,
                    pull_request_number=event.pull_request_number,
                    url=event.pull_request_url,
                    created_at=event.created_at,
                )
                current = links_by_key.get(link_key)
                if current is None or (candidate.source_id, candidate.created_at) < (
                    current.source_id,
                    current.created_at,
                ):
                    links_by_key[link_key] = candidate
                pull_numbers.add(event.pull_request_number)
                if len(pull_numbers) > MAX_RELATED_PULL_REQUESTS:
                    raise GitHubPartialData()
        pulls = tuple(
            sorted(
                [
                await self._source.get_pull_request(request.repository, number)
                for number in sorted(pull_numbers)
                ],
                key=lambda pull: (pull.source_id, pull.number),
            )
        )
        checks = tuple(
            sorted(
                await self._source.list_checks_for_ref(
                    request.repository, candidate_sha
                ),
                key=lambda check: (check.run_id, check.source_id),
            )
        )
        if len(checks) > MAX_CANDIDATE_CHECKS or any(
            check.head_sha != candidate_sha for check in checks
        ):
            raise GitHubPartialData()
        comparisons = tuple(
            sorted(
                [
                PullRequestComparison(
                    pull_request_number=pull.number,
                    comparison=await self._source.compare_commits(
                        request.repository, pull.merge_commit_sha, candidate_sha
                    ),
                )
                for pull in pulls
                if pull.merge_commit_sha is not None
                ],
                key=lambda comparison: comparison.pull_request_number,
            )
        )
        if any(
            comparison.comparison.head_sha != candidate_sha
            for comparison in comparisons
        ):
            raise GitHubPartialData()

        return _EvidenceWindow(
            milestone=milestone,
            items=items,
            candidate_sha=candidate_sha,
            links=tuple(
                sorted(
                    links_by_key.values(),
                    key=lambda link: (
                        link.issue_number,
                        link.pull_request_number,
                        link.url,
                        link.source_id,
                    ),
                )
            ),
            pull_requests=pulls,
            checks=checks,
            comparisons=comparisons,
        )

    def _snapshot(
        self,
        request: AnalysisRequest,
        fetch_started_at: datetime,
        window: _EvidenceWindow,
    ) -> ReleaseSnapshot:
        issue_item = next(
            (item for item in window.items if item.kind is GitHubItemKind.ISSUE), None
        )
        linked = tuple(
            str(link.pull_request_number)
            for link in window.links
            if issue_item is not None and link.issue_number == issue_item.number
        )
        snapshot = ReleaseSnapshot(
            release_name=f"Milestone {request.milestone_number}",
            issue_number=str(issue_item.number) if issue_item is not None else "",
            milestone_number=request.milestone_number,
            issue_labels=issue_item.labels if issue_item is not None else (),
            linked_pr_numbers=linked,
            issue_evidence=self._issue_evidence(issue_item),
            snapshot_version=SnapshotVersion.GITHUB_V1,
            repository_id=request.repository_id,
            repository_full_name=(
                f"{request.repository.owner}/{request.repository.name}"
            ),
            fetch_started_at=fetch_started_at,
            fetched_at=self._now(),
            complete=True,
            source_errors=(),
            candidate_ref=request.candidate_ref,
            candidate_sha=window.candidate_sha,
            items=window.items,
            links=window.links,
            pull_requests=window.pull_requests,
            checks=window.checks,
            comparisons=window.comparisons,
        )
        return snapshot

    async def _get_milestone(self, request: AnalysisRequest) -> GitHubMilestone:
        try:
            return await self._source.get_milestone(
                request.repository, request.milestone_number
            )
        except GitHubNotFound:
            raise MissingMilestone() from None

    async def _resolve_candidate(self, request: AnalysisRequest) -> str:
        try:
            return await self._source.resolve_ref(
                request.repository, request.candidate_ref
            )
        except GitHubNotFound:
            raise MissingCandidateRef() from None

    def _incomplete(
        self,
        request: AnalysisRequest,
        fetch_started_at: datetime,
        error: SourceError,
    ) -> ReleaseSnapshot:
        return _unavailable_snapshot(request, fetch_started_at, self._now(), error)

    def _now(self) -> datetime:
        timestamp = self._clock()
        if timestamp.tzinfo is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return timestamp.astimezone(UTC)

    @staticmethod
    def _issue_evidence(item: GitHubItem | None) -> EvidenceRef:
        if item is None:
            return EvidenceRef(
                evidence_id="github-milestone-empty",
                source_type="github_milestone",
                source_id="empty",
                url="https://github.com",
                fingerprint="github:milestone:empty",
            )
        return EvidenceRef(
            evidence_id=f"github-issue-{item.source_id}",
            source_type="github_issue",
            source_id=str(item.number),
            url=item.url,
            fingerprint=f"github:issue:{item.source_id}:{item.updated_at.isoformat()}",
        )


def _unavailable_snapshot(
    request: AnalysisRequest,
    fetch_started_at: datetime,
    fetched_at: datetime,
    error: SourceError,
) -> ReleaseSnapshot:
    return ReleaseSnapshot(
        release_name=f"Milestone {request.milestone_number}",
        issue_number="",
        milestone_number=request.milestone_number,
        issue_labels=(),
        linked_pr_numbers=(),
        issue_evidence=EvidenceRef(
            evidence_id="github-release-unavailable",
            source_type="github_release",
            source_id=str(request.milestone_number),
            url=(
                f"https://github.com/{request.repository.owner}/"
                f"{request.repository.name}/milestone/{request.milestone_number}"
            ),
            fingerprint=f"github:milestone:{request.milestone_number}:unavailable",
        ),
        snapshot_version=SnapshotVersion.GITHUB_V1,
        repository_id=request.repository_id,
        repository_full_name=(
            f"{request.repository.owner}/{request.repository.name}"
        ),
        fetch_started_at=fetch_started_at,
        fetched_at=fetched_at,
        complete=False,
        source_errors=(error,),
        candidate_ref=request.candidate_ref,
        candidate_sha="",
    )


def assess_fixture_release() -> ReadinessAssessment:
    return assess_release(load_demo_release())


def assess(
    snapshot: ReleaseSnapshot,
    policy: object,
    decisions: tuple[object, ...],
    *,
    now: datetime,
) -> ReadinessAssessment:
    del policy, decisions
    if snapshot.snapshot_version is SnapshotVersion.LEGACY:
        trusted_legacy_fixture = (
            snapshot.repository_id == "fixture:demo"
            and snapshot.repository_full_name == "example/release-demo"
            and snapshot.complete
            and not snapshot.source_errors
            and snapshot.fetch_started_at is None
            and snapshot.fetched_at is None
            and not snapshot.candidate_ref
            and not snapshot.candidate_sha
            and not snapshot.items
            and not snapshot.links
            and not snapshot.pull_requests
            and not snapshot.checks
            and not snapshot.comparisons
        )
        if trusted_legacy_fixture:
            return assess_release(snapshot)
        return ReadinessAssessment(
            status=ReleaseStatus.INSUFFICIENT_DATA, findings=()
        )
    if snapshot.snapshot_version is not SnapshotVersion.GITHUB_V1:
        return ReadinessAssessment(
            status=ReleaseStatus.INSUFFICIENT_DATA, findings=()
        )
    if now.tzinfo is None:
        return ReadinessAssessment(
            status=ReleaseStatus.INSUFFICIENT_DATA, findings=()
        )
    if (
        not snapshot.complete
        or snapshot.source_errors
        or snapshot.fetch_started_at is None
        or snapshot.fetched_at is None
        or snapshot.fetch_started_at.tzinfo is None
        or snapshot.fetched_at.tzinfo is None
        or not snapshot.candidate_ref
        or not snapshot.candidate_sha
        or snapshot.milestone_number <= 0
        or not snapshot.repository_id
        or not snapshot.repository_full_name
    ):
        return ReadinessAssessment(
            status=ReleaseStatus.INSUFFICIENT_DATA, findings=()
        )
    started_at = snapshot.fetch_started_at.astimezone(UTC)
    fetched_at = snapshot.fetched_at.astimezone(UTC)
    effective_now = now.astimezone(UTC)
    if (
        started_at > fetched_at
        or fetched_at > effective_now
        or effective_now - fetched_at > MAX_SNAPSHOT_AGE
    ):
        return ReadinessAssessment(
            status=ReleaseStatus.INSUFFICIENT_DATA, findings=()
        )
    return assess_release(snapshot)
