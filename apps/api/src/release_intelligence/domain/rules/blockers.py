from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from release_intelligence.domain.models import ReadinessFinding, ReleaseSnapshot
from release_intelligence.domain.policy import ReleasePolicy
from release_intelligence.domain.rules.scope import (
    _is_direct_url,
    _issue_evidence,
    _item_facts,
    _normalized_labels,
    _prerequisite_error_codes,
)
from release_intelligence.ports.github import GitHubItem, GitHubItemKind

_MAX_SIGNED_BIGINT = 2**63 - 1
_MAX_BODY_LENGTH = 65_536
_MAX_COLLECTION_LENGTH = 100
_MAX_SOURCE_STRING_LENGTH = 255
_MAX_URL_LENGTH = 2_048
_STATES = frozenset({"open", "closed"})
_GITHUB_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")


class BlockerEvidenceError(Exception):
    def __init__(
        self, *, findings: tuple[ReadinessFinding, ...], codes: Iterable[str]
    ) -> None:
        super().__init__("Blocker evidence is incomplete")
        self.findings = findings
        self.codes = tuple(sorted(set(codes)))


@dataclass(frozen=True, slots=True)
class _IssueEvidenceState:
    items: tuple[GitHubItem, ...]
    invalid_owner_numbers: frozenset[int]
    codes: tuple[str, ...]


def evaluate_blockers(
    snapshot: ReleaseSnapshot, policy: ReleasePolicy
) -> tuple[ReadinessFinding, ...]:
    prerequisite_errors = _prerequisite_error_codes(snapshot, policy)
    if prerequisite_errors:
        raise BlockerEvidenceError(findings=(), codes=prerequisite_errors)

    evidence = _analyze_issue_evidence(snapshot)
    findings = tuple(
        ReadinessFinding(
            rule_id="blockers.open_release_blocker",
            severity="BLOCKING",
            summary=f"Issue #{item.number} is an open release blocker",
            required_action=f"Resolve and close release blocker Issue #{item.number}",
            evidence=(_issue_evidence(snapshot, item),),
        )
        for item in evidence.items
        if item.state == "open"
        and item.milestone_number == snapshot.milestone_number
        and policy.blocker_label.casefold() in _normalized_labels(item.labels)
    )
    codes = [*evidence.codes]
    codes.extend(
        f"issue.invalid_assignees:{item.number}"
        for item in evidence.items
        if item.number in evidence.invalid_owner_numbers
    )
    if codes:
        raise BlockerEvidenceError(findings=findings, codes=codes)
    return findings


def _analyze_issue_evidence(snapshot: ReleaseSnapshot) -> _IssueEvidenceState:
    grouped: defaultdict[int, list[GitHubItem]] = defaultdict(list)
    codes: list[str] = []
    for item in snapshot.items:
        if not isinstance(item.kind, GitHubItemKind):
            coordinate = _issue_coordinate(item.number)
            codes.append(f"issue.invalid_kind:{coordinate}")
            continue
        if not _is_issue_number(item.number):
            if item.kind is GitHubItemKind.ISSUE:
                codes.append(
                    f"issue.invalid_coordinate:{_issue_coordinate(item.number)}"
                )
            continue
        grouped[item.number].append(item)

    valid: list[GitHubItem] = []
    invalid_owners: set[int] = set()
    for number, raw_candidates in sorted(grouped.items()):
        if not any(item.kind is GitHubItemKind.ISSUE for item in raw_candidates):
            continue
        if any(item.kind is not GitHubItemKind.ISSUE for item in raw_candidates):
            codes.append(f"issue.conflicting_records:{number}")
            continue

        candidates: dict[str, GitHubItem] = {}
        candidate_codes: list[str] = []
        candidate_invalid_owner = False
        for item in raw_candidates:
            fatal_codes, invalid_owner = _validate_issue(snapshot, item)
            candidate_codes.extend(fatal_codes)
            candidate_invalid_owner = candidate_invalid_owner or invalid_owner
            if fatal_codes:
                continue
            key = json.dumps(_item_facts(item), sort_keys=True, separators=(",", ":"))
            candidates[key] = item
        if candidate_codes:
            codes.extend(candidate_codes)
            continue
        if len(candidates) != 1:
            codes.append(f"issue.conflicting_records:{number}")
            continue
        valid.append(next(iter(candidates.values())))
        if candidate_invalid_owner:
            invalid_owners.add(number)
    return _IssueEvidenceState(
        items=tuple(valid),
        invalid_owner_numbers=frozenset(invalid_owners),
        codes=tuple(sorted(set(codes))),
    )


def _validate_issue(
    snapshot: ReleaseSnapshot, item: GitHubItem
) -> tuple[tuple[str, ...], bool]:
    number = item.number
    codes: list[str] = []
    if not _is_bounded_decimal(item.source_id):
        codes.append(f"issue.invalid_source_id:{number}")
    if (
        not isinstance(item.state, str)
        or len(item.state) > len("closed")
        or item.state not in _STATES
    ):
        codes.append(f"issue.invalid_state:{number}")
    if item.milestone_number is not None and not _is_issue_number(
        item.milestone_number
    ):
        codes.append(f"issue.invalid_milestone:{number}")
    if not _valid_timestamps(snapshot, item):
        codes.append(f"issue.invalid_timestamps:{number}")
    if not _valid_strings(item.labels):
        codes.append(f"issue.invalid_labels:{number}")
    bounded_assignees = _bounded_strings(item.assignees)
    if not bounded_assignees:
        codes.append(f"issue.invalid_assignees:{number}")
    if not isinstance(item.body, str) or len(item.body) > _MAX_BODY_LENGTH:
        codes.append(f"issue.invalid_body:{number}")
    if (
        not isinstance(item.url, str)
        or len(item.url) > _MAX_URL_LENGTH
        or not _is_direct_url(
            item.url, snapshot.repository_full_name, "issues", item.number
        )
    ):
        codes.append(f"issue.invalid_url:{number}")
    invalid_owner = bounded_assignees and any(
        _GITHUB_LOGIN.fullmatch(owner) is None for owner in item.assignees
    )
    return tuple(sorted(set(codes))), invalid_owner


def _valid_timestamps(snapshot: ReleaseSnapshot, item: GitHubItem) -> bool:
    fetched_at = snapshot.fetched_at
    return (
        _is_aware(item.created_at)
        and _is_aware(item.updated_at)
        and isinstance(fetched_at, datetime)
        and item.created_at <= item.updated_at <= fetched_at
    )


def _valid_strings(values: object) -> bool:
    if not isinstance(values, tuple):
        return False
    return _bounded_strings(values) and all(
        bool(value.strip()) and value.isprintable() for value in values
    )


def _bounded_strings(values: object) -> bool:
    return (
        isinstance(values, tuple)
        and len(values) <= _MAX_COLLECTION_LENGTH
        and all(
            isinstance(value, str) and len(value) <= _MAX_SOURCE_STRING_LENGTH
            for value in values
        )
    )


def _is_issue_number(value: object) -> bool:
    return type(value) is int and 0 < value <= _MAX_SIGNED_BIGINT


def _is_bounded_decimal(value: object) -> bool:
    if not isinstance(value, str):
        return False
    maximum = str(_MAX_SIGNED_BIGINT)
    return (
        0 < len(value) <= len(maximum)
        and value.isascii()
        and value.isdigit()
        and not value.startswith("0")
        and (
            len(value) < len(maximum)
            or (len(value) == len(maximum) and value <= maximum)
        )
    )


def _issue_coordinate(value: object) -> str:
    if _is_issue_number(value):
        return str(value)
    if type(value) is int:
        absolute = abs(value)
        payload = (
            f"int:{value < 0}:{absolute.bit_length()}:{absolute & ((1 << 64) - 1)}"
        )
    elif isinstance(value, str):
        payload = f"str:{len(value)}:{value[:64]}:{value[-64:]}"
    else:
        payload = f"type:{type(value).__module__}.{type(value).__qualname__}"
    return "record-" + hashlib.sha256(payload.encode()).hexdigest()[:16]


def _is_aware(value: object) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )
