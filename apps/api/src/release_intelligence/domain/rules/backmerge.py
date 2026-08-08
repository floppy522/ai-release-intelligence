from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime

from release_intelligence.domain.models import (
    EvidenceRef,
    ReadinessFinding,
    ReleaseLink,
    ReleaseSnapshot,
)
from release_intelligence.domain.policy import PolicyValidationError, ReleasePolicy
from release_intelligence.domain.rules.blockers import (
    _analyze_issue_evidence,
    _is_bounded_decimal,
    _is_issue_number,
    _valid_strings,
    _valid_timestamps,
)
from release_intelligence.domain.rules.scope import (
    _full_item_sort_key,
    _full_link_sort_key,
    _full_pull_sort_key,
    _is_direct_url,
    _issue_evidence,
    _link_evidence,
    _pr_item_evidence,
    _prerequisite_error_codes,
    _pull_evidence,
)
from release_intelligence.ports.github import (
    GitHubItem,
    GitHubItemKind,
    GitHubPullRequest,
)

_MAX_BRANCH_LENGTH = 255
_MAX_ITEMS = 100
_MAX_LINKS = 200
_MAX_PULLS = 200
_MAX_URL_LENGTH = 2_048
_MAX_FINDING_EVIDENCE = 10
_STATES = frozenset({"open", "closed"})


class BackmergeEvidenceError(Exception):
    """Signal incomplete back-merge evidence while retaining proven blockers."""

    def __init__(
        self, *, findings: tuple[ReadinessFinding, ...], codes: Iterable[str]
    ) -> None:
        super().__init__("Previous-release back-merge evidence is incomplete")
        self.findings = findings
        self.codes = tuple(sorted(set(codes)))


def evaluate_backmerge(
    snapshot: ReleaseSnapshot, policy: ReleasePolicy
) -> tuple[ReadinessFinding, ...]:
    """Require Issue-related main PRs for prior-release branch PRs.

    Relations, not commit identity, connect a previous-release PR to its main PR.
    Invalid evidence quarantines only the entity chains that depend on it.
    """

    previous_milestone = policy.previous_milestone_number
    previous_branch = policy.previous_release_branch
    if previous_milestone is None or previous_branch is None:
        raise PolicyValidationError(
            "previous release milestone and branch are required for back-merge evaluation"
        )

    prerequisite_codes = list(_prerequisite_error_codes(snapshot, policy))
    if (
        snapshot.previous_milestone_number is None
        or snapshot.previous_release_branch is None
    ):
        prerequisite_codes.append("snapshot.previous_release_context_missing")
    elif (
        snapshot.previous_milestone_number != previous_milestone
        or snapshot.previous_release_branch != previous_branch
    ):
        prerequisite_codes.append("snapshot.previous_release_context_mismatch")
    if prerequisite_codes:
        raise BackmergeEvidenceError(findings=(), codes=prerequisite_codes)

    findings, codes = _evaluate(snapshot, policy, previous_milestone, previous_branch)
    if codes:
        raise BackmergeEvidenceError(findings=findings, codes=codes)
    return findings


def _evaluate(
    snapshot: ReleaseSnapshot,
    policy: ReleasePolicy,
    previous_milestone: int,
    previous_branch: str,
) -> tuple[tuple[ReadinessFinding, ...], tuple[str, ...]]:
    codes: list[str] = []
    if len(snapshot.items) > _MAX_ITEMS:
        codes.append("snapshot.too_many_items")
    if len(snapshot.links) > _MAX_LINKS:
        codes.append("snapshot.too_many_links")
    if len(snapshot.pull_requests) > _MAX_PULLS:
        codes.append("snapshot.too_many_pulls")
    if codes:
        return (), tuple(codes)

    issue_state = _analyze_issue_evidence(snapshot)
    codes.extend(issue_state.codes)
    codes.extend(
        f"issue.invalid_assignees:{number}"
        for number in issue_state.invalid_owner_numbers
    )
    codes.extend(
        f"issue.invalid_body:{number}" for number in issue_state.invalid_body_numbers
    )
    issues = {item.number: item for item in issue_state.items}
    invalid_issue_numbers = frozenset(
        {
            *_issue_numbers_from_codes(issue_state.codes),
            *issue_state.invalid_owner_numbers,
            *issue_state.invalid_body_numbers,
        }
    )

    aliased_items, item_alias_codes = _item_source_aliases(snapshot.items)
    aliased_pulls, pull_alias_codes = _pull_source_aliases(snapshot.pull_requests)
    pull_items, invalid_pull_items, item_codes = _analyze_pull_items(snapshot)
    pulls, invalid_pulls, pull_codes = _analyze_pulls(snapshot)
    (
        links,
        invalid_link_pairs,
        uncertain_link_issues,
        uncertain_link_pulls,
        link_codes,
    ) = _analyze_links(snapshot, issues)
    codes.extend(item_alias_codes)
    codes.extend(pull_alias_codes)
    codes.extend(item_codes)
    codes.extend(pull_codes)
    codes.extend(link_codes)
    invalid_issue_numbers = frozenset(
        {*invalid_issue_numbers, *(issues.keys() & aliased_items)}
    )
    invalid_pull_numbers = {
        *invalid_pull_items,
        *invalid_pulls,
        *aliased_items,
        *aliased_pulls,
    }
    for number in sorted(pull_items.keys() & pulls.keys()):
        if pull_items[number].source_id != pulls[number].source_id:
            codes.append(f"pull.source_id_mismatch:{number}")
            invalid_pull_numbers.add(number)
        if pull_items[number].state != pulls[number].state:
            codes.append(f"pull.state_mismatch:{number}")
            invalid_pull_numbers.add(number)

    links_by_pull: defaultdict[int, list[ReleaseLink]] = defaultdict(list)
    links_by_issue: defaultdict[int, list[ReleaseLink]] = defaultdict(list)
    for relation in links:
        links_by_pull[relation.pull_request_number].append(relation)
        links_by_issue[relation.issue_number].append(relation)

    findings: list[ReadinessFinding] = []
    candidate_numbers: list[int] = []
    for number, item in sorted(pull_items.items()):
        if item.milestone_number != previous_milestone:
            continue
        if number in invalid_pull_numbers:
            continue
        pull = pulls.get(number)
        if pull is None:
            codes.append(f"pull.missing_record:{number}")
            continue
        if not _is_merged_into(pull, previous_branch):
            continue
        if item.state != "closed":
            codes.append(f"pull_item.invalid_merge_state:{number}")
            continue
        candidate_numbers.append(number)

    for candidate_number in candidate_numbers:
        candidate = pulls[candidate_number]
        candidate_item = pull_items[candidate_number]
        shipped_issues: list[GitHubItem] = []
        chain_uncertain = (
            any(
                pull_number == candidate_number
                for _issue_number, pull_number in invalid_link_pairs
            )
            or candidate_number in uncertain_link_pulls
        )
        for relation in links_by_pull.get(candidate_number, ()):  # bounded above
            pair = (relation.issue_number, relation.pull_request_number)
            if pair in invalid_link_pairs:
                chain_uncertain = True
                continue
            if relation.issue_number in invalid_issue_numbers:
                chain_uncertain = True
                continue
            linked_issue = issues.get(relation.issue_number)
            if linked_issue is None:
                chain_uncertain = True
                continue
            if linked_issue.milestone_number == previous_milestone:
                shipped_issues.append(linked_issue)

        if not shipped_issues:
            if not chain_uncertain:
                findings.append(
                    _missing_issue_relation_finding(snapshot, candidate, candidate_item)
                )
            continue

        unique_shipped = {item.number: item for item in shipped_issues}
        for issue_number, item in sorted(unique_shipped.items()):
            related_pulls: list[GitHubPullRequest] = []
            issue_uncertain = issue_number in uncertain_link_issues
            for relation in links_by_issue.get(issue_number, ()):  # bounded above
                pair = (relation.issue_number, relation.pull_request_number)
                pull_number = relation.pull_request_number
                if pair in invalid_link_pairs or pull_number in invalid_pull_numbers:
                    issue_uncertain = True
                    continue
                related_pull = pulls.get(pull_number)
                if related_pull is None:
                    codes.append(f"pull.missing_record:{pull_number}")
                    issue_uncertain = True
                    continue
                related_pulls.append(related_pull)

            if any(_is_merged_into(pull, policy.main_branch) for pull in related_pulls):
                continue
            if not issue_uncertain:
                findings.append(
                    _missing_main_finding(
                        snapshot,
                        item,
                        candidate,
                        candidate_item,
                        links_by_issue[issue_number],
                        related_pulls,
                        policy.main_branch,
                    )
                )

    return (
        tuple(sorted(findings, key=_finding_sort_key)),
        tuple(sorted(set(codes))),
    )


def _analyze_pull_items(
    snapshot: ReleaseSnapshot,
) -> tuple[dict[int, GitHubItem], frozenset[int], tuple[str, ...]]:
    grouped: defaultdict[int, dict[str, GitHubItem]] = defaultdict(dict)
    invalid: set[int] = set()
    codes: list[str] = []
    for item in snapshot.items:
        if item.kind is not GitHubItemKind.PULL_REQUEST:
            continue
        if not _is_issue_number(item.number):
            codes.append("pull_item.invalid_coordinate")
            continue
        item_codes = _validate_pull_item(snapshot, item)
        if item_codes:
            codes.extend(item_codes)
            invalid.add(item.number)
            continue
        grouped[item.number][_full_item_sort_key(item)] = item

    valid: dict[int, GitHubItem] = {}
    for number, records in sorted(grouped.items()):
        if len(records) != 1:
            codes.append(f"pull_item.conflicting_records:{number}")
            invalid.add(number)
            continue
        valid[number] = next(iter(records.values()))
    return valid, frozenset(invalid), tuple(sorted(set(codes)))


def _validate_pull_item(snapshot: ReleaseSnapshot, item: GitHubItem) -> tuple[str, ...]:
    codes: list[str] = []
    number = item.number
    if not _is_bounded_decimal(item.source_id):
        codes.append(f"pull_item.invalid_source_id:{number}")
    if item.state not in _STATES:
        codes.append(f"pull_item.invalid_state:{number}")
    if item.milestone_number is not None and not _is_issue_number(
        item.milestone_number
    ):
        codes.append(f"pull_item.invalid_milestone:{number}")
    if not _valid_timestamps(snapshot, item):
        codes.append(f"pull_item.invalid_timestamps:{number}")
    if (
        not isinstance(item.url, str)
        or len(item.url) > _MAX_URL_LENGTH
        or not _is_direct_url(item.url, snapshot.repository_full_name, "pull", number)
    ):
        codes.append(f"pull_item.invalid_url:{number}")
    if not _valid_strings(item.labels) or not _valid_strings(item.assignees):
        codes.append(f"pull_item.invalid_metadata:{number}")
    if not isinstance(item.body, str) or item.body:
        codes.append(f"pull_item.invalid_body:{number}")
    return tuple(codes)


def _analyze_pulls(
    snapshot: ReleaseSnapshot,
) -> tuple[dict[int, GitHubPullRequest], frozenset[int], tuple[str, ...]]:
    grouped: defaultdict[int, dict[str, GitHubPullRequest]] = defaultdict(dict)
    invalid: set[int] = set()
    codes: list[str] = []
    for pull in snapshot.pull_requests:
        if not _is_issue_number(pull.number):
            codes.append("pull.invalid_coordinate")
            continue
        pull_codes = _validate_pull(snapshot, pull)
        if pull_codes:
            codes.extend(pull_codes)
            invalid.add(pull.number)
            continue
        normalized = replace(pull, milestone_number=None)
        key = _full_pull_sort_key(normalized)
        current = grouped[pull.number].get(key)
        if current is None or repr(normalized) < repr(current):
            grouped[pull.number][key] = normalized

    valid: dict[int, GitHubPullRequest] = {}
    for number, records in sorted(grouped.items()):
        if len(records) != 1:
            codes.append(f"pull.conflicting_records:{number}")
            invalid.add(number)
            continue
        valid[number] = next(iter(records.values()))
    return valid, frozenset(invalid), tuple(sorted(set(codes)))


def _validate_pull(
    snapshot: ReleaseSnapshot, pull: GitHubPullRequest
) -> tuple[str, ...]:
    number = pull.number
    codes: list[str] = []
    if not _is_bounded_decimal(pull.source_id):
        codes.append(f"pull.invalid_source_id:{number}")
    if (
        not isinstance(pull.url, str)
        or len(pull.url) > _MAX_URL_LENGTH
        or not _is_direct_url(pull.url, snapshot.repository_full_name, "pull", number)
    ):
        codes.append(f"pull.invalid_url:{number}")
    if pull.state not in _STATES:
        codes.append(f"pull.invalid_state:{number}")
    if not _valid_branch(pull.base_ref) or not _valid_branch(pull.head_ref):
        codes.append(f"pull.invalid_branch:{number}")
    if not _valid_sha(pull.base_sha) or not _valid_sha(pull.head_sha):
        codes.append(f"pull.invalid_sha:{number}")
    if pull.merge_commit_sha is not None and not _valid_sha(pull.merge_commit_sha):
        codes.append(f"pull.invalid_merge_sha:{number}")
    if not _valid_pull_timestamps(snapshot, pull):
        codes.append(f"pull.invalid_timestamps:{number}")
    if not _valid_strings(pull.labels) or not _valid_strings(pull.assignees):
        codes.append(f"pull.invalid_metadata:{number}")
    if pull.merged_at is not None and (
        pull.state != "closed" or pull.merge_commit_sha is None
    ):
        codes.append(f"pull.invalid_merge_state:{number}")
    return tuple(codes)


def _analyze_links(
    snapshot: ReleaseSnapshot, issues: dict[int, GitHubItem]
) -> tuple[
    tuple[ReleaseLink, ...],
    frozenset[tuple[int, int]],
    frozenset[int],
    frozenset[int],
    tuple[str, ...],
]:
    grouped: defaultdict[tuple[int, int], dict[str, ReleaseLink]] = defaultdict(dict)
    invalid, alias_codes = _link_source_aliases(snapshot.links)
    uncertain_issues: set[int] = set()
    uncertain_pulls: set[int] = set()
    codes: list[str] = list(alias_codes)
    all_issue_numbers = {
        item.number
        for item in snapshot.items
        if item.kind is GitHubItemKind.ISSUE and _is_issue_number(item.number)
    }
    for relation in snapshot.links:
        issue_number = relation.issue_number
        pull_number = relation.pull_request_number
        valid_issue = _is_issue_number(issue_number)
        valid_pull = _is_issue_number(pull_number)
        if not valid_issue or not valid_pull:
            codes.append("link.invalid_coordinate")
            if valid_issue:
                uncertain_issues.add(issue_number)
            if valid_pull:
                uncertain_pulls.add(pull_number)
            continue
        pair = (issue_number, pull_number)
        relation_codes = _validate_link(snapshot, relation)
        if issue_number not in all_issue_numbers:
            relation_codes.append(f"link.missing_issue:{issue_number}:{pull_number}")
        elif issue_number not in issues:
            invalid.add(pair)
        if relation_codes or pair in invalid:
            codes.extend(relation_codes)
            invalid.add(pair)
            continue
        grouped[pair][_full_link_sort_key(relation)] = relation

    valid: list[ReleaseLink] = []
    for pair, records in sorted(grouped.items()):
        if len(records) != 1:
            codes.append(f"link.conflicting_records:{pair[0]}:{pair[1]}")
            invalid.add(pair)
            continue
        valid.append(next(iter(records.values())))
    return (
        tuple(valid),
        frozenset(invalid),
        frozenset(uncertain_issues),
        frozenset(uncertain_pulls),
        tuple(sorted(set(codes))),
    )


def _validate_link(snapshot: ReleaseSnapshot, relation: ReleaseLink) -> list[str]:
    issue_number = relation.issue_number
    pull_number = relation.pull_request_number
    codes: list[str] = []
    if not _is_bounded_decimal(relation.source_id):
        codes.append(f"link.invalid_source_id:{issue_number}:{pull_number}")
    if (
        not isinstance(relation.url, str)
        or len(relation.url) > _MAX_URL_LENGTH
        or not _is_direct_url(
            relation.url, snapshot.repository_full_name, "pull", pull_number
        )
    ):
        codes.append(f"link.invalid_url:{issue_number}:{pull_number}")
    if not _aware(relation.created_at) or (
        isinstance(snapshot.fetched_at, datetime)
        and isinstance(relation.created_at, datetime)
        and relation.created_at > snapshot.fetched_at
    ):
        codes.append(f"link.invalid_timestamps:{issue_number}:{pull_number}")
    return codes


def _is_merged_into(pull: GitHubPullRequest, branch: str) -> bool:
    return (
        pull.base_ref == branch
        and pull.state == "closed"
        and _aware(pull.merged_at)
        and pull.merge_commit_sha is not None
        and _valid_sha(pull.merge_commit_sha)
    )


def _valid_pull_timestamps(snapshot: ReleaseSnapshot, pull: GitHubPullRequest) -> bool:
    fetched_at = snapshot.fetched_at
    if not (
        _aware(pull.created_at)
        and _aware(pull.updated_at)
        and isinstance(fetched_at, datetime)
        and pull.created_at <= pull.updated_at <= fetched_at
    ):
        return False
    if pull.merged_at is None:
        return True
    return (
        _aware(pull.merged_at) and pull.created_at <= pull.merged_at <= pull.updated_at
    )


def _valid_branch(value: object) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= _MAX_BRANCH_LENGTH
        and value.strip() == value
        and value.isprintable()
    )


def _valid_sha(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 40
        and all(character in "0123456789abcdef" for character in value)
    )


def _aware(value: object) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _issue_numbers_from_codes(codes: Iterable[str]) -> frozenset[int]:
    numbers: set[int] = set()
    for code in codes:
        coordinate = code.rpartition(":")[2]
        if coordinate.isascii() and coordinate.isdigit():
            numbers.add(int(coordinate))
    return frozenset(numbers)


def _item_source_aliases(
    items: Iterable[GitHubItem],
) -> tuple[frozenset[int], tuple[str, ...]]:
    grouped: defaultdict[str, set[int]] = defaultdict(set)
    for item in items:
        if _is_bounded_decimal(item.source_id) and _is_issue_number(item.number):
            grouped[item.source_id].add(item.number)
    return _coordinate_aliases(grouped, "item")


def _pull_source_aliases(
    pulls: Iterable[GitHubPullRequest],
) -> tuple[frozenset[int], tuple[str, ...]]:
    grouped: defaultdict[str, set[int]] = defaultdict(set)
    for pull in pulls:
        if _is_bounded_decimal(pull.source_id) and _is_issue_number(pull.number):
            grouped[pull.source_id].add(pull.number)
    return _coordinate_aliases(grouped, "pull")


def _coordinate_aliases(
    grouped: dict[str, set[int]] | defaultdict[str, set[int]], family: str
) -> tuple[frozenset[int], tuple[str, ...]]:
    invalid: set[int] = set()
    codes: list[str] = []
    for coordinates in grouped.values():
        if len(coordinates) <= 1:
            continue
        ordered = sorted(coordinates)
        invalid.update(ordered)
        codes.append(f"{family}.conflicting_source_id:" + ":".join(map(str, ordered)))
    return frozenset(invalid), tuple(sorted(codes))


def _link_source_aliases(
    links: Iterable[ReleaseLink],
) -> tuple[set[tuple[int, int]], tuple[str, ...]]:
    grouped: defaultdict[str, set[tuple[int, int]]] = defaultdict(set)
    for relation in links:
        if (
            _is_bounded_decimal(relation.source_id)
            and _is_issue_number(relation.issue_number)
            and _is_issue_number(relation.pull_request_number)
        ):
            grouped[relation.source_id].add(
                (relation.issue_number, relation.pull_request_number)
            )
    invalid: set[tuple[int, int]] = set()
    codes: list[str] = []
    for pairs in grouped.values():
        if len(pairs) <= 1:
            continue
        ordered = sorted(pairs)
        invalid.update(ordered)
        coordinates = ":".join(
            f"{issue_number}:{pull_number}" for issue_number, pull_number in ordered
        )
        codes.append(f"link.conflicting_source_id:{coordinates}")
    return invalid, tuple(sorted(codes))


def _missing_issue_relation_finding(
    snapshot: ReleaseSnapshot,
    candidate: GitHubPullRequest,
    candidate_item: GitHubItem,
) -> ReadinessFinding:
    return ReadinessFinding(
        rule_id="backmerge.main_pr_required",
        severity="BLOCKING",
        summary=(
            f"Previous-release PR #{candidate.number} has no linked shipped-scope Issue"
        ),
        required_action=(
            f"Link PR #{candidate.number} to its previous-milestone Issue and a merged main PR"
        ),
        evidence=(
            _pull_evidence(snapshot, candidate),
            _pr_item_evidence(snapshot, candidate_item),
        ),
    )


def _missing_main_finding(
    snapshot: ReleaseSnapshot,
    issue: GitHubItem,
    candidate: GitHubPullRequest,
    candidate_item: GitHubItem,
    relations: Iterable[ReleaseLink],
    pulls: Iterable[GitHubPullRequest],
    main_branch: str,
) -> ReadinessFinding:
    evidence: list[EvidenceRef] = [
        _issue_evidence(snapshot, issue),
        _pull_evidence(snapshot, candidate),
        _pr_item_evidence(snapshot, candidate_item),
    ]
    evidence.extend(
        _link_evidence(snapshot, relation)
        for relation in sorted(relations, key=_full_link_sort_key)
    )
    evidence.extend(
        _pull_evidence(snapshot, pull)
        for pull in sorted(pulls, key=_full_pull_sort_key)
    )
    evidence = _deduplicated_evidence(evidence)[:_MAX_FINDING_EVIDENCE]
    return ReadinessFinding(
        rule_id="backmerge.main_pr_required",
        severity="BLOCKING",
        summary=f"Issue #{issue.number} has no linked PR merged into {main_branch}",
        required_action=f"Merge a PR linked to Issue #{issue.number} into {main_branch}",
        evidence=tuple(evidence),
    )


def _deduplicated_evidence(evidence: Iterable[EvidenceRef]) -> list[EvidenceRef]:
    unique = {
        (ref.source_type, ref.source_id, ref.fingerprint): ref for ref in evidence
    }
    return list(unique.values())


def _finding_sort_key(finding: ReadinessFinding) -> tuple[object, ...]:
    return (
        finding.rule_id,
        finding.summary,
        finding.required_action,
        tuple(ref.fingerprint for ref in finding.evidence),
    )
