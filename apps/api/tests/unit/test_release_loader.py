from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta

import pytest

from release_intelligence.adapters.persistence.repositories import AnalysisRepository
from release_intelligence.application.analyze_release import (
    AnalysisRequest,
    GitHubReleaseLoader,
    MissingCandidateRef,
    MissingMilestone,
    assess,
)
from release_intelligence.domain.models import ReleaseStatus
from release_intelligence.ports.github import (
    CommitComparison,
    GitHubCheck,
    GitHubCommit,
    GitHubIssueTimelineEvent,
    GitHubItem,
    GitHubItemKind,
    GitHubMilestone,
    GitHubNotFound,
    GitHubPartialData,
    GitHubPullRequest,
    GitHubRateLimited,
    RepoRef,
)

NOW = datetime(2026, 8, 7, 14, 30, tzinfo=UTC)
REQUEST = AnalysisRequest(
    repository_id="987654",
    repository=RepoRef(owner="example", name="release-intelligence"),
    installation_id=123,
    milestone_number=7,
    candidate_ref="release/2026-08-10",
)


def milestone(*, updated_at: datetime = NOW) -> GitHubMilestone:
    return GitHubMilestone(
        source_id="700",
        number=7,
        url="https://github.com/example/release-intelligence/milestone/7",
        state="open",
        created_at=NOW - timedelta(days=20),
        updated_at=updated_at,
        due_on=NOW + timedelta(days=3),
    )


def issue(*, updated_at: datetime = NOW) -> GitHubItem:
    return GitHubItem(
        source_id="1420",
        number=142,
        kind=GitHubItemKind.ISSUE,
        url="https://github.com/example/release-intelligence/issues/142",
        state="open",
        labels=("code-change",),
        assignees=("octocat",),
        milestone_number=7,
        created_at=NOW - timedelta(days=5),
        updated_at=updated_at,
    )


def pull_request() -> GitHubPullRequest:
    return GitHubPullRequest(
        source_id="1430",
        number=143,
        url="https://github.com/example/release-intelligence/pull/143",
        state="closed",
        labels=(),
        assignees=("octocat",),
        milestone_number=7,
        head_ref="feature/142",
        head_sha="feature-sha",
        base_ref="main",
        base_sha="main-sha",
        merge_commit_sha="merge-sha",
        merged_at=NOW - timedelta(days=1),
        created_at=NOW - timedelta(days=4),
        updated_at=NOW - timedelta(days=1),
    )


def timeline_event() -> GitHubIssueTimelineEvent:
    return GitHubIssueTimelineEvent(
        source_id="900",
        source_repository=REQUEST.repository,
        pull_request_number=143,
        pull_request_url="https://github.com/example/release-intelligence/pull/143",
        created_at=NOW - timedelta(days=2),
    )


def check() -> GitHubCheck:
    return GitHubCheck(
        source_id="1",
        run_id=1,
        name="test",
        url="https://github.com/example/release-intelligence/actions/runs/1",
        head_sha="candidate-sha",
        status="completed",
        conclusion="success",
        started_at=NOW - timedelta(minutes=4),
        completed_at=NOW - timedelta(minutes=2),
    )


def comparison() -> CommitComparison:
    commit = GitHubCommit(
        sha="merge-sha",
        url="https://github.com/example/release-intelligence/commit/merge-sha",
        committed_at=NOW - timedelta(days=1),
    )
    return CommitComparison(
        status="ahead",
        ahead_by=1,
        behind_by=0,
        total_commits=1,
        url="https://github.com/example/release-intelligence/compare/merge-sha...candidate-sha",
        base_sha="merge-sha",
        merge_base_sha="merge-sha",
        commits=(commit,),
    )


class FakeSource:
    def __init__(self) -> None:
        self.calls: defaultdict[str, int] = defaultdict(int)
        self.fail_at: str | None = None
        self.milestones = [milestone(), milestone(), milestone(), milestone()]
        self.item_sets = [(issue(),), (issue(),), (issue(),), (issue(),)]
        self.refs = ["candidate-sha"] * 4

    def _take(self, name: str) -> None:
        self.calls[name] += 1
        if self.fail_at == name:
            raise GitHubPartialData()

    async def get_milestone(self, repo: RepoRef, number: int) -> GitHubMilestone:
        del repo, number
        self._take("milestone")
        return self.milestones.pop(0)

    async def list_milestone_items(
        self, repo: RepoRef, number: int
    ) -> tuple[GitHubItem, ...]:
        del repo, number
        self._take("items")
        return self.item_sets.pop(0)

    async def list_issue_timeline(
        self, repo: RepoRef, issue_number: int
    ) -> tuple[GitHubIssueTimelineEvent, ...]:
        del repo, issue_number
        self._take("timeline")
        return (timeline_event(),)

    async def get_pull_request(
        self, repo: RepoRef, pull_number: int
    ) -> GitHubPullRequest:
        del repo, pull_number
        self._take("pull")
        return pull_request()

    async def resolve_ref(self, repo: RepoRef, ref: str) -> str:
        del repo, ref
        self._take("ref")
        return self.refs.pop(0)

    async def list_checks_for_ref(
        self, repo: RepoRef, ref: str
    ) -> tuple[GitHubCheck, ...]:
        del repo, ref
        self._take("checks")
        return (check(),)

    async def compare_commits(
        self, repo: RepoRef, base: str, head: str
    ) -> CommitComparison:
        del repo, base, head
        self._take("comparison")
        return comparison()


async def test_complete_loader_captures_normalized_evidence_window() -> None:
    source = FakeSource()

    snapshot = await GitHubReleaseLoader(source, clock=lambda: NOW).load(REQUEST)

    assert snapshot.complete is True
    assert snapshot.fetch_started_at == NOW
    assert snapshot.fetched_at == NOW
    assert snapshot.source_errors == ()
    assert snapshot.milestone_number == 7
    assert snapshot.candidate_ref == "release/2026-08-10"
    assert snapshot.candidate_sha == "candidate-sha"
    assert snapshot.items == (issue(),)
    assert snapshot.links[0].issue_number == 142
    assert snapshot.pull_requests == (pull_request(),)
    assert snapshot.checks == (check(),)
    assert snapshot.comparisons[0].pull_request_number == 143

    payload = AnalysisRepository._snapshot_payload(snapshot)
    assert AnalysisRepository._snapshot_from_payload(payload) == snapshot


async def test_partial_github_fetch_cannot_produce_ready() -> None:
    source = FakeSource()
    source.fail_at = "checks"
    snapshot = await GitHubReleaseLoader(source, clock=lambda: NOW).load(REQUEST)

    assessment = assess(snapshot, policy=None, decisions=(), now=NOW)

    assert snapshot.complete is False
    assert snapshot.source_errors[0].code == "github.partial_data"
    assert assessment.status is ReleaseStatus.INSUFFICIENT_DATA


async def test_rate_limit_reset_is_preserved_as_source_metadata() -> None:
    source = FakeSource()
    reset_at = NOW + timedelta(minutes=30)
    original_take = source._take

    def rate_limit_checks(name: str) -> None:
        original_take(name)
        if name == "checks":
            raise GitHubRateLimited(reset_at)

    source._take = rate_limit_checks  # type: ignore[method-assign]

    snapshot = await GitHubReleaseLoader(source, clock=lambda: NOW).load(REQUEST)

    assert snapshot.complete is False
    assert snapshot.source_errors[0].code == "github.rate_limited"
    assert snapshot.source_errors[0].reset_at == reset_at


async def test_loader_reconciles_one_changed_window_then_succeeds() -> None:
    source = FakeSource()
    source.refs = ["old-sha", "new-sha", "candidate-sha", "candidate-sha"]

    snapshot = await GitHubReleaseLoader(source, clock=lambda: NOW).load(REQUEST)

    assert snapshot.complete is True
    assert snapshot.candidate_sha == "candidate-sha"
    assert source.calls["checks"] == 2


async def test_loader_fails_closed_after_second_inconsistent_window() -> None:
    source = FakeSource()
    source.refs = ["a", "b", "c", "d"]

    snapshot = await GitHubReleaseLoader(source, clock=lambda: NOW).load(REQUEST)

    assert snapshot.complete is False
    assert snapshot.source_errors[0].code == "github.inconsistent_state"
    assert source.calls["checks"] == 2


async def test_loader_fails_closed_before_expanding_oversized_milestone() -> None:
    source = FakeSource()
    source.item_sets[0] = tuple(issue() for _ in range(101))

    snapshot = await GitHubReleaseLoader(source, clock=lambda: NOW).load(REQUEST)

    assert snapshot.complete is False
    assert snapshot.source_errors[0].code == "github.partial_data"
    assert source.calls["timeline"] == 0


async def test_snapshot_older_than_ten_minutes_is_insufficient() -> None:
    source = FakeSource()
    snapshot = await GitHubReleaseLoader(source, clock=lambda: NOW).load(REQUEST)

    assessment = assess(
        snapshot,
        policy=None,
        decisions=(),
        now=snapshot.fetched_at + timedelta(minutes=11),
    )

    assert assessment.status is ReleaseStatus.INSUFFICIENT_DATA


@pytest.mark.parametrize(
    ("operation", "expected"),
    [("milestone", MissingMilestone), ("ref", MissingCandidateRef)],
)
async def test_missing_release_identity_is_typed(
    operation: str, expected: type[Exception]
) -> None:
    source = FakeSource()
    source.fail_at = None
    original_take = source._take

    def fail_not_found(name: str) -> None:
        original_take(name)
        if name == operation:
            raise GitHubNotFound()

    source._take = fail_not_found  # type: ignore[method-assign]

    with pytest.raises(expected):
        await GitHubReleaseLoader(source, clock=lambda: NOW).load(REQUEST)


async def test_milestone_disappearing_during_window_is_still_typed_missing() -> None:
    source = FakeSource()
    original_take = source._take

    def disappear_on_validation(name: str) -> None:
        original_take(name)
        if name == "milestone" and source.calls[name] == 2:
            raise GitHubNotFound()

    source._take = disappear_on_validation  # type: ignore[method-assign]

    with pytest.raises(MissingMilestone):
        await GitHubReleaseLoader(source, clock=lambda: NOW).load(REQUEST)
