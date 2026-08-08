from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable

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


class BlockerEvidenceError(Exception):
    def __init__(
        self, *, findings: tuple[ReadinessFinding, ...], codes: Iterable[str]
    ) -> None:
        super().__init__("Blocker evidence is incomplete")
        self.findings = findings
        self.codes = tuple(sorted(set(codes)))


def evaluate_blockers(
    snapshot: ReleaseSnapshot, policy: ReleasePolicy
) -> tuple[ReadinessFinding, ...]:
    prerequisite_errors = _prerequisite_error_codes(snapshot, policy)
    if prerequisite_errors:
        raise BlockerEvidenceError(findings=(), codes=prerequisite_errors)

    records, codes = _current_issue_records(snapshot)
    findings = tuple(
        ReadinessFinding(
            rule_id="blockers.open_release_blocker",
            severity="BLOCKING",
            summary=f"Issue #{item.number} is an open release blocker",
            required_action=f"Resolve and close release blocker Issue #{item.number}",
            evidence=(_issue_evidence(snapshot, item),),
        )
        for item in records
        if policy.blocker_label.casefold() in _normalized_labels(item.labels)
    )
    if codes:
        raise BlockerEvidenceError(findings=findings, codes=codes)
    return findings


def _current_issue_records(
    snapshot: ReleaseSnapshot,
) -> tuple[tuple[GitHubItem, ...], tuple[str, ...]]:
    grouped: defaultdict[int, dict[str, GitHubItem]] = defaultdict(dict)
    codes: list[str] = []
    for item in snapshot.items:
        if (
            item.kind is not GitHubItemKind.ISSUE
            or item.state.casefold() != "open"
            or item.milestone_number != snapshot.milestone_number
        ):
            continue
        key = json.dumps(_item_facts(item), sort_keys=True, separators=(",", ":"))
        grouped[item.number][key] = item

    valid: list[GitHubItem] = []
    for number, candidates in sorted(grouped.items()):
        if len(candidates) != 1:
            codes.append(f"issue.conflicting_records:{number}")
            continue
        item = next(iter(candidates.values()))
        if not _is_direct_url(
            item.url, snapshot.repository_full_name, "issues", item.number
        ):
            codes.append(f"issue.invalid_url:{number}")
            continue
        valid.append(item)
    return tuple(valid), tuple(sorted(set(codes)))
