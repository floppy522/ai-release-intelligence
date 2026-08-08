from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from itertools import permutations
from typing import cast

import pytest

from release_intelligence.domain.models import (
    EvidenceRef,
    ReleaseSnapshot,
    SnapshotVersion,
)
from release_intelligence.domain.policy import ReleasePolicy
from release_intelligence.domain.rules.blockers import (
    BlockerEvidenceError,
    evaluate_blockers,
)
from release_intelligence.ports.github import GitHubItem, GitHubItemKind

NOW = datetime(2026, 8, 7, 12, tzinfo=UTC)
REPOSITORY = "acme/widgets"
CANDIDATE_SHA = "a" * 40
POLICY = ReleasePolicy(
    main_branch="main",
    candidate_branch="release/2026-08-10",
    milestone_number=7,
    code_change_label="code-change",
    release_ops_label="release-ops",
    blocker_label="release-blocker",
    check_categories={},
)


def _issue(
    number: int = 41,
    *,
    state: str = "open",
    labels: tuple[str, ...] = ("release-blocker",),
    source_id: str | None = None,
    url: str | None = None,
) -> GitHubItem:
    return GitHubItem(
        source_id=source_id or str(number * 10),
        number=number,
        kind=GitHubItemKind.ISSUE,
        url=url or f"https://github.com/{REPOSITORY}/issues/{number}",
        state=state,
        labels=labels,
        assignees=("release-owner",),
        milestone_number=7,
        created_at=NOW,
        updated_at=NOW,
        body="",
    )


def _snapshot(*items: GitHubItem) -> ReleaseSnapshot:
    return ReleaseSnapshot(
        release_name="Milestone 7",
        issue_number="41",
        milestone_number=7,
        issue_labels=(),
        linked_pr_numbers=(),
        issue_evidence=EvidenceRef(
            "unused", "unused", "unused", "https://github.com", "unused"
        ),
        snapshot_version=SnapshotVersion.GITHUB_V1,
        repository_id="repo-1",
        repository_full_name=REPOSITORY,
        fetch_started_at=NOW,
        fetched_at=NOW,
        candidate_ref=POLICY.candidate_branch,
        candidate_sha=CANDIDATE_SHA,
        items=items,
    )


def test_open_release_blocker_blocks_and_cannot_be_overridden() -> None:
    findings = evaluate_blockers(_snapshot(_issue(state="open")), POLICY)

    assert findings[0].rule_id == "blockers.open_release_blocker"
    assert findings[0].blocks_release is True
    assert findings[0].decision_allowed is False
    assert findings[0].evidence[0].url == ("https://github.com/acme/widgets/issues/41")
    assert len(findings[0].evidence[0].fingerprint) == 71


@pytest.mark.parametrize(
    "issue",
    [
        _issue(state="closed"),
        _issue(labels=("code-change",)),
        replace(_issue(), milestone_number=8),
    ],
)
def test_closed_non_blockers_and_foreign_milestones_do_not_block(
    issue: GitHubItem,
) -> None:
    assert evaluate_blockers(_snapshot(issue), POLICY) == ()


def test_blocker_label_matching_is_case_and_whitespace_normalized() -> None:
    findings = evaluate_blockers(
        _snapshot(_issue(labels=(" RELEASE-BLOCKER ",))), POLICY
    )

    assert len(findings) == 1


def test_blocker_order_and_exact_duplicate_handling_are_deterministic() -> None:
    items = (_issue(42), _issue(41))
    expected = evaluate_blockers(_snapshot(*items), POLICY)

    for ordering in permutations(items):
        assert evaluate_blockers(_snapshot(*ordering, ordering[0]), POLICY) == expected
    assert [finding.evidence[0].source_id for finding in expected] == ["410", "420"]


def test_set_like_issue_fields_do_not_create_false_conflicts() -> None:
    blocker = replace(
        _issue(labels=("release-blocker", "backend")),
        assignees=("zulu", "alpha"),
    )
    reordered = replace(
        blocker,
        labels=("backend", "release-blocker"),
        assignees=("alpha", "zulu"),
    )

    assert evaluate_blockers(_snapshot(blocker, reordered), POLICY) == (
        evaluate_blockers(_snapshot(blocker), POLICY)
    )


def test_conflicting_blocker_records_are_insufficient_but_preserve_other_blockers() -> (
    None
):
    conflict = replace(_issue(42), source_id="4200")

    with pytest.raises(BlockerEvidenceError) as raised:
        evaluate_blockers(_snapshot(_issue(41), _issue(42), conflict), POLICY)

    assert [finding.evidence[0].source_id for finding in raised.value.findings] == [
        "410"
    ]
    assert raised.value.codes == ("issue.conflicting_records:42",)


def test_cross_repository_blocker_url_is_typed_insufficiency() -> None:
    with pytest.raises(BlockerEvidenceError) as raised:
        evaluate_blockers(
            _snapshot(_issue(url="https://github.com/other/widgets/issues/41")),
            POLICY,
        )

    assert raised.value.findings == ()
    assert raised.value.codes == ("issue.invalid_url:41",)


def test_open_and_closed_records_for_one_issue_are_conflicting() -> None:
    with pytest.raises(BlockerEvidenceError) as raised:
        evaluate_blockers(
            _snapshot(_issue(state="open"), _issue(state="closed")), POLICY
        )

    assert raised.value.findings == ()
    assert raised.value.codes == ("issue.conflicting_records:41",)


def test_unknown_release_labeled_state_is_typed_insufficiency() -> None:
    with pytest.raises(BlockerEvidenceError) as raised:
        evaluate_blockers(_snapshot(_issue(state="mystery")), POLICY)

    assert raised.value.findings == ()
    assert raised.value.codes == ("issue.invalid_state:41",)


@pytest.mark.parametrize(
    ("corrupt", "expected_code"),
    [
        (lambda item: replace(item, number=0), "issue.invalid_coordinate:"),
        (lambda item: replace(item, number=2**63), "issue.invalid_coordinate:"),
        (lambda item: replace(item, source_id="0"), "issue.invalid_source_id:41"),
        (
            lambda item: replace(item, source_id=str(2**63)),
            "issue.invalid_source_id:41",
        ),
        (
            lambda item: replace(item, milestone_number=cast(int, True)),
            "issue.invalid_milestone:41",
        ),
        (
            lambda item: replace(item, created_at=NOW.replace(tzinfo=None)),
            "issue.invalid_timestamps:41",
        ),
        (
            lambda item: replace(item, created_at=NOW, updated_at=NOW.replace(hour=11)),
            "issue.invalid_timestamps:41",
        ),
        (
            lambda item: replace(item, labels=("release-blocker", "")),
            "issue.invalid_labels:41",
        ),
        (
            lambda item: replace(item, assignees=("x" * 256,)),
            "issue.invalid_assignees:41",
        ),
        (
            lambda item: replace(item, body="x" * 65_537),
            "issue.invalid_body:41",
        ),
        (
            lambda item: replace(item, kind=cast(GitHubItemKind, "unknown")),
            "issue.invalid_kind:41",
        ),
        (
            lambda item: replace(item, url=cast(str, ["unsafe"])),
            "issue.invalid_url:41",
        ),
    ],
)
def test_malformed_issue_evidence_is_bounded_typed_insufficiency(
    corrupt, expected_code: str
) -> None:
    with pytest.raises(BlockerEvidenceError) as raised:
        evaluate_blockers(_snapshot(corrupt(_issue())), POLICY)

    assert raised.value.findings == ()
    if expected_code.endswith(":"):
        assert len(raised.value.codes) == 1
        assert raised.value.codes[0].startswith(expected_code)
        assert len(raised.value.codes[0]) <= 64
    else:
        assert raised.value.codes == (expected_code,)
