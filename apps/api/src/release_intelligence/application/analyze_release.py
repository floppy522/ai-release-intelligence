from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from release_intelligence.adapters.fixtures.github_source import load_demo_release
from release_intelligence.domain.assessment import assess as _assess
from release_intelligence.domain.assessment import assess_release
from release_intelligence.domain.models import (
    EvidenceRef,
    PullRequestComparison,
    ReadinessAssessment,
    ReleaseLink,
    ReleaseSnapshot,
    SnapshotVersion,
    SourceError,
)
from release_intelligence.domain.policy import ReleasePolicy
from release_intelligence.domain.rules.checks import CheckDecision
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
from release_intelligence.ports.policies import PolicyRepositoryPort
from release_intelligence.ports.repositories import (
    AnalysisRepositoryPort,
    StoredAnalysisRun,
)

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
    previous_milestone_number: int | None = None
    previous_release_branch: str | None = None


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
        policy_repository: PolicyRepositoryPort | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._loader_factory = loader_factory
        self._repository = repository
        self._policy_repository = policy_repository
        self._clock = clock or (lambda: datetime.now(UTC))

    async def run(self, request: AnalysisRequest, actor: str) -> UUID:
        if not actor:
            raise ValueError("actor is required")
        policy_record = (
            await self._policy_repository.get_latest(request.repository_id)
            if self._policy_repository is not None
            else None
        )
        policy = policy_record.policy if policy_record is not None else None
        configured_request = replace(
            request,
            previous_milestone_number=(
                policy.previous_milestone_number if policy is not None else None
            ),
            previous_release_branch=(
                policy.previous_release_branch if policy is not None else None
            ),
        )
        bootstrap_started_at = self._now()
        try:
            loader = await self._loader_factory(configured_request)
            snapshot = await loader.load(configured_request)
        except GitHubRateLimited as error:
            snapshot = _unavailable_snapshot(
                configured_request,
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
                configured_request,
                bootstrap_started_at,
                self._now(),
                SourceError(
                    code="github.partial_data",
                    message="GitHub returned incomplete release evidence",
                ),
            )
        now = self._now()
        policy_version = (
            f"configuration:{policy_record.version}"
            if policy_record is not None
            else "default-v1"
        )
        assessment = assess(snapshot, policy=policy, decisions=(), now=now)
        source_fetched_at = snapshot.fetched_at
        if source_fetched_at is None:
            raise ValueError("loaded snapshot requires fetched_at")
        return await self._repository.create_run(
            snapshot=snapshot,
            findings=assessment.findings,
            assessment=assessment,
            policy_version=policy_version,
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
    previous_milestone: GitHubMilestone | None
    previous_release_branch: str | None
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
            previous_milestone_number=None,
            previous_release_branch=None,
        )

    async def _collect_window(self, request: AnalysisRequest) -> _EvidenceWindow:
        milestone = await self._get_milestone(request)
        current_items = await self._source.list_milestone_items(
            request.repository, request.milestone_number
        )
        previous_milestone: GitHubMilestone | None = None
        previous_items: tuple[GitHubItem, ...] = ()
        previous_number = request.previous_milestone_number
        previous_branch = request.previous_release_branch
        if (previous_number is None) != (previous_branch is None):
            raise GitHubPartialData()
        if previous_number is not None:
            previous_milestone = await self._get_previous_milestone(
                request, previous_number
            )
            previous_items = await self._source.list_milestone_items(
                request.repository, previous_number
            )
        if len(current_items) + len(previous_items) > MAX_MILESTONE_ITEMS:
            raise GitHubPartialData()
        items = tuple(
            sorted(
                (*current_items, *previous_items),
                key=lambda item: (item.kind.value, item.source_id, item.number),
            )
        )
        candidate_sha = await self._resolve_candidate(request)

        links_by_key: dict[tuple[int, int, str], ReleaseLink] = {}
        event_count = 0
        current_item_numbers = {item.number for item in current_items}
        current_pull_numbers = {
            item.number
            for item in current_items
            if item.kind is GitHubItemKind.PULL_REQUEST
        }
        pull_numbers = {
            item.number for item in items if item.kind is GitHubItemKind.PULL_REQUEST
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
                if item.number in current_item_numbers:
                    current_pull_numbers.add(event.pull_request_number)
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
                    if pull.number in current_pull_numbers
                    and pull.merge_commit_sha is not None
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
            previous_milestone=previous_milestone,
            previous_release_branch=previous_branch,
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
            (
                item
                for item in window.items
                if item.kind is GitHubItemKind.ISSUE
                and item.milestone_number == request.milestone_number
            ),
            None,
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
            previous_milestone_number=(
                window.previous_milestone.number
                if window.previous_milestone is not None
                else None
            ),
            previous_release_branch=window.previous_release_branch,
        )
        return snapshot

    async def _get_milestone(self, request: AnalysisRequest) -> GitHubMilestone:
        try:
            return await self._source.get_milestone(
                request.repository, request.milestone_number
            )
        except GitHubNotFound:
            raise MissingMilestone() from None

    async def _get_previous_milestone(
        self, request: AnalysisRequest, milestone_number: int
    ) -> GitHubMilestone:
        try:
            return await self._source.get_milestone(
                request.repository, milestone_number
            )
        except GitHubNotFound:
            raise GitHubPartialData() from None

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
        repository_full_name=(f"{request.repository.owner}/{request.repository.name}"),
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
    policy: ReleasePolicy | None,
    decisions: Iterable[CheckDecision],
    *,
    now: datetime,
) -> ReadinessAssessment:
    """Compatibility boundary for existing application and route callers."""

    return _assess(snapshot, policy, decisions, now=now)
