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
    assert AnalysisRepository._snapshot_from_payload(payload) == snapshot()
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


def test_candidate_pr_milestone_metadata_does_not_override_item_membership() -> None:
    release = snapshot(
        pulls=(pull(143, base_ref=PREVIOUS_BRANCH, milestone=5),),
        links=(),
    )

    findings = evaluate_backmerge(release, POLICY)

    assert [finding.rule_id for finding in findings] == ["backmerge.main_pr_required"]


def test_main_pr_milestone_metadata_and_item_membership_are_irrelevant() -> None:
    release = snapshot(
        items=(issue(), pull_item(143), pull_item(144)),
        pulls=(
            pull(143, base_ref=PREVIOUS_BRANCH),
            pull(144, base_ref="main", milestone=5),
        ),
    )

    assert evaluate_backmerge(release, POLICY) == ()


def test_pull_records_differing_only_by_irrelevant_milestone_are_idempotent() -> None:
    first = pull(143, base_ref=PREVIOUS_BRANCH, milestone=5)
    release = snapshot(
        links=(),
        pulls=(first, replace(first, milestone_number=99)),
    )

    assert evaluate_backmerge(release, POLICY) == evaluate_backmerge(
        snapshot(links=(), pulls=(first,)), POLICY
    )


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


def test_snapshot_keeps_all_legacy_positional_fields_in_place() -> None:
    original = snapshot()

    restored = ReleaseSnapshot(
        original.release_name,
        original.issue_number,
        original.milestone_number,
        original.issue_labels,
        original.linked_pr_numbers,
        original.issue_evidence,
        original.snapshot_version,
        original.repository_id,
        original.repository_full_name,
        original.fetch_started_at,
        original.fetched_at,
        original.complete,
        original.source_errors,
        original.candidate_ref,
        original.candidate_sha,
        original.items,
        original.links,
        original.pull_requests,
        original.checks,
        original.comparisons,
    )

    assert restored.items == original.items
    assert restored.links == original.links
    assert restored.pull_requests == original.pull_requests
    assert restored.checks == original.checks
    assert restored.comparisons == original.comparisons
    assert restored.previous_milestone_number is None
    assert restored.previous_release_branch is None


def test_milestone_item_bound_is_exactly_one_hundred() -> None:
    at_limit = snapshot(
        items=tuple(issue(number, milestone=5) for number in range(1, 101)),
        links=(),
        pulls=(),
    )

    assert evaluate_backmerge(at_limit, POLICY) == ()

    with pytest.raises(BackmergeEvidenceError) as raised:
        evaluate_backmerge(
            replace(at_limit, items=(*at_limit.items, issue(101, milestone=5))),
            POLICY,
        )

    assert raised.value.codes == ("snapshot.too_many_items",)


def test_related_link_bound_is_exactly_two_hundred() -> None:
    issues = tuple(issue(number, milestone=5) for number in range(1, 101))
    pulls = tuple(
        pull(number, base_ref="develop", milestone=None) for number in range(1000, 1200)
    )
    relations = tuple(
        link(issue_number, pull_number)
        for issue_number in range(1, 101)
        for pull_number in (999 + issue_number * 2 - 1, 999 + issue_number * 2)
    )
    at_limit = snapshot(items=issues, links=relations, pulls=pulls)

    assert evaluate_backmerge(at_limit, POLICY) == ()

    with pytest.raises(BackmergeEvidenceError) as raised:
        evaluate_backmerge(
            replace(at_limit, links=(*at_limit.links, at_limit.links[0])), POLICY
        )

    assert raised.value.codes == ("snapshot.too_many_links",)


def test_related_pull_bound_is_exactly_two_hundred() -> None:
    pulls = tuple(
        pull(number, base_ref="develop", milestone=None) for number in range(1000, 1200)
    )
    at_limit = snapshot(items=(), links=(), pulls=pulls)

    assert evaluate_backmerge(at_limit, POLICY) == ()

    with pytest.raises(BackmergeEvidenceError) as raised:
        evaluate_backmerge(
            replace(
                at_limit,
                pull_requests=(
                    *at_limit.pull_requests,
                    pull(1200, base_ref="develop", milestone=None),
                ),
            ),
            POLICY,
        )

    assert raised.value.codes == ("snapshot.too_many_pulls",)


@pytest.mark.parametrize(
    ("items", "expected_code"),
    [
        (
            (
                issue(142, milestone=5, source_id="999"),
                issue(145, milestone=5, source_id="999"),
            ),
            "item.conflicting_source_id:142:145",
        ),
        (
            (
                pull_item(143, milestone=5, source_id="999"),
                pull_item(146, milestone=5, source_id="999"),
            ),
            "item.conflicting_source_id:143:146",
        ),
    ],
)
def test_milestone_item_source_id_cannot_alias_distinct_coordinates(
    items: tuple[GitHubItem, ...], expected_code: str
) -> None:
    with pytest.raises(BackmergeEvidenceError) as raised:
        evaluate_backmerge(snapshot(items=items, links=(), pulls=()), POLICY)

    assert raised.value.findings == ()
    assert raised.value.codes == (expected_code,)


def test_pull_source_id_cannot_alias_distinct_coordinates() -> None:
    pulls = (
        pull(143, base_ref="develop", milestone=None, source_id="999"),
        pull(146, base_ref="develop", milestone=None, source_id="999"),
    )

    with pytest.raises(BackmergeEvidenceError) as raised:
        evaluate_backmerge(snapshot(items=(), links=(), pulls=pulls), POLICY)

    assert raised.value.findings == ()
    assert raised.value.codes == ("pull.conflicting_source_id:143:146",)


def test_link_source_id_cannot_alias_distinct_relations() -> None:
    release = snapshot(
        items=(issue(142, milestone=5), issue(145, milestone=5)),
        links=(link(142, 143, source_id="999"), link(145, 143, source_id="999")),
        pulls=(pull(143, base_ref="develop", milestone=None),),
    )

    with pytest.raises(BackmergeEvidenceError) as raised:
        evaluate_backmerge(release, POLICY)

    assert raised.value.findings == ()
    assert raised.value.codes == ("link.conflicting_source_id:142:143:145:143",)


def test_aliased_possible_main_link_quarantines_both_relation_endpoints() -> None:
    unrelated = issue(147)
    aliased_main = link(142, 144, source_id="999")
    alias_peer = link(145, 146, source_id="999")
    stable_links = (link(142, 143), link(147, 143))
    release = snapshot(
        items=(
            issue(142),
            issue(145, milestone=5),
            unrelated,
            pull_item(143),
        ),
        pulls=(
            pull(143, base_ref=PREVIOUS_BRANCH),
            pull(144, base_ref="main", milestone=None),
            pull(146, base_ref="develop", milestone=None),
        ),
    )

    expected_findings = None
    for aliases in ((aliased_main, alias_peer), (alias_peer, aliased_main)):
        for relations in (stable_links, tuple(reversed(stable_links))):
            with pytest.raises(BackmergeEvidenceError) as raised:
                evaluate_backmerge(
                    replace(release, links=(*relations, *aliases)), POLICY
                )

            assert raised.value.codes == ("link.conflicting_source_id:142:144:145:146",)
            assert [
                finding.evidence[0].source_id for finding in raised.value.findings
            ] == [unrelated.source_id]
            if expected_findings is None:
                expected_findings = raised.value.findings
            assert raised.value.findings == expected_findings


@pytest.mark.parametrize(
    ("malformed", "expected_code"),
    [
        (
            lambda relation: replace(
                relation,
                url="https://github.com/attacker/repo/pull/144",
            ),
            "link.invalid_url:142:144",
        ),
        (
            lambda relation: replace(relation, source_id="invalid"),
            "link.invalid_source_id:142:144",
        ),
        (
            lambda relation: replace(relation, created_at=NOW.replace(tzinfo=None)),
            "link.invalid_timestamps:142:144",
        ),
    ],
)
def test_rejected_safe_coordinate_link_quarantines_both_dependencies(
    malformed, expected_code: str
) -> None:
    unrelated = issue(147)
    possible_main = link(142, 144)
    base_links = (link(142, 143), link(147, 143))
    release = snapshot(
        items=(issue(142), unrelated, pull_item(143)),
        pulls=(
            pull(143, base_ref=PREVIOUS_BRANCH),
            pull(144, base_ref="main", milestone=None),
        ),
    )

    for relations in (
        (*base_links, malformed(possible_main)),
        (malformed(possible_main), *reversed(base_links)),
    ):
        with pytest.raises(BackmergeEvidenceError) as raised:
            evaluate_backmerge(replace(release, links=relations), POLICY)

        assert raised.value.codes == (expected_code,)
        assert [finding.evidence[0].source_id for finding in raised.value.findings] == [
            unrelated.source_id
        ]


def test_conflicting_possible_main_link_quarantines_both_dependencies() -> None:
    unrelated = issue(147)
    first = link(142, 144)
    second = replace(first, source_id="999")
    stable_links = (link(142, 143), link(147, 143))
    release = snapshot(
        items=(issue(142), unrelated, pull_item(143)),
        pulls=(
            pull(143, base_ref=PREVIOUS_BRANCH),
            pull(144, base_ref="main", milestone=None),
        ),
    )

    expected_findings = None
    for conflict in ((first, second), (second, first)):
        with pytest.raises(BackmergeEvidenceError) as raised:
            evaluate_backmerge(
                replace(release, links=(*reversed(stable_links), *conflict)), POLICY
            )

        assert raised.value.codes == ("link.conflicting_records:142:144",)
        assert [finding.evidence[0].source_id for finding in raised.value.findings] == [
            unrelated.source_id
        ]
        if expected_findings is None:
            expected_findings = raised.value.findings
        assert raised.value.findings == expected_findings


def test_missing_issue_link_quarantines_only_its_valid_pull_endpoint() -> None:
    proven = issue(147)
    release = snapshot(
        items=(proven, pull_item(143), pull_item(146)),
        links=(link(999, 143), link(147, 146)),
        pulls=(
            pull(143, base_ref=PREVIOUS_BRANCH),
            pull(146, base_ref=PREVIOUS_BRANCH),
        ),
    )

    with pytest.raises(BackmergeEvidenceError) as raised:
        evaluate_backmerge(release, POLICY)

    assert raised.value.codes == ("link.missing_issue:999:143",)
    assert [finding.evidence[0].source_id for finding in raised.value.findings] == [
        proven.source_id
    ]


def test_exact_duplicate_links_remain_idempotent_not_uncertain() -> None:
    relation = link(142, 143)
    release = snapshot(
        links=(relation,),
        pulls=(pull(143, base_ref=PREVIOUS_BRANCH),),
    )
    expected = evaluate_backmerge(release, POLICY)

    assert (
        evaluate_backmerge(replace(release, links=(relation, relation)), POLICY)
        == expected
    )


def test_pr_item_and_pull_source_id_must_match() -> None:
    release = snapshot(
        items=(issue(), pull_item(143, source_id="999")),
        links=(link(142, 143),),
        pulls=(pull(143, base_ref=PREVIOUS_BRANCH, source_id="888"),),
    )

    with pytest.raises(BackmergeEvidenceError) as raised:
        evaluate_backmerge(release, POLICY)

    assert raised.value.findings == ()
    assert raised.value.codes == ("pull.source_id_mismatch:143",)


def test_identity_mismatch_preserves_unrelated_proven_finding() -> None:
    valid_issue = issue(145)
    release = snapshot(
        items=(
            issue(142),
            valid_issue,
            pull_item(143, source_id="999"),
            pull_item(146),
        ),
        links=(link(142, 143), link(145, 146)),
        pulls=(
            pull(143, base_ref=PREVIOUS_BRANCH, source_id="888"),
            pull(146, base_ref=PREVIOUS_BRANCH),
        ),
    )

    with pytest.raises(BackmergeEvidenceError) as raised:
        evaluate_backmerge(release, POLICY)

    assert raised.value.codes == ("pull.source_id_mismatch:143",)
    assert [finding.evidence[0].source_id for finding in raised.value.findings] == [
        valid_issue.source_id
    ]


def test_invalid_issue_coordinate_makes_valid_candidate_relation_uncertain() -> None:
    valid_issue = issue(145)
    malformed = replace(link(142, 143), issue_number=0, source_id="999")
    base_links = (malformed, link(145, 146))
    release = snapshot(
        items=(valid_issue, pull_item(143), pull_item(146)),
        pulls=(
            pull(143, base_ref=PREVIOUS_BRANCH),
            pull(146, base_ref=PREVIOUS_BRANCH),
        ),
    )

    for relations in (base_links, tuple(reversed(base_links))):
        with pytest.raises(BackmergeEvidenceError) as raised:
            evaluate_backmerge(replace(release, links=relations), POLICY)

        assert raised.value.codes == ("link.invalid_coordinate",)
        assert [finding.evidence[0].source_id for finding in raised.value.findings] == [
            valid_issue.source_id
        ]


def test_invalid_pull_coordinate_makes_valid_issue_relation_uncertain() -> None:
    valid_issue = issue(145)
    malformed = replace(link(142, 143), pull_request_number=0, source_id="999")
    base_links = (
        link(142, 143),
        malformed,
        link(145, 143),
    )
    release = snapshot(
        items=(issue(142), valid_issue, pull_item(143)),
        pulls=(pull(143, base_ref=PREVIOUS_BRANCH),),
    )

    for relations in (base_links, tuple(reversed(base_links))):
        with pytest.raises(BackmergeEvidenceError) as raised:
            evaluate_backmerge(replace(release, links=relations), POLICY)

        assert raised.value.codes == ("link.invalid_coordinate",)
        assert [finding.evidence[0].source_id for finding in raised.value.findings] == [
            valid_issue.source_id
        ]


def _repository_for_url_length(kind: str, number: int, length: int) -> str:
    fixed = len(f"https://github.com//r/{kind}/{number}")
    repository = f"{'o' * (length - fixed)}/r"
    assert len(f"https://github.com/{repository}/{kind}/{number}") == length
    return repository


@pytest.mark.parametrize("family", ["issue", "pull_item", "pull", "link"])
def test_untrusted_urls_accept_exact_bound_and_reject_one_over(family: str) -> None:
    kind = "issues" if family == "issue" else "pull"
    number = 1 if family in {"issue", "link"} else 143
    pull_number = 1000 if family == "link" else number
    repository = _repository_for_url_length(kind, number, 2_048)
    if family == "link":
        repository = _repository_for_url_length("pull", pull_number, 2_048)
    issue_number = number
    issue_record = issue(
        issue_number,
        milestone=5,
        url=f"https://github.com/{repository}/issues/{issue_number}",
    )
    pull_record = pull(
        pull_number,
        base_ref="develop",
        milestone=None,
        url=f"https://github.com/{repository}/pull/{pull_number}",
    )
    pull_item_record = pull_item(
        number,
        milestone=5,
        url=f"https://github.com/{repository}/pull/{number}",
    )
    relation = link(
        issue_number,
        pull_number,
        url=f"https://github.com/{repository}/pull/{pull_number}",
    )
    cases = {
        "issue": snapshot(items=(issue_record,), links=(), pulls=()),
        "pull_item": snapshot(items=(pull_item_record,), links=(), pulls=()),
        "pull": snapshot(items=(), links=(), pulls=(pull_record,)),
        "link": snapshot(items=(issue_record,), links=(relation,), pulls=()),
    }
    at_limit = replace(cases[family], repository_full_name=repository)

    assert evaluate_backmerge(at_limit, POLICY) == ()

    if family == "issue":
        over_repository = _repository_for_url_length("issues", 1, 2_049)
        over = replace(
            at_limit,
            repository_full_name=over_repository,
            items=(
                replace(
                    issue_record,
                    url=f"https://github.com/{over_repository}/issues/1",
                ),
            ),
        )
        expected_code = "issue.invalid_url:1"
    elif family == "pull_item":
        over_repository = _repository_for_url_length("pull", 143, 2_049)
        over = replace(
            at_limit,
            repository_full_name=over_repository,
            items=(
                replace(
                    pull_item_record,
                    url=f"https://github.com/{over_repository}/pull/143",
                ),
            ),
        )
        expected_code = "pull_item.invalid_url:143"
    elif family == "pull":
        over_repository = _repository_for_url_length("pull", 143, 2_049)
        over = replace(
            at_limit,
            repository_full_name=over_repository,
            pull_requests=(
                replace(
                    pull_record,
                    url=f"https://github.com/{over_repository}/pull/143",
                ),
            ),
        )
        expected_code = "pull.invalid_url:143"
    else:
        over_repository = _repository_for_url_length("pull", 1000, 2_049)
        over = replace(
            at_limit,
            repository_full_name=over_repository,
            items=(
                replace(
                    issue_record,
                    url=f"https://github.com/{over_repository}/issues/1",
                ),
            ),
            links=(
                replace(
                    relation,
                    url=f"https://github.com/{over_repository}/pull/1000",
                ),
            ),
            pull_requests=(),
        )
        expected_code = "link.invalid_url:1:1000"

    with pytest.raises(BackmergeEvidenceError) as raised:
        evaluate_backmerge(over, POLICY)

    assert raised.value.codes == (expected_code,)
