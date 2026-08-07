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
    SourceError,
)
from release_intelligence.ports.github import (
    GitHubError,
    GitHubItem,
    GitHubItemKind,
    GitHubMilestone,
    GitHubNotFound,
    GitHubPartialData,
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
        loader = await self._loader_factory(request)
        snapshot = await loader.load(request)
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
        latest: ReleaseSnapshot | None = None
        for _attempt in range(2):
            try:
                latest, consistent = await self._load_once(request, fetch_started_at)
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
            if consistent:
                return latest
        assert latest is not None
        return replace(
            latest,
            complete=False,
            source_errors=(
                SourceError(
                    code="github.inconsistent_state",
                    message="GitHub release state changed during both fetch windows",
                ),
            ),
            fetched_at=self._now(),
        )

    async def _load_once(
        self, request: AnalysisRequest, fetch_started_at: datetime
    ) -> tuple[ReleaseSnapshot, bool]:
        milestone_before = await self._get_milestone(request)
        items_before = await self._source.list_milestone_items(
            request.repository, request.milestone_number
        )
        if len(items_before) > MAX_MILESTONE_ITEMS:
            raise GitHubPartialData()
        candidate_before = await self._resolve_candidate(request)

        links: list[ReleaseLink] = []
        pull_numbers = {
            item.number
            for item in items_before
            if item.kind is GitHubItemKind.PULL_REQUEST
        }
        for item in items_before:
            if item.kind is not GitHubItemKind.ISSUE:
                continue
            events = await self._source.list_issue_timeline(
                request.repository, item.number
            )
            for event in events:
                links.append(
                    ReleaseLink(
                        issue_number=item.number,
                        pull_request_number=event.pull_request_number,
                        url=event.pull_request_url,
                    )
                )
                pull_numbers.add(event.pull_request_number)
                if len(pull_numbers) > MAX_RELATED_PULL_REQUESTS:
                    raise GitHubPartialData()
        pulls = tuple(
            [
                await self._source.get_pull_request(request.repository, number)
                for number in sorted(pull_numbers)
            ]
        )
        checks = await self._source.list_checks_for_ref(
            request.repository, candidate_before
        )
        if len(checks) > MAX_CANDIDATE_CHECKS:
            raise GitHubPartialData()
        comparisons = tuple(
            [
                PullRequestComparison(
                    pull_request_number=pull.number,
                    comparison=await self._source.compare_commits(
                        request.repository, pull.merge_commit_sha, candidate_before
                    ),
                )
                for pull in pulls
                if pull.merge_commit_sha is not None
            ]
        )

        milestone_after = await self._get_milestone(request)
        items_after = await self._source.list_milestone_items(
            request.repository, request.milestone_number
        )
        candidate_after = await self._resolve_candidate(request)
        consistent = (
            milestone_before == milestone_after
            and items_before == items_after
            and candidate_before == candidate_after
            and all(check.head_sha == candidate_before for check in checks)
        )
        issue_item = next(
            (item for item in items_before if item.kind is GitHubItemKind.ISSUE), None
        )
        linked = tuple(
            str(link.pull_request_number)
            for link in links
            if issue_item is not None and link.issue_number == issue_item.number
        )
        snapshot = ReleaseSnapshot(
            release_name=f"Milestone {request.milestone_number}",
            issue_number=str(issue_item.number) if issue_item is not None else "",
            milestone_number=request.milestone_number,
            issue_labels=issue_item.labels if issue_item is not None else (),
            linked_pr_numbers=linked,
            issue_evidence=self._issue_evidence(issue_item),
            repository_id=request.repository_id,
            repository_full_name=(
                f"{request.repository.owner}/{request.repository.name}"
            ),
            fetch_started_at=fetch_started_at,
            fetched_at=self._now(),
            complete=consistent,
            source_errors=(),
            candidate_ref=request.candidate_ref,
            candidate_sha=candidate_before,
            items=items_before,
            links=tuple(links),
            pull_requests=pulls,
            checks=checks,
            comparisons=comparisons,
        )
        return snapshot, consistent

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
            repository_id=request.repository_id,
            repository_full_name=(
                f"{request.repository.owner}/{request.repository.name}"
            ),
            fetch_started_at=fetch_started_at,
            fetched_at=self._now(),
            complete=False,
            source_errors=(error,),
            candidate_ref=request.candidate_ref,
            candidate_sha="",
        )

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
    if not snapshot.complete:
        return ReadinessAssessment(
            status=ReleaseStatus.INSUFFICIENT_DATA, findings=()
        )
    if snapshot.fetched_at is not None:
        if snapshot.fetched_at.tzinfo is None or now.tzinfo is None:
            raise ValueError("snapshot and assessment times must be timezone-aware")
        if now.astimezone(UTC) - snapshot.fetched_at.astimezone(UTC) > MAX_SNAPSHOT_AGE:
            return ReadinessAssessment(
                status=ReleaseStatus.INSUFFICIENT_DATA, findings=()
            )
    return assess_release(snapshot)
