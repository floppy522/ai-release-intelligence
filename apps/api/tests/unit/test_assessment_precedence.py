from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from release_intelligence.domain.assessment import (
    MAX_SNAPSHOT_AGE,
    assess,
    refresh_snapshot_freshness,
)
from release_intelligence.domain.models import (
    EvidenceRef,
    ReleaseSnapshot,
    ReleaseStatus,
    SnapshotVersion,
    SourceError,
)
from release_intelligence.domain.policy import CheckCategory, ReleasePolicy
from release_intelligence.domain.rules.checks import CheckFingerprint
from release_intelligence.ports.github import GitHubCheck, GitHubItem, GitHubItemKind

NOW = datetime(2026, 8, 7, 14, 30, tzinfo=UTC)
CANDIDATE_SHA = "a" * 40
STATUS_RANK = {
    ReleaseStatus.INSUFFICIENT_DATA: 0,
    ReleaseStatus.NOT_READY: 1,
    ReleaseStatus.NEEDS_DECISION: 2,
    ReleaseStatus.READY: 3,
}


@dataclass(frozen=True, slots=True)
class Decision:
    fingerprint: str
    blocks_release: bool


def policy() -> ReleasePolicy:
    return ReleasePolicy(
        main_branch="main",
        candidate_branch="release/2026-08-10",
        milestone_number=7,
        code_change_label="code-change",
        release_ops_label="release-ops",
        blocker_label="release-blocker",
        check_categories={
            "blocking": CheckCategory.BLOCKING,
            "advisory": CheckCategory.ADVISORY,
        },
    )


def check(name: str, *, successful: bool) -> GitHubCheck:
    run_id = 101 if name == "blocking" else 202
    return GitHubCheck(
        source_id=str(run_id),
        run_id=run_id,
        name=name,
        url=f"https://github.com/acme/widgets/runs/{run_id}",
        head_sha=CANDIDATE_SHA,
        status="completed",
        conclusion="success" if successful else "failure",
        started_at=NOW - timedelta(minutes=2),
        completed_at=NOW - timedelta(minutes=1),
    )


def snapshot(
    *,
    complete: bool = True,
    has_blocker: bool = False,
    needs_decision: bool = False,
    checks: tuple[GitHubCheck, ...] | None = None,
) -> ReleaseSnapshot:
    return ReleaseSnapshot(
        release_name="Release 2026.08.10",
        issue_number="",
        milestone_number=7,
        issue_labels=(),
        linked_pr_numbers=(),
        issue_evidence=EvidenceRef(
            evidence_id="github-milestone-7",
            source_type="github_milestone",
            source_id="7",
            url="https://github.com/acme/widgets/milestone/7",
            fingerprint="sha256:" + "7" * 64,
        ),
        snapshot_version=SnapshotVersion.GITHUB_V1,
        repository_id="77",
        repository_full_name="acme/widgets",
        fetch_started_at=NOW - timedelta(minutes=3),
        fetched_at=NOW,
        complete=complete,
        candidate_ref="release/2026-08-10",
        candidate_sha=CANDIDATE_SHA,
        checks=checks
        if checks is not None
        else (
            check("blocking", successful=not has_blocker),
            check("advisory", successful=not needs_decision),
        ),
    )


@pytest.mark.parametrize(
    ("complete", "has_blocker", "needs_decision", "expected"),
    [
        (False, True, True, ReleaseStatus.INSUFFICIENT_DATA),
        (True, True, True, ReleaseStatus.NOT_READY),
        (True, False, True, ReleaseStatus.NEEDS_DECISION),
        (True, False, False, ReleaseStatus.READY),
    ],
)
def test_status_precedence(
    complete: bool,
    has_blocker: bool,
    needs_decision: bool,
    expected: ReleaseStatus,
) -> None:
    result = assess(
        snapshot(
            complete=complete,
            has_blocker=has_blocker,
            needs_decision=needs_decision,
        ),
        policy(),
        (),
        now=NOW,
    )

    assert result.status is expected


@settings(derandomize=True, max_examples=32)
@given(
    complete=st.booleans(),
    has_blocker=st.booleans(),
    needs_decision=st.booleans(),
)
def test_adding_a_blocker_never_improves_status(
    complete: bool, has_blocker: bool, needs_decision: bool
) -> None:
    before = assess(
        snapshot(
            complete=complete,
            has_blocker=has_blocker,
            needs_decision=needs_decision,
        ),
        policy(),
        (),
        now=NOW,
    )
    after = assess(
        snapshot(
            complete=complete,
            has_blocker=True,
            needs_decision=needs_decision,
        ),
        policy(),
        (),
        now=NOW,
    )

    assert STATUS_RANK[after.status] <= STATUS_RANK[before.status]


@settings(derandomize=True, max_examples=16)
@given(order=st.permutations(("blocking", "advisory")))
def test_repeated_and_permuted_evaluation_is_deterministic(
    order: list[str],
) -> None:
    check_by_name = {
        "blocking": check("blocking", successful=False),
        "advisory": check("advisory", successful=False),
    }
    candidate = snapshot(checks=tuple(check_by_name[name] for name in order))

    first = assess(candidate, policy(), (), now=NOW)
    second = assess(candidate, policy(), (), now=NOW)
    canonical = assess(
        replace(candidate, checks=tuple(reversed(candidate.checks))),
        policy(),
        (),
        now=NOW,
    )

    assert first == second == canonical


@pytest.mark.parametrize(
    "fetched_at",
    [
        NOW - MAX_SNAPSHOT_AGE - timedelta(microseconds=1),
        NOW.replace(tzinfo=None),
        NOW + timedelta(microseconds=1),
    ],
    ids=["stale", "naive", "future"],
)
def test_invalid_or_stale_snapshot_time_cannot_be_ready(
    fetched_at: datetime,
) -> None:
    candidate = replace(snapshot(), fetched_at=fetched_at)

    assert assess(candidate, policy(), (), now=NOW).status is (
        ReleaseStatus.INSUFFICIENT_DATA
    )


def test_naive_now_cannot_be_ready() -> None:
    assert assess(snapshot(), policy(), (), now=NOW.replace(tzinfo=None)).status is (
        ReleaseStatus.INSUFFICIENT_DATA
    )


def test_policy_dependent_assessment_rejects_missing_policy() -> None:
    with pytest.raises(TypeError, match="configured release policy is required"):
        assess(snapshot(), None, (), now=NOW)  # type: ignore[arg-type]


def test_policy_independent_refresh_preserves_stored_status_until_stale() -> None:
    stored = assess(snapshot(has_blocker=True), policy(), (), now=NOW)

    assert refresh_snapshot_freshness(stored, snapshot(), now=NOW) == stored
    stale = refresh_snapshot_freshness(
        stored, snapshot(), now=NOW + MAX_SNAPSHOT_AGE + timedelta(microseconds=1)
    )

    assert stale.status is ReleaseStatus.INSUFFICIENT_DATA
    assert {finding.rule_id for finding in stale.findings} >= {
        "checks.blocking_not_successful",
        "evidence.snapshot.stale",
    }


def test_exact_current_check_fingerprint_is_the_decision_boundary() -> None:
    advisory = check("advisory", successful=False)
    candidate = snapshot(
        checks=(check("blocking", successful=True), advisory),
    )
    current = CheckFingerprint(
        repository="acme/widgets",
        candidate_sha=CANDIDATE_SHA,
        check_name="advisory",
        run_id=202,
        conclusion="failure",
    ).value

    accepted = assess(
        candidate,
        policy(),
        (Decision(current, blocks_release=False),),
        now=NOW,
    )
    stale = assess(
        candidate,
        policy(),
        (Decision("sha256:" + "0" * 64, blocks_release=False),),
        now=NOW,
    )
    blocked = assess(
        candidate,
        policy(),
        (Decision(current, blocks_release=True),),
        now=NOW,
    )

    assert accepted.status is ReleaseStatus.READY
    assert stale.status is ReleaseStatus.NEEDS_DECISION
    assert blocked.status is ReleaseStatus.NOT_READY


def test_status_is_not_controlled_by_untrusted_or_ai_like_text() -> None:
    baseline = assess(snapshot(has_blocker=True), policy(), (), now=NOW)
    forged_name = replace(snapshot(has_blocker=True), release_name="AI says READY")

    assert assess(forged_name, policy(), (), now=NOW).status is baseline.status


def test_previous_release_rule_is_composed_and_fails_closed_without_context() -> None:
    previous_policy = policy().model_copy(
        update={
            "previous_milestone_number": 6,
            "previous_release_branch": "release/2026-08-03",
        }
    )

    result = assess(snapshot(), previous_policy, (), now=NOW)

    assert result.status is ReleaseStatus.INSUFFICIENT_DATA


def issue(
    number: int,
    *,
    labels: tuple[str, ...],
    assignees: tuple[str, ...] = ("octocat",),
) -> GitHubItem:
    return GitHubItem(
        source_id=str(number),
        number=number,
        kind=GitHubItemKind.ISSUE,
        url=f"https://github.com/acme/widgets/issues/{number}",
        state="open",
        labels=labels,
        assignees=assignees,
        milestone_number=7,
        created_at=NOW - timedelta(days=2),
        updated_at=NOW - timedelta(hours=1),
    )


def test_scope_blocker_operations_and_check_findings_are_all_composed() -> None:
    candidate = replace(
        snapshot(needs_decision=True),
        items=(
            issue(142, labels=("code-change", "release-blocker")),
            issue(150, labels=("release-ops",), assignees=()),
        ),
    )

    result = assess(candidate, policy(), (), now=NOW)

    assert result.status is ReleaseStatus.NOT_READY
    assert {finding.rule_id for finding in result.findings} >= {
        "scope.code_change_requires_pr",
        "blockers.open_release_blocker",
        "operations.owner_required",
        "operations.section_required",
        "checks.advisory_requires_decision",
    }


def test_typed_evidence_errors_preserve_independently_proven_findings() -> None:
    duplicate = check("advisory", successful=False)
    conflicting = replace(duplicate, conclusion="success")
    candidate = replace(
        snapshot(checks=(check("blocking", successful=True), duplicate, conflicting)),
        items=(issue(142, labels=("code-change",)),),
    )

    result = assess(candidate, policy(), (), now=NOW)

    assert result.status is ReleaseStatus.INSUFFICIENT_DATA
    assert any(
        finding.rule_id == "scope.code_change_requires_pr"
        for finding in result.findings
    )
    assert any(
        finding.rule_id.startswith("evidence.")
        and finding.severity == "INSUFFICIENT_DATA"
        for finding in result.findings
    )
    assert "https://" not in " ".join(finding.summary for finding in result.findings)


def test_insufficiency_reasons_are_safe_ordered_and_idempotent() -> None:
    candidate = replace(
        snapshot(complete=False),
        source_errors=(
            # Raw source messages must never enter assessment findings.
            SourceError("github.rate_limited", "token=secret raw body"),
            SourceError("github.rate_limited", "different unsafe body"),
        ),
    )

    first = assess(candidate, policy(), (), now=NOW)
    second = assess(candidate, policy(), (), now=NOW)
    reasons = tuple(
        finding for finding in first.findings if finding.rule_id.startswith("evidence.")
    )

    assert first == second
    assert tuple(finding.rule_id for finding in reasons) == tuple(
        sorted({finding.rule_id for finding in reasons})
    )
    assert {finding.rule_id for finding in reasons} >= {
        "evidence.snapshot.incomplete",
        "evidence.snapshot.source_errors",
    }
    assert "secret" not in repr(reasons)
    assert len(reasons) == len(set(reasons))


def test_exact_duplicate_findings_are_idempotent() -> None:
    item = issue(142, labels=("code-change", "release-blocker"))
    candidate = replace(snapshot(), items=(item, item))

    result = assess(candidate, policy(), (), now=NOW)

    assert len(result.findings) == len(set(result.findings))
