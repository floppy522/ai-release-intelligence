from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import permutations
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest

from release_intelligence.domain.models import (
    EvidenceRef,
    PullRequestComparison,
    ReleaseLink,
    ReleaseSnapshot,
    SnapshotVersion,
    SourceError,
)
from release_intelligence.domain.policy import ReleasePolicy
from release_intelligence.domain.rules.scope import evaluate_scope
from release_intelligence.ports.github import (
    CommitComparison,
    GitHubCommit,
    GitHubItem,
    GitHubItemKind,
    GitHubPullRequest,
)

NOW = datetime(2026, 8, 7, 14, 30, tzinfo=UTC)
REPOSITORY = "example/release-intelligence"
BASE_SHA = "2" * 40
MIDDLE_SHA = "3" * 40
CANDIDATE_SHA = "4" * 40
ISSUE_URL = f"https://github.com/{REPOSITORY}/issues/142"
PULL_URL = f"https://github.com/{REPOSITORY}/pull/143"
POLICY = ReleasePolicy(
    main_branch="main",
    candidate_branch="release/2026-08-10",
    milestone_number=7,
    code_change_label="code-change",
    release_ops_label="release-ops",
    blocker_label="release-blocker",
    check_categories={},
)
FIXTURE_PATH = Path(__file__).parents[2] / "fixtures" / "github" / "scope_cases.json"


def issue(
    number: int = 142,
    *,
    labels: tuple[str, ...] = ("code-change",),
    state: str = "open",
    source_id: str | None = None,
    milestone: int | None = 7,
    url: str | None = None,
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
        updated_at=NOW - timedelta(hours=number % 10),
    )


def pull_item(
    number: int = 143,
    *,
    milestone: int | None = 7,
    url: str | None = None,
) -> GitHubItem:
    return GitHubItem(
        source_id=str(number * 10),
        number=number,
        kind=GitHubItemKind.PULL_REQUEST,
        url=url or f"https://github.com/{REPOSITORY}/pull/{number}",
        state="closed",
        labels=(),
        assignees=("owner",),
        milestone_number=milestone,
        created_at=NOW - timedelta(days=18),
        updated_at=NOW - timedelta(days=1),
    )


def pull(
    number: int = 143,
    *,
    milestone: int | None = 7,
    base_ref: str = "main",
    state: str = "closed",
    merged_at: datetime | None = NOW - timedelta(days=1),
    merge_sha: str | None = None,
    url: str | None = None,
) -> GitHubPullRequest:
    effective_merge_sha = merge_sha if merge_sha is not None else BASE_SHA
    return GitHubPullRequest(
        source_id=str(number * 10),
        number=number,
        url=url or f"https://github.com/{REPOSITORY}/pull/{number}",
        state=state,
        labels=(),
        assignees=("owner",),
        milestone_number=milestone,
        head_ref=f"feature/{number}",
        head_sha=f"head-{number}",
        base_ref=base_ref,
        base_sha="main-sha",
        merge_commit_sha=effective_merge_sha,
        merged_at=merged_at,
        created_at=NOW - timedelta(days=18),
        updated_at=NOW - timedelta(days=1),
    )


def link(
    issue_number: int = 142,
    pull_number: int = 143,
    *,
    url: str | None = None,
) -> ReleaseLink:
    return ReleaseLink(
        source_id=f"link-{issue_number}-{pull_number}",
        issue_number=issue_number,
        pull_request_number=pull_number,
        url=url or f"https://github.com/{REPOSITORY}/pull/{pull_number}",
        created_at=NOW - timedelta(days=15),
    )


def comparison(
    pull_number: int = 143,
    *,
    status: str = "ahead",
    base_sha: str | None = None,
    head_sha: str = CANDIDATE_SHA,
    merge_base_sha: str | None = None,
    ahead_by: int | None = None,
    behind_by: int | None = None,
    total_commits: int | None = None,
    commits: tuple[GitHubCommit, ...] | None = None,
    url: str | None = None,
) -> PullRequestComparison:
    effective_base = base_sha or BASE_SHA
    effective_merge_base = merge_base_sha or effective_base
    effective_ahead = (2 if status == "ahead" else 0) if ahead_by is None else ahead_by
    effective_behind = (
        (0 if status in {"ahead", "identical"} else 1)
        if behind_by is None
        else behind_by
    )
    effective_total = (
        (2 if status == "ahead" else 0) if total_commits is None else total_commits
    )
    effective_commits = (
        (
            (
                GitHubCommit(
                    sha=MIDDLE_SHA,
                    url=f"https://github.com/{REPOSITORY}/commit/{MIDDLE_SHA}",
                    committed_at=NOW - timedelta(days=2),
                ),
                GitHubCommit(
                    sha=CANDIDATE_SHA,
                    url=f"https://github.com/{REPOSITORY}/commit/{CANDIDATE_SHA}",
                    committed_at=NOW - timedelta(days=1),
                ),
            )
            if status == "ahead"
            else ()
        )
        if commits is None
        else commits
    )
    return PullRequestComparison(
        pull_request_number=pull_number,
        comparison=CommitComparison(
            status=status,
            ahead_by=effective_ahead,
            behind_by=effective_behind,
            total_commits=effective_total,
            url=url
            or (
                f"https://github.com/{REPOSITORY}/compare/{effective_base}...{head_sha}"
            ),
            base_sha=effective_base,
            merge_base_sha=effective_merge_base,
            commits=effective_commits,
            head_sha=head_sha,
        ),
    )


def snapshot(
    *,
    items: tuple[GitHubItem, ...] | None = None,
    links: tuple[ReleaseLink, ...] | None = None,
    pulls: tuple[GitHubPullRequest, ...] | None = None,
    comparisons: tuple[PullRequestComparison, ...] | None = None,
) -> ReleaseSnapshot:
    return ReleaseSnapshot(
        release_name="Milestone 7",
        issue_number="142",
        milestone_number=7,
        issue_labels=("code-change",),
        linked_pr_numbers=("143",),
        issue_evidence=EvidenceRef(
            evidence_id="github-issue-1420",
            source_type="github_issue",
            source_id="1420",
            url=ISSUE_URL,
            fingerprint="legacy-not-used",
        ),
        snapshot_version=SnapshotVersion.GITHUB_V1,
        repository_id="987654",
        repository_full_name=REPOSITORY,
        fetch_started_at=NOW - timedelta(minutes=1),
        fetched_at=NOW,
        complete=True,
        source_errors=(),
        candidate_ref=POLICY.candidate_branch,
        candidate_sha=CANDIDATE_SHA,
        items=items if items is not None else (issue(), pull_item()),
        links=links if links is not None else (link(),),
        pull_requests=pulls if pulls is not None else (pull(),),
        comparisons=comparisons if comparisons is not None else (comparison(),),
    )


@pytest.fixture(scope="module")
def cases() -> dict[str, dict[str, Any]]:
    document = json.loads(FIXTURE_PATH.read_text())
    return {case["name"]: case for case in document["cases"]}


def scope_case(case: dict[str, Any]) -> ReleaseSnapshot:
    issue_labels = tuple(case.get("issue_labels", ("code-change",)))
    issue_state = case.get("issue_state", "open")
    pull_milestone = case.get("pull_milestone", 7)
    merged_at = NOW - timedelta(days=case.get("pull_merged_days_ago", 1))
    pull_numbers = tuple(case.get("pulls", (143,)))
    pull_item_numbers = tuple(case.get("pull_items", (143,)))
    link_numbers = tuple(case.get("links", (143,)))
    comparison_numbers = tuple(case.get("comparisons", (143,)))
    return snapshot(
        items=(
            issue(labels=issue_labels, state=issue_state),
            *(
                pull_item(number, milestone=pull_milestone)
                for number in pull_item_numbers
            ),
        ),
        links=tuple(link(142, number) for number in link_numbers),
        pulls=tuple(
            pull(
                number,
                milestone=pull_milestone,
                base_ref=case.get("pull_base_ref", "main"),
                merged_at=merged_at,
            )
            for number in pull_numbers
        ),
        comparisons=tuple(
            comparison(
                number,
                status=case.get("comparison_status", "ahead"),
                merge_base_sha=case.get("comparison_merge_base_sha"),
            )
            for number in comparison_numbers
        ),
    )


@pytest.mark.parametrize(
    "case_name",
    [
        "missing_type",
        "two_types",
        "missing_pr",
        "pr_outside_milestone",
        "pr_not_merged_to_main",
        "change_missing_from_candidate",
    ],
)
def test_each_scope_violation_is_blocking_and_evidenced(
    case_name: str, cases: dict[str, dict[str, Any]]
) -> None:
    case = cases[case_name]

    findings = evaluate_scope(scope_case(case), POLICY)

    assert tuple(finding.rule_id for finding in findings) == tuple(
        case["expected_rules"]
    )
    assert all(finding.severity == "BLOCKING" for finding in findings)
    assert all(finding.evidence for finding in findings)
    assert all(
        _is_direct_github_issue_or_pr(ref.url)
        for finding in findings
        for ref in finding.evidence
    )
    assert all(
        ref.fingerprint.startswith("sha256:") and len(ref.fingerprint) == 71
        for finding in findings
        for ref in finding.evidence
    )


@pytest.mark.parametrize(
    "case_name",
    [
        "release_ops_without_pr",
        "closed_non_blocking_issue",
        "merged_before_cut_and_reachable",
    ],
)
def test_valid_scope_cases_do_not_create_false_positives(
    case_name: str, cases: dict[str, dict[str, Any]]
) -> None:
    assert evaluate_scope(scope_case(cases[case_name]), POLICY) == ()


def test_one_fully_valid_linked_pr_satisfies_the_issue() -> None:
    release = snapshot(
        items=(issue(), pull_item(143), pull_item(144, milestone=8)),
        links=(link(142, 144), link(142, 143)),
        pulls=(
            pull(144, milestone=8, base_ref="develop", merged_at=None),
            pull(143),
        ),
        comparisons=(
            comparison(144, status="behind", merge_base_sha="older-sha"),
            comparison(143),
        ),
    )

    assert evaluate_scope(release, POLICY) == ()


def test_ambiguous_links_are_blocking_and_fingerprinted() -> None:
    ambiguous = snapshot(
        links=(link(), replace(link(), source_id="other-link-source")),
    )
    ambiguous_finding = evaluate_scope(ambiguous, POLICY)[0]
    absent_finding = evaluate_scope(snapshot(links=()), POLICY)[0]

    assert ambiguous_finding.rule_id == "scope.code_change_requires_pr"
    assert ambiguous_finding.evidence != absent_finding.evidence
    assert all(
        ref.source_type == "github_issue_pr_link"
        for ref in ambiguous_finding.evidence[1:]
    )


def test_valid_pr_stages_must_form_one_complete_chain() -> None:
    release = snapshot(
        items=(issue(), pull_item(143), pull_item(144, milestone=8)),
        links=(link(142, 143), link(142, 144)),
        pulls=(
            pull(143, base_ref="develop"),
            pull(144, milestone=8),
        ),
        comparisons=(comparison(143), comparison(144)),
    )

    findings = evaluate_scope(release, POLICY)

    assert tuple(finding.rule_id for finding in findings) == (
        "scope.pr_requires_main_merge",
    )


def test_all_issue_findings_are_preserved_in_stable_order() -> None:
    missing_type = issue(141, labels=())
    missing_pr = issue(142)
    outside = issue(145)
    release = snapshot(
        items=(outside, missing_pr, missing_type, pull_item(146, milestone=8)),
        links=(link(145, 146),),
        pulls=(pull(146, milestone=8),),
        comparisons=(comparison(146),),
    )

    findings = evaluate_scope(release, POLICY)

    assert [
        (finding.rule_id, finding.evidence[0].source_id) for finding in findings
    ] == [
        ("scope.exactly_one_type", "1410"),
        ("scope.code_change_requires_pr", "1420"),
        ("scope.pr_requires_milestone", "1450"),
    ]


def test_conflicting_issue_records_are_all_fingerprinted() -> None:
    release = snapshot(
        items=(
            issue(labels=("code-change",)),
            issue(labels=("release-ops",), source_id="conflicting-issue-source"),
            pull_item(),
        )
    )

    finding = evaluate_scope(release, POLICY)[0]

    assert finding.rule_id == "scope.exactly_one_type"
    assert len(finding.evidence) == 2
    assert len({ref.fingerprint for ref in finding.evidence}) == 2


def test_evaluation_is_deterministic_under_evidence_permutations_and_duplicates() -> (
    None
):
    base = snapshot(
        items=(issue(141, labels=()), issue(), pull_item()),
        links=(link(),),
        pulls=(pull(),),
        comparisons=(comparison(),),
    )
    expected = evaluate_scope(base, POLICY)

    for item_order in permutations(base.items):
        permuted = replace(
            base,
            items=tuple(item_order) + (base.items[0],),
            links=(base.links[0], base.links[0]),
            pull_requests=(base.pull_requests[0], base.pull_requests[0]),
            comparisons=(base.comparisons[0], base.comparisons[0]),
        )
        assert evaluate_scope(permuted, POLICY) == expected


def test_semantically_identical_set_order_duplicates_are_idempotent() -> None:
    first_issue = issue(labels=("code-change", "backend"))
    first_pull = replace(pull(), labels=("backend", "ready"))
    first_pull_item = replace(pull_item(), labels=("backend", "ready"))
    release = snapshot(
        items=(
            first_issue,
            replace(first_issue, labels=("backend", "code-change")),
            first_pull_item,
            replace(first_pull_item, labels=("ready", "backend")),
        ),
        pulls=(first_pull, replace(first_pull, labels=("ready", "backend"))),
    )

    assert evaluate_scope(release, POLICY) == ()


def test_conflicting_duplicate_pull_records_do_not_fail_open() -> None:
    release = snapshot(
        pulls=(pull(), replace(pull(), base_ref="develop")),
    )

    findings = evaluate_scope(release, POLICY)

    assert tuple(finding.rule_id for finding in findings) == (
        "scope.pr_requires_milestone",
    )
    pull_evidence = [
        ref for ref in findings[0].evidence if ref.source_type == "github_pull_request"
    ]
    assert len(pull_evidence) == 2
    assert len({ref.fingerprint for ref in pull_evidence}) == 2


def test_source_evidence_fingerprint_is_independent_of_the_rule() -> None:
    outside_finding = evaluate_scope(
        snapshot(
            items=(issue(), pull_item(milestone=8)),
            pulls=(pull(milestone=8),),
        ),
        POLICY,
    )[0]
    main_finding = evaluate_scope(snapshot(pulls=(pull(base_ref="develop"),)), POLICY)[
        0
    ]

    assert outside_finding.evidence[0] == main_finding.evidence[0]


def test_issue_fingerprint_is_stable_under_set_like_field_order() -> None:
    first = evaluate_scope(
        snapshot(
            items=(issue(labels=("code-change", "backend")),),
            links=(),
            pulls=(),
            comparisons=(),
        ),
        POLICY,
    )[0]
    reordered = evaluate_scope(
        snapshot(
            items=(issue(labels=("backend", "code-change")),),
            links=(),
            pulls=(),
            comparisons=(),
        ),
        POLICY,
    )[0]

    assert first.evidence == reordered.evidence


def test_case_ties_assignees_and_exact_duplicates_have_canonical_fingerprints() -> None:
    first_issue = replace(
        issue(labels=("code-change", "CODE-CHANGE", "backend", "backend")),
        assignees=("Zulu", "alpha", "Zulu"),
    )
    reordered_issue = replace(
        first_issue,
        labels=("backend", "CODE-CHANGE", "code-change"),
        assignees=("alpha", "Zulu"),
    )
    first = evaluate_scope(
        snapshot(items=(first_issue,), links=(), pulls=(), comparisons=()),
        POLICY,
    )[0]
    reordered = evaluate_scope(
        snapshot(items=(reordered_issue,), links=(), pulls=(), comparisons=()),
        POLICY,
    )[0]

    assert first.evidence == reordered.evidence


def test_milestone_finding_fingerprints_the_pr_item_evidence() -> None:
    other_milestone = evaluate_scope(
        snapshot(items=(issue(), pull_item(milestone=8))), POLICY
    )[0]
    no_milestone = evaluate_scope(
        snapshot(items=(issue(), pull_item(milestone=None))), POLICY
    )[0]

    assert other_milestone.evidence != no_milestone.evidence
    assert other_milestone.evidence[-1].source_type == "github_milestone_item"
    assert _is_direct_github_issue_or_pr(other_milestone.evidence[-1].url)


@pytest.mark.parametrize(
    "corrupt_comparisons",
    [
        (),
        (comparison(143, base_sha="different-merge"),),
        (
            comparison(143),
            replace(
                comparison(143),
                comparison=replace(comparison(143).comparison, status="behind"),
            ),
        ),
    ],
)
def test_missing_or_ambiguous_comparison_does_not_fail_open(
    corrupt_comparisons: tuple[PullRequestComparison, ...],
) -> None:
    findings = evaluate_scope(snapshot(comparisons=corrupt_comparisons), POLICY)

    assert tuple(finding.rule_id for finding in findings) == (
        "scope.change_requires_candidate_inclusion",
    )


def test_candidate_finding_fingerprints_the_comparison_evidence() -> None:
    behind = evaluate_scope(
        snapshot(
            comparisons=(comparison(143, status="behind", merge_base_sha="older-sha"),)
        ),
        POLICY,
    )[0]
    diverged = evaluate_scope(
        snapshot(
            comparisons=(
                comparison(143, status="diverged", merge_base_sha="older-sha"),
            )
        ),
        POLICY,
    )[0]

    assert behind.evidence != diverged.evidence
    assert behind.evidence[-1].source_type == "github_commit_comparison"
    assert _is_direct_github_issue_or_pr(behind.evidence[-1].url)


def test_duplicate_comparison_evidence_is_idempotent() -> None:
    invalid = comparison(
        status="behind", merge_base_sha="1" * 40, head_sha=CANDIDATE_SHA
    )
    once = evaluate_scope(snapshot(comparisons=(invalid,)), POLICY)
    duplicated = evaluate_scope(snapshot(comparisons=(invalid, invalid)), POLICY)

    assert duplicated == once


@pytest.mark.parametrize(
    "corrupt",
    [
        lambda release: replace(
            release,
            comparisons=(
                replace(
                    release.comparisons[0],
                    comparison=replace(
                        release.comparisons[0].comparison,
                        url=(
                            "https://github.com/attacker/repo/compare/"
                            "merge-143...candidate-sha"
                        ),
                    ),
                ),
            ),
        ),
    ],
)
def test_candidate_evidence_must_be_bound_to_the_repository(
    corrupt,
) -> None:
    findings = evaluate_scope(corrupt(snapshot()), POLICY)

    assert tuple(finding.rule_id for finding in findings) == (
        "scope.change_requires_candidate_inclusion",
    )


def test_comparison_url_must_bind_exact_base_and_head_shas() -> None:
    release = snapshot()
    branch_url = f"https://github.com/{REPOSITORY}/compare/main...release/2026-08-10"
    release = replace(
        release,
        comparisons=(
            replace(
                release.comparisons[0],
                comparison=replace(
                    release.comparisons[0].comparison,
                    url=branch_url,
                ),
            ),
        ),
    )

    findings = evaluate_scope(release, POLICY)

    assert tuple(finding.rule_id for finding in findings) == (
        "scope.change_requires_candidate_inclusion",
    )


def test_identical_comparison_is_valid_only_for_the_candidate_commit() -> None:
    release = snapshot(
        pulls=(pull(merge_sha=CANDIDATE_SHA),),
        comparisons=(
            comparison(
                status="identical",
                base_sha=CANDIDATE_SHA,
                head_sha=CANDIDATE_SHA,
                merge_base_sha=CANDIDATE_SHA,
            ),
        ),
    )

    assert evaluate_scope(release, POLICY) == ()


@pytest.mark.parametrize(
    "invalid_comparison",
    [
        comparison(ahead_by=-1),
        comparison(behind_by=-1),
        comparison(total_commits=-1),
        comparison(total_commits=1),
        comparison(ahead_by=1),
        comparison(commits=()),
        comparison(
            commits=(
                GitHubCommit(
                    sha=MIDDLE_SHA,
                    url=f"https://github.com/{REPOSITORY}/commit/{MIDDLE_SHA}",
                    committed_at=NOW,
                ),
                GitHubCommit(
                    sha=MIDDLE_SHA,
                    url=f"https://github.com/{REPOSITORY}/commit/{MIDDLE_SHA}",
                    committed_at=NOW,
                ),
            )
        ),
        comparison(
            commits=(
                GitHubCommit(
                    sha=MIDDLE_SHA,
                    url=f"https://github.com/{REPOSITORY}/commit/{MIDDLE_SHA}",
                    committed_at=NOW,
                ),
                GitHubCommit(
                    sha="5" * 40,
                    url=f"https://github.com/{REPOSITORY}/commit/{'5' * 40}",
                    committed_at=NOW,
                ),
            )
        ),
        comparison(head_sha="5" * 40),
        comparison(merge_base_sha="6" * 40),
        comparison(
            commits=(
                GitHubCommit(
                    sha=MIDDLE_SHA,
                    url="https://github.com/attacker/repo/commit/" + MIDDLE_SHA,
                    committed_at=NOW,
                ),
                GitHubCommit(
                    sha=CANDIDATE_SHA,
                    url=f"https://github.com/{REPOSITORY}/commit/{CANDIDATE_SHA}",
                    committed_at=NOW,
                ),
            )
        ),
        comparison(
            commits=(
                GitHubCommit(
                    sha=MIDDLE_SHA,
                    url=f"https://github.com/{REPOSITORY}/commit/{MIDDLE_SHA}",
                    committed_at=NOW.replace(tzinfo=None),
                ),
                GitHubCommit(
                    sha=CANDIDATE_SHA,
                    url=f"https://github.com/{REPOSITORY}/commit/{CANDIDATE_SHA}",
                    committed_at=NOW,
                ),
            )
        ),
        comparison(status="diverged", behind_by=0),
        comparison(
            status="identical",
            base_sha=CANDIDATE_SHA,
            head_sha=CANDIDATE_SHA,
            merge_base_sha=CANDIDATE_SHA,
            ahead_by=1,
            total_commits=1,
            commits=(
                GitHubCommit(
                    sha=CANDIDATE_SHA,
                    url=f"https://github.com/{REPOSITORY}/commit/{CANDIDATE_SHA}",
                    committed_at=NOW,
                ),
            ),
        ),
    ],
)
def test_comparison_matrix_fails_closed(
    invalid_comparison: PullRequestComparison,
) -> None:
    release = snapshot(
        pulls=(
            pull(
                merge_sha=(
                    CANDIDATE_SHA
                    if invalid_comparison.comparison.status == "identical"
                    else BASE_SHA
                )
            ),
        ),
        comparisons=(invalid_comparison,),
    )

    findings = evaluate_scope(release, POLICY)

    assert tuple(finding.rule_id for finding in findings) == (
        "scope.change_requires_candidate_inclusion",
    )


def test_closed_and_foreign_milestone_issues_are_outside_scope() -> None:
    release = snapshot(
        items=(
            issue(141, labels=(), state="closed"),
            issue(142, labels=(), milestone=8),
        ),
        links=(),
        pulls=(),
        comparisons=(),
    )

    assert evaluate_scope(release, POLICY) == ()


def test_foreign_duplicate_does_not_make_current_issue_ambiguous() -> None:
    release = snapshot(
        items=(
            issue(labels=("code-change",)),
            issue(labels=(), milestone=8, source_id="foreign-record"),
        ),
        links=(),
        pulls=(),
        comparisons=(),
    )

    findings = evaluate_scope(release, POLICY)

    assert tuple(finding.rule_id for finding in findings) == (
        "scope.code_change_requires_pr",
    )


@pytest.mark.parametrize(
    "corrupt",
    [
        lambda release: replace(release, snapshot_version=SnapshotVersion.LEGACY),
        lambda release: replace(release, complete=False),
        lambda release: replace(
            release,
            source_errors=(SourceError(code="github.partial", message="partial"),),
        ),
        lambda release: replace(release, milestone_number=8),
        lambda release: replace(release, candidate_ref="release/2026-08-17"),
        lambda release: replace(release, candidate_sha=""),
        lambda release: replace(release, candidate_sha="not-a-sha"),
        lambda release: replace(release, candidate_sha=None),
        lambda release: replace(release, repository_id=""),
        lambda release: replace(release, repository_id=None),
        lambda release: replace(release, repository_full_name="invalid"),
        lambda release: replace(release, repository_full_name=None),
        lambda release: replace(release, fetch_started_at=None),
        lambda release: replace(release, fetched_at=None),
        lambda release: replace(release, fetch_started_at=NOW.replace(tzinfo=None)),
        lambda release: replace(release, fetch_started_at="invalid"),
        lambda release: replace(release, fetch_started_at=NOW + timedelta(minutes=1)),
    ],
)
def test_invalid_snapshot_prerequisites_do_not_infer_absence_findings(corrupt) -> None:
    missing_pr = snapshot(items=(issue(),), links=(), pulls=(), comparisons=())

    assert evaluate_scope(corrupt(missing_pr), POLICY) == ()


MALFORMED_URLS = [
    "https://user:secret@github.com/example/release-intelligence/pull/143",
    "https://github.com:443/example/release-intelligence/pull/143",
    "https://github.com:bad/example/release-intelligence/pull/143",
    "https://[::1/example/release-intelligence/pull/143",
    "https://\ud800.github.com/example/release-intelligence/pull/143",
]


@pytest.mark.parametrize("malformed_url", MALFORMED_URLS)
@pytest.mark.parametrize(
    ("corrupt", "expected_rule"),
    [
        (
            lambda release, url: replace(release, links=(link(url=url),)),
            "scope.code_change_requires_pr",
        ),
        (
            lambda release, url: replace(release, pull_requests=(pull(url=url),)),
            "scope.pr_requires_milestone",
        ),
        (
            lambda release, url: replace(release, items=(issue(), pull_item(url=url))),
            "scope.pr_requires_milestone",
        ),
        (
            lambda release, url: replace(release, items=(issue(url=url), pull_item())),
            "scope.exactly_one_type",
        ),
        (
            lambda release, url: replace(release, comparisons=(comparison(url=url),)),
            "scope.change_requires_candidate_inclusion",
        ),
    ],
)
def test_malformed_evidence_urls_never_crash_or_pass(
    malformed_url: str, corrupt, expected_rule: str
) -> None:
    findings = evaluate_scope(corrupt(snapshot(), malformed_url), POLICY)

    assert tuple(finding.rule_id for finding in findings) == (expected_rule,)
    assert all(
        _is_direct_github_issue_or_pr(ref.url)
        for finding in findings
        for ref in finding.evidence
    )


@pytest.mark.parametrize(
    "repository",
    ["owner", "owner/repo/extra", "../repo", "owner/repo@attacker", "\ud800/repo"],
)
def test_invalid_repository_identity_never_synthesizes_evidence(
    repository: str,
) -> None:
    missing_pr = replace(
        snapshot(items=(issue(),), links=(), pulls=(), comparisons=()),
        repository_full_name=repository,
    )

    assert evaluate_scope(missing_pr, POLICY) == ()


def test_duplicate_supported_label_is_one_logical_type() -> None:
    release = snapshot(
        items=(issue(labels=("CODE-CHANGE", "code-change")), pull_item()),
    )

    assert evaluate_scope(release, POLICY) == ()


def test_scope_uses_policy_unicode_lower_semantics_for_labels() -> None:
    unicode_policy = POLICY.model_copy(
        update={"code_change_label": "straße", "release_ops_label": "release-ops"}
    )
    release = snapshot(
        items=(issue(labels=("STRASSE",)),),
        links=(),
        pulls=(),
        comparisons=(),
    )

    findings = evaluate_scope(release, unicode_policy)

    assert tuple(finding.rule_id for finding in findings) == ("scope.exactly_one_type",)


def test_unrelated_labels_do_not_hide_code_change_requirements() -> None:
    release = snapshot(
        items=(issue(labels=("code-change", "release-blocker", "backend")),),
        links=(),
        pulls=(),
        comparisons=(),
    )

    findings = evaluate_scope(release, POLICY)

    assert tuple(finding.rule_id for finding in findings) == (
        "scope.code_change_requires_pr",
    )


def test_input_snapshot_is_not_mutated() -> None:
    release = snapshot()
    before = repr(release)

    result = evaluate_scope(release, POLICY)

    assert result == ()
    assert repr(release) == before


def _is_direct_github_issue_or_pr(url: str) -> bool:
    parsed = urlparse(url)
    parts = parsed.path.strip("/").split("/")
    return (
        parsed.scheme == "https"
        and parsed.netloc == "github.com"
        and len(parts) == 4
        and parts[2] in {"issues", "pull"}
        and parts[3].isdigit()
        and not parsed.query
        and not parsed.fragment
    )
