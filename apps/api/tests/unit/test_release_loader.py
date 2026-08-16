from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from release_intelligence.adapters.fixtures.github_source import load_demo_release
from release_intelligence.adapters.persistence.repositories import AnalysisRepository
from release_intelligence.application.analyze_release import (
    AnalysisRequest,
    GitHubReleaseLoader,
    MissingCandidateRef,
    MissingMilestone,
)
from release_intelligence.domain.assessment import assess, refresh_snapshot_freshness
from release_intelligence.domain.models import (
    PullRequestComparison,
    ReadinessAssessment,
    ReleaseStatus,
    SourceError,
)
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
from release_intelligence.ports.repositories import IncompatibleSnapshotError

NOW = datetime(2026, 8, 7, 14, 30, tzinfo=UTC)
STORED_READY = ReadinessAssessment(status=ReleaseStatus.READY, findings=())
REQUEST = AnalysisRequest(
    repository_id="987654",
    repository=RepoRef(owner="example", name="release-intelligence"),
    installation_id=123,
    milestone_number=7,
    candidate_ref="release/2026-08-10",
)
PREVIOUS_REQUEST = replace(
    REQUEST,
    previous_milestone_number=6,
    previous_release_branch="release/2026-08-03",
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
        url="https://github.com/example/release-intelligence/runs/1",
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
        head_sha="candidate-sha",
    )


class FakeSource:
    def __init__(self) -> None:
        self.calls: defaultdict[str, int] = defaultdict(int)
        self.fail_at: str | None = None
        self.milestones = [milestone(), milestone(), milestone(), milestone()]
        self.item_sets = [(issue(),), (issue(),), (issue(),), (issue(),)]
        self.refs = ["candidate-sha"] * 4
        self.timelines = [(timeline_event(),)] * 4
        self.pulls = [pull_request()] * 4
        self.check_sets = [(check(),)] * 4
        self.comparison_sets = [comparison()] * 4

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
        return self.timelines[min(self.calls["timeline"] - 1, len(self.timelines) - 1)]

    async def get_pull_request(
        self, repo: RepoRef, pull_number: int
    ) -> GitHubPullRequest:
        del repo, pull_number
        self._take("pull")
        return self.pulls[min(self.calls["pull"] - 1, len(self.pulls) - 1)]

    async def resolve_ref(self, repo: RepoRef, ref: str) -> str:
        del repo, ref
        self._take("ref")
        return self.refs.pop(0)

    async def list_checks_for_ref(
        self, repo: RepoRef, ref: str
    ) -> tuple[GitHubCheck, ...]:
        del repo, ref
        self._take("checks")
        return self.check_sets[min(self.calls["checks"] - 1, len(self.check_sets) - 1)]

    async def compare_commits(
        self, repo: RepoRef, base: str, head: str
    ) -> CommitComparison:
        del repo, base, head
        self._take("comparison")
        return self.comparison_sets[
            min(self.calls["comparison"] - 1, len(self.comparison_sets) - 1)
        ]


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
    assert snapshot.comparisons[0].comparison.head_sha == snapshot.candidate_sha

    payload = AnalysisRepository._snapshot_payload(snapshot)
    assert AnalysisRepository._snapshot_from_payload(payload) == snapshot


async def test_loader_collects_configured_previous_milestone_in_both_windows() -> None:
    source = FakeSource()
    previous = replace(
        milestone(),
        source_id="600",
        number=6,
        url="https://github.com/example/release-intelligence/milestone/6",
    )
    source.milestones = [milestone(), previous, milestone(), previous]
    source.item_sets = [(issue(),), (), (issue(),), ()]

    loaded = await GitHubReleaseLoader(source, clock=lambda: NOW).load(PREVIOUS_REQUEST)

    assert loaded.complete is True
    assert loaded.previous_milestone_number == 6
    assert loaded.previous_release_branch == "release/2026-08-03"
    assert source.calls["milestone"] == 4
    assert source.calls["items"] == 4
    assert source.calls["checks"] == 2


async def test_partial_previous_release_collection_clears_context_markers() -> None:
    class PartialPreviousSource(FakeSource):
        async def list_milestone_items(
            self, repo: RepoRef, number: int
        ) -> tuple[GitHubItem, ...]:
            if number == 6:
                raise GitHubPartialData()
            return await super().list_milestone_items(repo, number)

    loaded = await GitHubReleaseLoader(PartialPreviousSource(), clock=lambda: NOW).load(
        PREVIOUS_REQUEST
    )

    assert loaded.complete is False
    assert loaded.source_errors[0].code == "github.partial_data"
    assert loaded.previous_milestone_number is None
    assert loaded.previous_release_branch is None


async def test_partial_github_fetch_cannot_produce_ready() -> None:
    source = FakeSource()
    source.fail_at = "checks"
    snapshot = await GitHubReleaseLoader(source, clock=lambda: NOW).load(REQUEST)

    assessment = refresh_snapshot_freshness(STORED_READY, snapshot, now=NOW)

    assert snapshot.complete is False
    assert snapshot.source_errors[0].code == "github.partial_data"
    assert assessment.status is ReleaseStatus.INSUFFICIENT_DATA


async def test_comparison_head_must_match_resolved_candidate() -> None:
    source = FakeSource()
    mismatched = replace(comparison(), head_sha="other-candidate")
    source.comparison_sets = [mismatched] * 4

    snapshot = await GitHubReleaseLoader(source, clock=lambda: NOW).load(REQUEST)

    assert snapshot.complete is False
    assert snapshot.source_errors[0].code == "github.partial_data"


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
    source.check_sets = [(replace(check(), head_sha=sha),) for sha in source.refs]
    source.comparison_sets = [
        replace(comparison(), head_sha=sha) for sha in source.refs
    ]

    snapshot = await GitHubReleaseLoader(source, clock=lambda: NOW).load(REQUEST)

    assert snapshot.complete is True
    assert snapshot.candidate_sha == "candidate-sha"
    assert source.calls["checks"] == 4


async def test_loader_fails_closed_after_second_inconsistent_window() -> None:
    source = FakeSource()
    source.refs = ["a", "b", "c", "d"]
    source.check_sets = [(replace(check(), head_sha=sha),) for sha in source.refs]
    source.comparison_sets = [
        replace(comparison(), head_sha=sha) for sha in source.refs
    ]

    snapshot = await GitHubReleaseLoader(source, clock=lambda: NOW).load(REQUEST)

    assert snapshot.complete is False
    assert snapshot.source_errors[0].code == "github.inconsistent_state"
    assert source.calls["checks"] == 4


@pytest.mark.parametrize(
    "component",
    ["milestone", "items", "timeline", "pull", "checks", "comparison"],
)
async def test_loader_fails_closed_when_any_material_evidence_keeps_changing(
    component: str,
) -> None:
    source = FakeSource()
    if component == "milestone":
        changed = milestone(updated_at=NOW + timedelta(seconds=1))
        source.milestones = [milestone(), changed, milestone(), changed]
    elif component == "items":
        changed = issue(updated_at=NOW + timedelta(seconds=1))
        source.item_sets = [(issue(),), (changed,), (issue(),), (changed,)]
    elif component == "timeline":
        changed = replace(
            timeline_event(), created_at=NOW - timedelta(days=2, seconds=1)
        )
        source.timelines = [
            (timeline_event(),),
            (changed,),
            (timeline_event(),),
            (changed,),
        ]
    elif component == "pull":
        changed = replace(pull_request(), updated_at=NOW)
        source.pulls = [pull_request(), changed, pull_request(), changed]
    elif component == "checks":
        changed = replace(check(), conclusion="failure")
        source.check_sets = [(check(),), (changed,), (check(),), (changed,)]
    else:
        changed = replace(comparison(), status="diverged")
        source.comparison_sets = [comparison(), changed, comparison(), changed]

    loaded = await GitHubReleaseLoader(source, clock=lambda: NOW).load(REQUEST)

    assert loaded.complete is False
    assert loaded.source_errors[0].code == "github.inconsistent_state"


async def test_loader_retries_whole_material_window_then_uses_stable_evidence() -> None:
    source = FakeSource()
    failed = replace(check(), conclusion="failure")
    source.check_sets = [(check(),), (failed,), (check(),), (check(),)]

    loaded = await GitHubReleaseLoader(source, clock=lambda: NOW).load(REQUEST)

    assert loaded.complete is True
    assert loaded.checks[0].conclusion == "success"
    assert source.calls["checks"] == 4


async def test_reordered_identical_set_like_evidence_is_one_stable_window() -> None:
    source = FakeSource()
    second = replace(
        check(),
        source_id="2",
        run_id=2,
        name="security",
        url="https://github.com/example/release-intelligence/runs/2",
    )
    forward = (check(), second)
    reverse = tuple(reversed(forward))
    source.check_sets = [forward, reverse, forward, reverse]

    loaded = await GitHubReleaseLoader(source, clock=lambda: NOW).load(REQUEST)

    assert loaded.complete is True
    assert [item.run_id for item in loaded.checks] == [1, 2]
    assert source.calls["checks"] == 2


async def test_reversed_semantic_duplicate_links_choose_same_representative() -> None:
    source = FakeSource()
    later = replace(
        timeline_event(), source_id="999", created_at=NOW - timedelta(days=1)
    )
    duplicates = (timeline_event(), later)
    source.timelines = [duplicates, tuple(reversed(duplicates))] * 2

    loaded = await GitHubReleaseLoader(source, clock=lambda: NOW).load(REQUEST)

    assert loaded.complete is True
    assert len(loaded.links) == 1
    assert loaded.links[0].source_id == "900"
    assert source.calls["timeline"] == 2


async def test_loader_fails_closed_before_expanding_oversized_milestone() -> None:
    source = FakeSource()
    source.item_sets[0] = tuple(issue() for _ in range(101))

    snapshot = await GitHubReleaseLoader(source, clock=lambda: NOW).load(REQUEST)

    assert snapshot.complete is False
    assert snapshot.source_errors[0].code == "github.partial_data"
    assert source.calls["timeline"] == 0


async def test_loader_deduplicates_semantically_identical_timeline_links() -> None:
    source = FakeSource()
    duplicates = (timeline_event(), replace(timeline_event(), source_id="901"))
    source.timelines = [duplicates] * 4

    loaded = await GitHubReleaseLoader(source, clock=lambda: NOW).load(REQUEST)

    assert loaded.complete is True
    assert len(loaded.links) == 1


async def test_loader_fails_closed_at_timeline_fanout_cap() -> None:
    source = FakeSource()
    fanout = tuple(
        replace(
            timeline_event(),
            source_id=str(900 + number),
            pull_request_number=1000 + number,
            pull_request_url=(
                f"https://github.com/example/release-intelligence/pull/{1000 + number}"
            ),
        )
        for number in range(201)
    )
    source.timelines = [fanout] * 4

    loaded = await GitHubReleaseLoader(source, clock=lambda: NOW).load(REQUEST)

    assert loaded.complete is False
    assert loaded.source_errors[0].code == "github.partial_data"
    assert source.calls["pull"] == 0


async def test_snapshot_older_than_ten_minutes_is_insufficient() -> None:
    source = FakeSource()
    snapshot = await GitHubReleaseLoader(source, clock=lambda: NOW).load(REQUEST)

    assessment = refresh_snapshot_freshness(
        STORED_READY,
        snapshot,
        now=snapshot.fetched_at + timedelta(minutes=11),
    )

    assert assessment.status is ReleaseStatus.INSUFFICIENT_DATA


@pytest.mark.parametrize(
    "corrupt",
    [
        lambda value: replace(value, fetched_at=None),
        lambda value: replace(value, fetched_at=NOW + timedelta(minutes=1)),
        lambda value: replace(
            value,
            fetch_started_at=NOW + timedelta(seconds=1),
            fetched_at=NOW,
        ),
        lambda value: replace(
            value,
            source_errors=(SourceError(code="github.partial_data", message="partial"),),
        ),
        lambda value: replace(value, fetched_at=NOW.replace(tzinfo=None)),
        lambda value: replace(value, candidate_ref=""),
        lambda value: replace(value, candidate_sha=""),
    ],
    ids=[
        "missing-fetched-at",
        "future",
        "inverted-window",
        "errors",
        "naive",
        "missing-candidate-ref",
        "missing-candidate-sha",
    ],
)
async def test_normalized_snapshot_metadata_contradictions_fail_closed(corrupt) -> None:
    complete = await GitHubReleaseLoader(FakeSource(), clock=lambda: NOW).load(REQUEST)

    assessment = refresh_snapshot_freshness(STORED_READY, corrupt(complete), now=NOW)

    assert assessment.status is ReleaseStatus.INSUFFICIENT_DATA


def test_unknown_future_snapshot_version_is_rejected_at_deserialization() -> None:
    payload = AnalysisRepository._snapshot_payload(load_demo_release())
    payload["snapshot_version"] = "github-v2"

    with pytest.raises(ValidationError):
        AnalysisRepository._snapshot_from_payload(payload)


def test_persisted_comparison_without_head_sha_uses_legacy_default() -> None:
    release = replace(
        load_demo_release(),
        comparisons=(
            PullRequestComparison(
                pull_request_number=143,
                comparison=comparison(),
            ),
        ),
    )
    payload = AnalysisRepository._snapshot_payload(release)
    comparisons = payload["comparisons"]
    assert isinstance(comparisons, list)
    comparison_payload = comparisons[0]
    assert isinstance(comparison_payload, dict)
    comparison_facts = comparison_payload["comparison"]
    assert isinstance(comparison_facts, dict)
    comparison_facts.pop("head_sha")

    restored = AnalysisRepository._snapshot_from_payload(payload)

    assert restored.comparisons[0].comparison.head_sha == ""


def test_repository_wraps_unknown_snapshot_version_with_trusted_identity() -> None:
    payload = AnalysisRepository._snapshot_payload(load_demo_release())
    payload["snapshot_version"] = "github-v2"

    with pytest.raises(IncompatibleSnapshotError) as raised:
        AnalysisRepository._decode_snapshot(payload, repository_id="trusted-repo-id")

    assert raised.value.repository_id == "trusted-repo-id"
    assert "github-v2" not in str(raised.value)


@pytest.mark.parametrize(
    "forged",
    [
        replace(load_demo_release(), repository_id="attacker/repository"),
        replace(
            load_demo_release(),
            source_errors=(SourceError(code="github.partial_data", message="partial"),),
        ),
        replace(load_demo_release(), fetched_at=NOW, candidate_sha="forged"),
    ],
)
def test_legacy_exemption_is_limited_to_the_trusted_fixture_boundary(forged) -> None:
    result = assess(forged, policy=None, decisions=(), now=NOW)  # type: ignore[arg-type]

    assert result.status is ReleaseStatus.INSUFFICIENT_DATA


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
