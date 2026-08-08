from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import permutations
from pathlib import Path
from typing import Any

import pytest

from release_intelligence.adapters.persistence.repositories import AnalysisRepository
from release_intelligence.domain.models import (
    EvidenceRef,
    ReleaseLink,
    ReleaseSnapshot,
    SnapshotVersion,
)
from release_intelligence.domain.policy import PolicyValidationError, ReleasePolicy
from release_intelligence.domain.rules.backmerge import (
    BackmergeEvidenceError,
    evaluate_backmerge,
)
from release_intelligence.ports.github import (
    GitHubItem,
    GitHubItemKind,
    GitHubPullRequest,
)

NOW = datetime(2026, 8, 7, 14, 30, tzinfo=UTC)
REPOSITORY = "example/release-intelligence"
PREVIOUS_MILESTONE = 6
PREVIOUS_BRANCH = "release/2026-08-03"
CURRENT_BRANCH = "release/2026-08-10"
PREVIOUS_SHA = "1" * 40
MAIN_SHA = "2" * 40
POLICY = ReleasePolicy(
    main_branch="main",
    candidate_branch=CURRENT_BRANCH,
    milestone_number=7,
    previous_milestone_number=PREVIOUS_MILESTONE,
    previous_release_branch=PREVIOUS_BRANCH,
    code_change_label="code-change",
    release_ops_label="release-ops",
    blocker_label="release-blocker",
    check_categories={},
)
FIXTURE_PATH = (
    Path(__file__).parents[2] / "fixtures" / "github" / "backmerge_cases.json"
)


def issue(
    number: int = 142,
    *,
    milestone: int | None = PREVIOUS_MILESTONE,
    state: str = "closed",
    source_id: str | None = None,
    url: str | None = None,
    labels: tuple[str, ...] = ("code-change",),
) -> GitHubItem:
    return GitHubItem(
        source_id=source_id or str(number * 10),
        number=number,
        kind=GitHubItemKind.ISSUE,
        url=url or f"https://github.com/{REPOSITORY}/issues/{number}",
        state=state,
        labels=labels,
        assignees=("owner",),
        milestone_number=milestone,
        created_at=NOW - timedelta(days=20),
        updated_at=NOW - timedelta(days=1),
    )


def pull_item(
    number: int = 143,
    *,
    milestone: int | None = PREVIOUS_MILESTONE,
    state: str = "closed",
    source_id: str | None = None,
    url: str | None = None,
) -> GitHubItem:
    return GitHubItem(
        source_id=source_id or str(number * 10),
        number=number,
        kind=GitHubItemKind.PULL_REQUEST,
        url=url or f"https://github.com/{REPOSITORY}/pull/{number}",
        state=state,
        labels=(),
        assignees=("owner",),
        milestone_number=milestone,
        created_at=NOW - timedelta(days=18),
        updated_at=NOW - timedelta(days=1),
    )


def pull(
    number: int,
    *,
    base_ref: str,
    milestone: int | None = PREVIOUS_MILESTONE,
    state: str = "closed",
    merged: bool = True,
    merge_sha: str | None = None,
    source_id: str | None = None,
    url: str | None = None,
) -> GitHubPullRequest:
    effective_sha = (PREVIOUS_SHA if number == 143 else MAIN_SHA) if merged else None
    if merge_sha is not None:
        effective_sha = merge_sha
    return GitHubPullRequest(
        source_id=source_id or str(number * 10),
        number=number,
        url=url or f"https://github.com/{REPOSITORY}/pull/{number}",
        state=state,
        labels=(),
        assignees=("owner",),
        milestone_number=milestone,
        head_ref=f"feature/{number}",
        head_sha="3" * 40,
        base_ref=base_ref,
        base_sha="4" * 40,
        merge_commit_sha=effective_sha,
        merged_at=NOW - timedelta(days=1) if merged else None,
        created_at=NOW - timedelta(days=18),
        updated_at=NOW - timedelta(days=1),
    )


def link(
    issue_number: int,
    pull_number: int,
    *,
    source_id: str | None = None,
    url: str | None = None,
) -> ReleaseLink:
    return ReleaseLink(
        issue_number=issue_number,
        pull_request_number=pull_number,
        url=url or f"https://github.com/{REPOSITORY}/pull/{pull_number}",
        source_id=source_id or f"{issue_number}{pull_number}",
        created_at=NOW - timedelta(days=2),
    )


def snapshot(
    *,
    items: tuple[GitHubItem, ...] | None = None,
    links: tuple[ReleaseLink, ...] | None = None,
    pulls: tuple[GitHubPullRequest, ...] | None = None,
) -> ReleaseSnapshot:
    return ReleaseSnapshot(
        release_name="Milestone 7",
        issue_number="",
        milestone_number=7,
        issue_labels=(),
        linked_pr_numbers=(),
        issue_evidence=EvidenceRef(
            evidence_id="github-milestone-7",
            source_type="github_milestone",
            source_id="7",
            url=f"https://github.com/{REPOSITORY}/milestone/7",
            fingerprint="legacy-not-used",
        ),
        snapshot_version=SnapshotVersion.GITHUB_V1,
        repository_id="987654",
        repository_full_name=REPOSITORY,
        fetch_started_at=NOW - timedelta(minutes=1),
        fetched_at=NOW,
        complete=True,
        source_errors=(),
        candidate_ref=CURRENT_BRANCH,
        candidate_sha="5" * 40,
        previous_milestone_number=PREVIOUS_MILESTONE,
        previous_release_branch=PREVIOUS_BRANCH,
        items=items if items is not None else (issue(), pull_item(143)),
        links=links if links is not None else (link(142, 143), link(142, 144)),
        pull_requests=pulls
        if pulls is not None
        else (
            pull(143, base_ref=PREVIOUS_BRANCH),
            pull(144, base_ref="main", milestone=None),
        ),
    )


@pytest.fixture(scope="module")
def cases() -> dict[str, dict[str, Any]]:
    document = json.loads(FIXTURE_PATH.read_text())
    return {case["name"]: case for case in document["cases"]}


def from_case(case: dict[str, Any]) -> ReleaseSnapshot:
    main = case["main_pr"]
    pulls = [pull(143, base_ref=PREVIOUS_BRANCH)]
    links = [link(142, 143)]
    if main is not None:
        pulls.append(
            pull(
                144,
                base_ref=main["base_ref"],
                milestone=None,
                state=main["state"],
                merged=main["merged"],
            )
        )
        links.append(link(142, 144))
    return snapshot(links=tuple(links), pulls=tuple(pulls))


@pytest.mark.parametrize(
    "case_name",
    [
        "missing_main_pr",
        "merged_main_pr",
        "open_main_pr",
        "unmerged_main_pr",
        "wrong_base_main_pr",
    ],
)
def test_previous_release_pr_requires_related_merged_main_pr(
    case_name: str, cases: dict[str, dict[str, Any]]
) -> None:
    findings = evaluate_backmerge(from_case(cases[case_name]), POLICY)

    assert [finding.rule_id for finding in findings] == cases[case_name][
        "expected_rules"
    ]
    assert all(finding.severity == "BLOCKING" for finding in findings)
    assert all(finding.decision_allowed is False for finding in findings)


def test_cherry_pick_with_different_shas_passes_through_issue_relations() -> None:
    release = snapshot(
        pulls=(
            pull(143, base_ref=PREVIOUS_BRANCH, merge_sha="a" * 40),
            pull(144, base_ref="main", milestone=None, merge_sha="b" * 40),
        )
    )

    assert evaluate_backmerge(release, POLICY) == ()


def test_previous_release_pr_without_any_issue_link_is_blocking() -> None:
    findings = evaluate_backmerge(
        snapshot(links=(), pulls=(pull(143, base_ref=PREVIOUS_BRANCH),)), POLICY
    )

    assert [finding.rule_id for finding in findings] == ["backmerge.main_pr_required"]
    assert findings[0].evidence[0].source_type == "github_pull_request"


def test_every_linked_shipped_issue_requires_a_main_pr() -> None:
    release = snapshot(
        items=(issue(142), issue(145), pull_item(143)),
        links=(link(142, 143), link(145, 143), link(142, 144)),
        pulls=(
            pull(143, base_ref=PREVIOUS_BRANCH),
            pull(144, base_ref="main", milestone=None),
        ),
    )

    findings = evaluate_backmerge(release, POLICY)

    assert [
        (finding.rule_id, finding.evidence[0].source_id) for finding in findings
    ] == [("backmerge.main_pr_required", "1450")]


def test_one_of_multiple_candidate_main_prs_can_satisfy_an_issue() -> None:
    release = snapshot(
        links=(link(142, 143), link(142, 144), link(142, 145)),
        pulls=(
            pull(143, base_ref=PREVIOUS_BRANCH),
            pull(144, base_ref="develop", milestone=None),
            pull(145, base_ref="main", milestone=None),
        ),
    )

    assert evaluate_backmerge(release, POLICY) == ()


def test_nonqualifying_prs_and_foreign_milestone_items_are_ignored() -> None:
    release = snapshot(
        items=(issue(), pull_item(143, milestone=5), pull_item(144)),
        links=(),
        pulls=(
            pull(143, base_ref=PREVIOUS_BRANCH, milestone=5),
            pull(144, base_ref="main"),
        ),
    )

    assert evaluate_backmerge(release, POLICY) == ()


def test_closed_previous_milestone_issue_is_shipped_scope() -> None:
    release = snapshot(
        items=(issue(state="closed"), pull_item(143)),
        links=(link(142, 143),),
        pulls=(pull(143, base_ref=PREVIOUS_BRANCH),),
    )

    findings = evaluate_backmerge(release, POLICY)

    assert findings[0].evidence[0].source_type == "github_issue"


def test_foreign_milestone_issue_does_not_expand_shipped_scope() -> None:
    release = snapshot(
        items=(issue(milestone=5), pull_item(143)),
        links=(link(142, 143),),
        pulls=(pull(143, base_ref=PREVIOUS_BRANCH),),
    )

    findings = evaluate_backmerge(release, POLICY)

    assert findings[0].evidence[0].source_type == "github_pull_request"


@pytest.mark.parametrize(
    "base_ref,state,merged",
    [
        ("main", "closed", True),
        (PREVIOUS_BRANCH, "open", False),
        (PREVIOUS_BRANCH, "closed", False),
        ("develop", "closed", True),
    ],
)
def test_pr_not_merged_to_previous_branch_is_not_a_candidate(
    base_ref: str, state: str, merged: bool
) -> None:
    release = snapshot(
        items=(issue(), pull_item(143, state=state)),
        links=(),
        pulls=(pull(143, base_ref=base_ref, state=state, merged=merged),),
    )

    assert evaluate_backmerge(release, POLICY) == ()


def test_missing_previous_release_policy_is_validation_error() -> None:
    policy = ReleasePolicy(
        main_branch="main",
        candidate_branch=CURRENT_BRANCH,
        milestone_number=7,
        code_change_label="code-change",
        release_ops_label="release-ops",
        blocker_label="release-blocker",
        check_categories={},
    )

    with pytest.raises(PolicyValidationError, match="previous release"):
        evaluate_backmerge(snapshot(), policy)


def test_legacy_persisted_snapshot_defaults_are_insufficient() -> None:
    payload = AnalysisRepository._snapshot_payload(snapshot())
    payload.pop("previous_milestone_number")
    payload.pop("previous_release_branch")
    restored = AnalysisRepository._snapshot_from_payload(payload)

    with pytest.raises(BackmergeEvidenceError) as raised:
        evaluate_backmerge(restored, POLICY)

    assert restored.previous_milestone_number is None
    assert restored.previous_release_branch is None
    assert raised.value.findings == ()
    assert raised.value.codes == ("snapshot.previous_release_context_missing",)


def test_missing_linked_issue_record_is_insufficient_not_a_business_finding() -> None:
    release = snapshot(
        items=(pull_item(143),),
        links=(link(142, 143),),
        pulls=(pull(143, base_ref=PREVIOUS_BRANCH),),
    )

    with pytest.raises(BackmergeEvidenceError) as raised:
        evaluate_backmerge(release, POLICY)

    assert raised.value.findings == ()
    assert raised.value.codes == ("link.missing_issue:142:143",)


@pytest.mark.parametrize(
    "corrupt,expected_code",
    [
        (
            lambda value: replace(
                value,
                items=(
                    replace(
                        issue(),
                        url="https://github.com/attacker/repo/issues/142",
                    ),
                    pull_item(143),
                ),
            ),
            "issue.invalid_url:142",
        ),
        (
            lambda value: replace(
                value,
                links=(
                    replace(
                        link(142, 143),
                        url="https://github.com/attacker/repo/pull/143",
                    ),
                ),
                pull_requests=(pull(143, base_ref=PREVIOUS_BRANCH),),
            ),
            "link.invalid_url:142:143",
        ),
        (
            lambda value: replace(
                value,
                pull_requests=(
                    replace(
                        pull(143, base_ref=PREVIOUS_BRANCH),
                        url="https://github.com/attacker/repo/pull/143",
                    ),
                ),
            ),
            "pull.invalid_url:143",
        ),
        (
            lambda value: replace(
                value,
                items=(
                    issue(),
                    replace(
                        pull_item(143),
                        url="https://github.com/attacker/repo/pull/143",
                    ),
                ),
            ),
            "pull_item.invalid_url:143",
        ),
    ],
)
def test_malformed_chain_urls_are_typed_insufficiency(
    corrupt, expected_code: str
) -> None:
    with pytest.raises(BackmergeEvidenceError) as raised:
        evaluate_backmerge(corrupt(snapshot()), POLICY)

    assert expected_code in raised.value.codes


def test_conflicting_issue_chain_preserves_unrelated_proven_finding() -> None:
    valid_issue = issue(145)
    release = snapshot(
        items=(
            issue(142),
            replace(issue(142), labels=("release-ops",), source_id="1421"),
            valid_issue,
            pull_item(143),
        ),
        links=(link(142, 143), link(145, 143)),
        pulls=(pull(143, base_ref=PREVIOUS_BRANCH),),
    )

    with pytest.raises(BackmergeEvidenceError) as raised:
        evaluate_backmerge(release, POLICY)

    assert raised.value.codes == ("issue.conflicting_records:142",)
    assert [
        (finding.rule_id, finding.evidence[0].source_id)
        for finding in raised.value.findings
    ] == [("backmerge.main_pr_required", valid_issue.source_id)]


def test_malformed_issue_field_quarantines_only_its_chain() -> None:
    invalid = replace(issue(142), assignees=("not a github login",))
    valid = issue(145)
    release = snapshot(
        items=(invalid, valid, pull_item(143)),
        links=(link(142, 143), link(145, 143)),
        pulls=(pull(143, base_ref=PREVIOUS_BRANCH),),
    )

    with pytest.raises(BackmergeEvidenceError) as raised:
        evaluate_backmerge(release, POLICY)

    assert raised.value.codes == ("issue.invalid_assignees:142",)
    assert [finding.evidence[0].source_id for finding in raised.value.findings] == [
        valid.source_id
    ]


@pytest.mark.parametrize(
    "records,expected_code",
    [
        (
            lambda: (
                pull(143, base_ref=PREVIOUS_BRANCH),
                replace(pull(143, base_ref=PREVIOUS_BRANCH), base_ref="develop"),
            ),
            "pull.conflicting_records:143",
        ),
        (
            lambda: (pull(143, base_ref=PREVIOUS_BRANCH),),
            "pull_item.conflicting_records:143",
        ),
    ],
)
def test_conflicting_pr_or_milestone_item_is_typed_insufficiency(
    records, expected_code: str
) -> None:
    release = snapshot(pulls=records())
    if expected_code.startswith("pull_item"):
        release = replace(
            release,
            items=(
                issue(),
                pull_item(143),
                replace(pull_item(143), milestone_number=5, source_id="1431"),
            ),
        )

    with pytest.raises(BackmergeEvidenceError) as raised:
        evaluate_backmerge(release, POLICY)

    assert expected_code in raised.value.codes


def test_pr_and_milestone_item_membership_conflict_is_insufficient() -> None:
    release = snapshot(
        pulls=(pull(143, base_ref=PREVIOUS_BRANCH, milestone=5),),
        links=(),
    )

    with pytest.raises(BackmergeEvidenceError) as raised:
        evaluate_backmerge(release, POLICY)

    assert raised.value.findings == ()
    assert raised.value.codes == ("pull.milestone_mismatch:143",)


def test_contradictory_merge_state_is_insufficient() -> None:
    contradictory = replace(
        pull(143, base_ref=PREVIOUS_BRANCH),
        state="open",
    )

    with pytest.raises(BackmergeEvidenceError) as raised:
        evaluate_backmerge(
            snapshot(links=(link(142, 143),), pulls=(contradictory,)), POLICY
        )

    assert raised.value.findings == ()
    assert raised.value.codes == ("pull.invalid_merge_state:143",)


def test_merge_timestamp_after_pr_update_is_insufficient() -> None:
    contradictory = replace(
        pull(143, base_ref=PREVIOUS_BRANCH),
        merged_at=NOW,
    )

    with pytest.raises(BackmergeEvidenceError) as raised:
        evaluate_backmerge(
            snapshot(links=(link(142, 143),), pulls=(contradictory,)), POLICY
        )

    assert raised.value.findings == ()
    assert raised.value.codes == ("pull.invalid_timestamps:143",)


def test_conflicting_links_are_typed_and_order_stable() -> None:
    first = link(142, 143)
    second = replace(first, source_id="999")

    for links in ((first, second), (second, first)):
        with pytest.raises(BackmergeEvidenceError) as raised:
            evaluate_backmerge(
                snapshot(
                    links=links,
                    pulls=(pull(143, base_ref=PREVIOUS_BRANCH),),
                ),
                POLICY,
            )

        assert raised.value.codes == ("link.conflicting_records:142:143",)


def test_duplicate_and_permuted_evidence_is_idempotent_and_deterministic() -> None:
    failing = snapshot(
        items=(issue(142), issue(145), pull_item(143)),
        links=(link(142, 143), link(145, 143)),
        pulls=(pull(143, base_ref=PREVIOUS_BRANCH),),
    )
    expected = evaluate_backmerge(failing, POLICY)

    for item_order in permutations(failing.items):
        release = replace(
            failing,
            items=tuple(item_order) + (failing.items[0],),
            links=tuple(reversed(failing.links)) + (failing.links[0],),
            pull_requests=(failing.pull_requests[0], failing.pull_requests[0]),
        )
        assert evaluate_backmerge(release, POLICY) == expected


def test_direct_canonical_evidence_is_bounded_and_fingerprinted() -> None:
    findings = evaluate_backmerge(
        snapshot(links=(link(142, 143),), pulls=(pull(143, base_ref=PREVIOUS_BRANCH),)),
        POLICY,
    )

    assert 0 < len(findings[0].evidence) <= 10
    assert all(
        ref.url.startswith(f"https://github.com/{REPOSITORY}/")
        for ref in findings[0].evidence
    )
    assert all(
        ref.fingerprint.startswith("sha256:") and len(ref.fingerprint) == 71
        for ref in findings[0].evidence
    )
