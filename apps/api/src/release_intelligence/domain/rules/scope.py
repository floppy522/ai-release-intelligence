from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import asdict
from datetime import datetime
from urllib.parse import urlparse

from release_intelligence.domain.models import (
    EvidenceRef,
    PullRequestComparison,
    ReadinessFinding,
    ReleaseLink,
    ReleaseSnapshot,
    SnapshotVersion,
)
from release_intelligence.domain.policy import ReleasePolicy
from release_intelligence.ports.github import (
    CommitComparison,
    GitHubItem,
    GitHubItemKind,
    GitHubPullRequest,
)

_REPOSITORY_PART = re.compile(r"^[A-Za-z0-9_.-]+$")
_SHA = re.compile(r"^[0-9a-f]{40}$")


def evaluate_scope(
    snapshot: ReleaseSnapshot, policy: ReleasePolicy
) -> tuple[ReadinessFinding, ...]:
    """Evaluate milestone membership as an ordered set of existential PR chains."""
    if not _has_valid_prerequisites(snapshot, policy):
        return ()
    issues = _issue_records(snapshot.items, snapshot.milestone_number)
    typed_issues = _unambiguously_typed_issues(
        issues, policy, snapshot.repository_full_name
    )
    code_change_issues = tuple(
        item
        for item in typed_issues
        if policy.code_change_label.lower() in _normalized_labels(item.labels)
    )

    findings: list[ReadinessFinding] = []
    findings.extend(_evaluate_type_labels(snapshot, policy, issues))
    findings.extend(_evaluate_pr_links(snapshot, code_change_issues))
    findings.extend(_evaluate_pr_milestones(snapshot, policy, code_change_issues))
    findings.extend(_evaluate_main_merge(snapshot, policy, code_change_issues))
    findings.extend(_evaluate_candidate_inclusion(snapshot, policy, code_change_issues))
    return tuple(findings)


def _evaluate_type_labels(
    snapshot: ReleaseSnapshot,
    policy: ReleasePolicy,
    issues: dict[int, tuple[GitHubItem, ...]],
) -> tuple[ReadinessFinding, ...]:
    supported = {
        policy.code_change_label.lower(),
        policy.release_ops_label.lower(),
    }
    findings: list[ReadinessFinding] = []
    for number, records in issues.items():
        item = records[0]
        type_labels = _normalized_labels(item.labels).intersection(supported)
        if (
            len(records) != 1
            or len(type_labels) != 1
            or not _is_direct_url(
                item.url,
                snapshot.repository_full_name,
                "issues",
                item.number,
            )
        ):
            findings.append(
                _finding(
                    "scope.exactly_one_type",
                    f"Issue #{number} must have exactly one supported type label",
                    (
                        f"Apply exactly one of '{policy.code_change_label}' or "
                        f"'{policy.release_ops_label}' to Issue #{number}"
                    ),
                    tuple(_issue_evidence(snapshot, record) for record in records),
                )
            )
    return tuple(findings)


def _evaluate_pr_links(
    snapshot: ReleaseSnapshot, issues: tuple[GitHubItem, ...]
) -> tuple[ReadinessFinding, ...]:
    return tuple(
        _finding(
            "scope.code_change_requires_pr",
            f"Issue #{item.number} has no unambiguous linked PR",
            f"Link a PR to Issue #{item.number}",
            _link_failure_evidence(snapshot, item),
        )
        for item in issues
        if not _linked_pull_numbers(snapshot, item.number)
    )


def _evaluate_pr_milestones(
    snapshot: ReleaseSnapshot,
    policy: ReleasePolicy,
    issues: tuple[GitHubItem, ...],
) -> tuple[ReadinessFinding, ...]:
    findings: list[ReadinessFinding] = []
    for item in issues:
        linked = _linked_pull_numbers(snapshot, item.number)
        if not linked:
            continue
        milestone_pulls = _milestone_pulls(snapshot, policy, linked)
        if not milestone_pulls:
            findings.append(
                _finding(
                    "scope.pr_requires_milestone",
                    f"Issue #{item.number} has no linked PR in Milestone {snapshot.milestone_number}",
                    (
                        f"Add a linked PR for Issue #{item.number} to Milestone "
                        f"{snapshot.milestone_number}"
                    ),
                    _chain_evidence(snapshot, item, linked),
                )
            )
    return tuple(findings)


def _evaluate_main_merge(
    snapshot: ReleaseSnapshot,
    policy: ReleasePolicy,
    issues: tuple[GitHubItem, ...],
) -> tuple[ReadinessFinding, ...]:
    findings: list[ReadinessFinding] = []
    for item in issues:
        linked = _linked_pull_numbers(snapshot, item.number)
        milestone_pulls = _milestone_pulls(snapshot, policy, linked)
        if not linked or not milestone_pulls:
            continue
        if not _main_merged_pulls(policy, milestone_pulls):
            findings.append(
                _finding(
                    "scope.pr_requires_main_merge",
                    f"Issue #{item.number} has no milestone PR merged into {policy.main_branch}",
                    (
                        f"Merge a milestone PR for Issue #{item.number} into "
                        f"{policy.main_branch}"
                    ),
                    _chain_evidence(snapshot, item, linked),
                )
            )
    return tuple(findings)


def _evaluate_candidate_inclusion(
    snapshot: ReleaseSnapshot,
    policy: ReleasePolicy,
    issues: tuple[GitHubItem, ...],
) -> tuple[ReadinessFinding, ...]:
    findings: list[ReadinessFinding] = []
    for item in issues:
        linked = _linked_pull_numbers(snapshot, item.number)
        milestone_pulls = _milestone_pulls(snapshot, policy, linked)
        merged_pulls = _main_merged_pulls(policy, milestone_pulls)
        if not linked or not milestone_pulls or not merged_pulls:
            continue
        if not any(_is_in_candidate(snapshot, policy, pull) for pull in merged_pulls):
            findings.append(
                _finding(
                    "scope.change_requires_candidate_inclusion",
                    f"Issue #{item.number} has no merged change in {policy.candidate_branch}",
                    (
                        f"Include a merged PR for Issue #{item.number} in "
                        f"{policy.candidate_branch}"
                    ),
                    _candidate_chain_evidence(
                        snapshot,
                        item,
                        merged_pulls,
                    ),
                )
            )
    return tuple(findings)


def _has_valid_prerequisites(snapshot: ReleaseSnapshot, policy: ReleasePolicy) -> bool:
    if (
        snapshot.snapshot_version is not SnapshotVersion.GITHUB_V1
        or not snapshot.complete
        or snapshot.source_errors
        or snapshot.milestone_number != policy.milestone_number
        or snapshot.candidate_ref != policy.candidate_branch
        or not isinstance(snapshot.repository_id, str)
        or not snapshot.repository_id.strip()
        or not _is_repository_name(snapshot.repository_full_name)
        or not _is_sha(snapshot.candidate_sha)
        or not _has_valid_evidence_identity(snapshot)
    ):
        return False
    started_at = snapshot.fetch_started_at
    fetched_at = snapshot.fetched_at
    return (
        isinstance(started_at, datetime)
        and isinstance(fetched_at, datetime)
        and started_at.tzinfo is not None
        and fetched_at.tzinfo is not None
        and started_at <= fetched_at
    )


def _has_valid_evidence_identity(snapshot: ReleaseSnapshot) -> bool:
    repository = snapshot.repository_full_name
    issue_numbers = {
        item.number for item in snapshot.items if item.kind is GitHubItemKind.ISSUE
    }
    pull_numbers = {pull.number for pull in snapshot.pull_requests}
    for item in snapshot.items:
        if item.kind is GitHubItemKind.PULL_REQUEST:
            if not _is_direct_url(item.url, repository, "pull", item.number):
                return False
        elif (
            item.state.lower() == "open"
            and item.milestone_number == snapshot.milestone_number
            and not _is_direct_url(item.url, repository, "issues", item.number)
        ):
            return False
    if any(
        link.issue_number not in issue_numbers
        or not _is_direct_url(link.url, repository, "pull", link.pull_request_number)
        for link in snapshot.links
    ):
        return False
    if any(
        not _is_direct_url(pull.url, repository, "pull", pull.number)
        for pull in snapshot.pull_requests
    ):
        return False
    for relation in snapshot.comparisons:
        comparison = relation.comparison
        if (
            relation.pull_request_number not in pull_numbers
            or not _is_sha(comparison.base_sha)
            or not _is_sha(comparison.merge_base_sha)
            or not _is_sha(comparison.head_sha)
            or comparison.head_sha != snapshot.candidate_sha
            or not _is_comparison_url(
                comparison.url,
                repository,
                comparison.base_sha,
                comparison.head_sha,
            )
            or any(
                not _is_sha(commit.sha)
                or not _is_commit_url(commit.url, repository, commit.sha)
                for commit in comparison.commits
            )
        ):
            return False
    return True


def _issue_records(
    items: Iterable[GitHubItem], milestone_number: int
) -> dict[int, tuple[GitHubItem, ...]]:
    grouped: defaultdict[int, dict[str, GitHubItem]] = defaultdict(dict)
    for item in items:
        if (
            item.kind is GitHubItemKind.ISSUE
            and item.state.lower() == "open"
            and item.milestone_number == milestone_number
        ):
            key = _full_item_sort_key(item)
            current = grouped[item.number].get(key)
            if current is None or repr(item) < repr(current):
                grouped[item.number][key] = item
    return {
        number: tuple(sorted(records.values(), key=_item_sort_key))
        for number, records in sorted(grouped.items())
    }


def _unambiguously_typed_issues(
    issues: dict[int, tuple[GitHubItem, ...]],
    policy: ReleasePolicy,
    repository: str,
) -> tuple[GitHubItem, ...]:
    supported = {
        policy.code_change_label.lower(),
        policy.release_ops_label.lower(),
    }
    return tuple(
        records[0]
        for records in issues.values()
        if len(records) == 1
        and len(_normalized_labels(records[0].labels).intersection(supported)) == 1
        and _is_direct_url(
            records[0].url,
            repository,
            "issues",
            records[0].number,
        )
    )


def _linked_pull_numbers(
    snapshot: ReleaseSnapshot, issue_number: int
) -> tuple[int, ...]:
    grouped: defaultdict[int, dict[str, ReleaseLink]] = defaultdict(dict)
    for release_link in snapshot.links:
        if release_link.issue_number == issue_number:
            key = _full_link_sort_key(release_link)
            current = grouped[release_link.pull_request_number].get(key)
            if current is None or repr(release_link) < repr(current):
                grouped[release_link.pull_request_number][key] = release_link
    valid: list[int] = []
    for pull_number, records in sorted(grouped.items()):
        if len(records) != 1:
            continue
        record = next(iter(records.values()))
        if _is_direct_url(
            record.url, snapshot.repository_full_name, "pull", pull_number
        ):
            valid.append(pull_number)
    return tuple(valid)


def _milestone_pulls(
    snapshot: ReleaseSnapshot,
    policy: ReleasePolicy,
    linked: Sequence[int],
) -> tuple[GitHubPullRequest, ...]:
    pull_records = _unique_pulls(snapshot.pull_requests)
    pull_items = _unique_items(
        item for item in snapshot.items if item.kind is GitHubItemKind.PULL_REQUEST
    )
    milestone = snapshot.milestone_number
    if milestone != policy.milestone_number:
        return ()
    valid: list[GitHubPullRequest] = []
    for number in linked:
        pull_record = pull_records.get(number)
        pull_item = pull_items.get(number)
        if pull_record is None or pull_item is None:
            continue
        if (
            pull_record.milestone_number != milestone
            or pull_item.milestone_number != milestone
            or not _is_direct_url(
                pull_record.url, snapshot.repository_full_name, "pull", number
            )
            or not _is_direct_url(
                pull_item.url, snapshot.repository_full_name, "pull", number
            )
        ):
            continue
        valid.append(pull_record)
    return tuple(sorted(valid, key=_pull_sort_key))


def _main_merged_pulls(
    policy: ReleasePolicy, pulls: Iterable[GitHubPullRequest]
) -> tuple[GitHubPullRequest, ...]:
    return tuple(
        pull
        for pull in pulls
        if pull.state.casefold() == "closed"
        and pull.base_ref == policy.main_branch
        and pull.merged_at is not None
        and pull.merged_at.tzinfo is not None
        and pull.merge_commit_sha is not None
        and bool(pull.merge_commit_sha.strip())
    )


def _is_in_candidate(
    snapshot: ReleaseSnapshot,
    policy: ReleasePolicy,
    pull: GitHubPullRequest,
) -> bool:
    if snapshot.candidate_ref != policy.candidate_branch or not snapshot.candidate_sha:
        return False
    comparisons = _unique_comparisons(snapshot.comparisons)
    candidate = comparisons.get(pull.number)
    if candidate is None or pull.merge_commit_sha is None:
        return False
    comparison = candidate.comparison
    return _is_valid_comparison(snapshot, pull.merge_commit_sha, comparison)


def _is_valid_comparison(
    snapshot: ReleaseSnapshot,
    merge_commit_sha: str,
    comparison: CommitComparison,
) -> bool:
    counts = (
        comparison.ahead_by,
        comparison.behind_by,
        comparison.total_commits,
    )
    if (
        not _is_sha(merge_commit_sha)
        or not _is_sha(comparison.base_sha)
        or not _is_sha(comparison.merge_base_sha)
        or not _is_sha(comparison.head_sha)
        or comparison.base_sha != merge_commit_sha
        or comparison.merge_base_sha != comparison.base_sha
        or comparison.head_sha != snapshot.candidate_sha
        or any(
            isinstance(count, bool) or not isinstance(count, int) or count < 0
            for count in counts
        )
        or not _is_comparison_url(
            comparison.url,
            snapshot.repository_full_name,
            comparison.base_sha,
            comparison.head_sha,
        )
    ):
        return False
    if comparison.status == "identical":
        return (
            comparison.base_sha == comparison.head_sha
            and comparison.ahead_by == 0
            and comparison.behind_by == 0
            and comparison.total_commits == 0
            and not comparison.commits
        )
    if comparison.status != "ahead":
        return False
    commits = comparison.commits
    if (
        comparison.behind_by != 0
        or comparison.ahead_by <= 0
        or comparison.total_commits != comparison.ahead_by
        or comparison.total_commits != len(commits)
        or not commits
        or sum(commit.sha == comparison.head_sha for commit in commits) != 1
        or len({commit.sha for commit in commits}) != len(commits)
    ):
        return False
    return all(
        _is_sha(commit.sha)
        and commit.committed_at.tzinfo is not None
        and _is_commit_url(
            commit.url,
            snapshot.repository_full_name,
            commit.sha,
        )
        for commit in commits
    )


def _unique_items(
    records: Iterable[GitHubItem],
) -> dict[int, GitHubItem | None]:
    grouped: defaultdict[int, dict[str, GitHubItem]] = defaultdict(dict)
    for record in records:
        key = _full_item_sort_key(record)
        current = grouped[record.number].get(key)
        if current is None or repr(record) < repr(current):
            grouped[record.number][key] = record
    return {
        number: next(iter(values.values())) if len(values) == 1 else None
        for number, values in grouped.items()
    }


def _unique_pulls(
    records: Iterable[GitHubPullRequest],
) -> dict[int, GitHubPullRequest | None]:
    grouped: defaultdict[int, dict[str, GitHubPullRequest]] = defaultdict(dict)
    for record in records:
        key = _full_pull_sort_key(record)
        current = grouped[record.number].get(key)
        if current is None or repr(record) < repr(current):
            grouped[record.number][key] = record
    return {
        number: next(iter(values.values())) if len(values) == 1 else None
        for number, values in grouped.items()
    }


def _unique_comparisons(
    records: Iterable[PullRequestComparison],
) -> dict[int, PullRequestComparison | None]:
    grouped: defaultdict[int, dict[str, PullRequestComparison]] = defaultdict(dict)
    for record in records:
        key = _full_comparison_sort_key(record)
        current = grouped[record.pull_request_number].get(key)
        if current is None or repr(record) < repr(current):
            grouped[record.pull_request_number][key] = record
    return {
        number: next(iter(values.values())) if len(values) == 1 else None
        for number, values in grouped.items()
    }


def _chain_evidence(
    snapshot: ReleaseSnapshot,
    item: GitHubItem,
    pull_numbers: Sequence[int],
) -> tuple[EvidenceRef, ...]:
    evidence = [_issue_evidence(snapshot, item)]
    grouped: defaultdict[int, dict[str, GitHubPullRequest]] = defaultdict(dict)
    for pull in snapshot.pull_requests:
        grouped[pull.number][_full_pull_sort_key(pull)] = pull
    item_records: defaultdict[int, dict[str, GitHubItem]] = defaultdict(dict)
    for candidate in snapshot.items:
        if candidate.kind is GitHubItemKind.PULL_REQUEST:
            item_records[candidate.number][_full_item_sort_key(candidate)] = candidate
    for number in sorted(set(pull_numbers)):
        pull_records = sorted(grouped.get(number, {}).values(), key=_full_pull_sort_key)
        if pull_records:
            evidence.extend(
                _pull_evidence(snapshot, pull_record) for pull_record in pull_records
            )
        else:
            evidence.append(_linked_pull_evidence(snapshot, number))
        milestone_items = sorted(
            item_records.get(number, {}).values(), key=_full_item_sort_key
        )
        if milestone_items:
            evidence.extend(
                _pr_item_evidence(snapshot, milestone_item)
                for milestone_item in milestone_items
            )
        else:
            evidence.append(_missing_pr_item_evidence(snapshot, number))
    return tuple(evidence)


def _candidate_chain_evidence(
    snapshot: ReleaseSnapshot,
    item: GitHubItem,
    pulls: Sequence[GitHubPullRequest],
) -> tuple[EvidenceRef, ...]:
    evidence = list(
        _chain_evidence(snapshot, item, tuple(pull.number for pull in pulls))
    )
    evidence.extend(
        _comparison_evidence(snapshot, pull.number)
        for pull in sorted(pulls, key=_pull_sort_key)
    )
    return tuple(evidence)


def _link_failure_evidence(
    snapshot: ReleaseSnapshot, item: GitHubItem
) -> tuple[EvidenceRef, ...]:
    evidence = [_issue_evidence(snapshot, item)]
    canonical_links = {
        _full_link_sort_key(release_link): release_link
        for release_link in snapshot.links
        if release_link.issue_number == item.number
    }
    links = sorted(canonical_links.values(), key=_full_link_sort_key)
    evidence.extend(_link_evidence(snapshot, release_link) for release_link in links)
    return tuple(evidence)


def _issue_evidence(snapshot: ReleaseSnapshot, item: GitHubItem) -> EvidenceRef:
    return _evidence(
        evidence_id=f"github-issue-{item.source_id}",
        source_type="github_issue",
        source_id=item.source_id,
        url=_direct_url(snapshot.repository_full_name, "issues", item.number),
        facts={"item": _item_facts(item)},
    )


def _pull_evidence(snapshot: ReleaseSnapshot, pull: GitHubPullRequest) -> EvidenceRef:
    return _evidence(
        evidence_id=f"github-pull-{pull.source_id}",
        source_type="github_pull_request",
        source_id=pull.source_id,
        url=_direct_url(snapshot.repository_full_name, "pull", pull.number),
        facts={"pull": _pull_facts(pull)},
    )


def _linked_pull_evidence(snapshot: ReleaseSnapshot, pull_number: int) -> EvidenceRef:
    return _evidence(
        evidence_id=f"github-pull-{pull_number}",
        source_type="github_pull_request",
        source_id=str(pull_number),
        url=_direct_url(snapshot.repository_full_name, "pull", pull_number),
        facts={"pull_number": pull_number, "missing": True},
    )


def _comparison_evidence(snapshot: ReleaseSnapshot, pull_number: int) -> EvidenceRef:
    canonical = {
        _full_comparison_sort_key(comparison): comparison
        for comparison in snapshot.comparisons
        if comparison.pull_request_number == pull_number
    }
    records = sorted(canonical.values(), key=_full_comparison_sort_key)
    return _evidence(
        evidence_id=f"github-comparison-pr-{pull_number}",
        source_type="github_commit_comparison",
        source_id=str(pull_number),
        url=_direct_url(snapshot.repository_full_name, "pull", pull_number),
        facts={
            "candidate_sha": snapshot.candidate_sha,
            "comparisons": [_jsonable(asdict(record)) for record in records],
        },
    )


def _link_evidence(snapshot: ReleaseSnapshot, release_link: ReleaseLink) -> EvidenceRef:
    return _evidence(
        evidence_id=f"github-issue-pr-link-{release_link.source_id}",
        source_type="github_issue_pr_link",
        source_id=release_link.source_id,
        url=_direct_url(
            snapshot.repository_full_name,
            "pull",
            release_link.pull_request_number,
        ),
        facts={"link": _jsonable(asdict(release_link))},
    )


def _pr_item_evidence(snapshot: ReleaseSnapshot, item: GitHubItem) -> EvidenceRef:
    return _evidence(
        evidence_id=f"github-milestone-item-{item.source_id}",
        source_type="github_milestone_item",
        source_id=item.source_id,
        url=_direct_url(snapshot.repository_full_name, "pull", item.number),
        facts={"item": _item_facts(item)},
    )


def _missing_pr_item_evidence(
    snapshot: ReleaseSnapshot, pull_number: int
) -> EvidenceRef:
    return _evidence(
        evidence_id=f"github-milestone-item-pr-{pull_number}",
        source_type="github_milestone_item",
        source_id=str(pull_number),
        url=_direct_url(snapshot.repository_full_name, "pull", pull_number),
        facts={
            "milestone_number": snapshot.milestone_number,
            "pull_number": pull_number,
            "missing": True,
        },
    )


def _evidence(
    *,
    evidence_id: str,
    source_type: str,
    source_id: str,
    url: str,
    facts: dict[str, object],
) -> EvidenceRef:
    payload = json.dumps(facts, sort_keys=True, separators=(",", ":"))
    fingerprint = "sha256:" + hashlib.sha256(payload.encode()).hexdigest()
    return EvidenceRef(
        evidence_id=evidence_id,
        source_type=source_type,
        source_id=source_id,
        url=url,
        fingerprint=fingerprint,
    )


def _finding(
    rule_id: str,
    summary: str,
    required_action: str,
    evidence: tuple[EvidenceRef, ...],
) -> ReadinessFinding:
    return ReadinessFinding(
        rule_id=rule_id,
        severity="BLOCKING",
        summary=summary,
        required_action=required_action,
        evidence=evidence,
    )


def _direct_url(repository: str, kind: str, number: int) -> str:
    return f"https://github.com/{repository}/{kind}/{number}"


def _is_direct_url(url: str, repository: str, kind: str, number: int) -> bool:
    return _is_canonical_github_url(url, f"/{repository}/{kind}/{number}")


def _is_comparison_url(url: str, repository: str, base_sha: str, head_sha: str) -> bool:
    return _is_canonical_github_url(
        url, f"/{repository}/compare/{base_sha}...{head_sha}"
    )


def _is_commit_url(url: str, repository: str, sha: str) -> bool:
    return _is_canonical_github_url(url, f"/{repository}/commit/{sha}")


def _is_canonical_github_url(url: str, expected_path: str) -> bool:
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        port = parsed.port
        username = parsed.username
        password = parsed.password
    except (UnicodeError, ValueError):
        return False
    return (
        parsed.scheme == "https"
        and parsed.netloc == "github.com"
        and hostname == "github.com"
        and port is None
        and username is None
        and password is None
        and parsed.path == expected_path
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


def _is_repository_name(repository: object) -> bool:
    if not isinstance(repository, str):
        return False
    parts = repository.split("/")
    return len(parts) == 2 and all(
        bool(part)
        and part not in {".", ".."}
        and _REPOSITORY_PART.fullmatch(part) is not None
        for part in parts
    )


def _is_sha(value: object) -> bool:
    return isinstance(value, str) and _SHA.fullmatch(value) is not None


def _normalized_labels(labels: Iterable[str]) -> set[str]:
    return {label.strip().lower() for label in labels if label.strip()}


def _item_sort_key(item: GitHubItem) -> tuple[object, ...]:
    return (
        item.source_id,
        item.url,
        item.state,
        tuple(_canonical_strings(item.labels)),
        item.updated_at,
    )


def _pull_sort_key(pull: GitHubPullRequest) -> tuple[object, ...]:
    return (pull.number, pull.source_id, pull.url, pull.updated_at)


def _full_pull_sort_key(pull: GitHubPullRequest) -> str:
    return json.dumps(_pull_facts(pull), sort_keys=True, separators=(",", ":"))


def _full_item_sort_key(item: GitHubItem) -> str:
    return json.dumps(_item_facts(item), sort_keys=True, separators=(",", ":"))


def _full_comparison_sort_key(comparison: PullRequestComparison) -> str:
    return json.dumps(
        _jsonable(asdict(comparison)), sort_keys=True, separators=(",", ":")
    )


def _full_link_sort_key(release_link: ReleaseLink) -> str:
    return json.dumps(
        _jsonable(asdict(release_link)), sort_keys=True, separators=(",", ":")
    )


def _item_facts(item: GitHubItem) -> object:
    facts = asdict(item)
    facts["labels"] = _canonical_strings(item.labels)
    facts["assignees"] = _canonical_strings(item.assignees)
    return _jsonable(facts)


def _pull_facts(pull: GitHubPullRequest) -> object:
    facts = asdict(pull)
    facts["labels"] = _canonical_strings(pull.labels)
    facts["assignees"] = _canonical_strings(pull.assignees)
    return _jsonable(facts)


def _canonical_strings(values: Iterable[str]) -> list[str]:
    return sorted(set(values), key=lambda value: (value.lower(), value))


def _jsonable(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value
