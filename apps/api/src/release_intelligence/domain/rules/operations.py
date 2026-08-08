from __future__ import annotations

import re
from collections.abc import Iterable

from release_intelligence.domain.models import (
    EvidenceRef,
    ReadinessFinding,
    ReleaseSnapshot,
)
from release_intelligence.domain.policy import ReleasePolicy
from release_intelligence.domain.rules.blockers import _current_issue_records
from release_intelligence.domain.rules.checks import (
    _analyze_evidence,
    _canonical_github_path,
    _check_evidence,
    _is_bounded_decimal,
    _is_success,
)
from release_intelligence.domain.rules.scope import (
    _issue_evidence,
    _normalized_labels,
    _prerequisite_error_codes,
)
from release_intelligence.ports.github import GitHubItem

_FIELDS = (
    "Before release",
    "During release",
    "After release",
    "Migration evidence",
)
_REQUIRED_SECTIONS = _FIELDS[:3]
_HEADING = re.compile(r"^#{1,6}[ \t]+(.+?)[ \t]*$")
_UNCHECKED = re.compile(r"(?m)^[ \t]*[-*][ \t]+\[[ \t]\]")
_HTML_COMMENT = re.compile(r"(?s)^<!--.*-->$")
_PLACEHOLDERS = frozenset(
    {
        "-",
        "none",
        "n/a",
        "na",
        "not applicable",
        "tbd",
        "todo",
        "no response",
        "_no response_",
    }
)


class OperationsEvidenceError(Exception):
    def __init__(
        self, *, findings: tuple[ReadinessFinding, ...], codes: Iterable[str]
    ) -> None:
        super().__init__("Release operations evidence is incomplete")
        self.findings = findings
        self.codes = tuple(sorted(set(codes)))


def evaluate_operations(
    snapshot: ReleaseSnapshot, policy: ReleasePolicy
) -> tuple[ReadinessFinding, ...]:
    prerequisite_errors = _prerequisite_error_codes(snapshot, policy)
    if prerequisite_errors:
        raise OperationsEvidenceError(findings=(), codes=prerequisite_errors)

    items, issue_codes = _current_issue_records(snapshot)
    findings: list[ReadinessFinding] = []
    codes = list(issue_codes)
    for item in items:
        if policy.release_ops_label.casefold() not in _normalized_labels(item.labels):
            continue
        item_findings, item_codes = _evaluate_item(snapshot, item)
        findings.extend(item_findings)
        codes.extend(item_codes)

    result = tuple(sorted(findings, key=_finding_sort_key))
    if codes:
        raise OperationsEvidenceError(findings=result, codes=codes)
    return result


def _evaluate_item(
    snapshot: ReleaseSnapshot, item: GitHubItem
) -> tuple[tuple[ReadinessFinding, ...], tuple[str, ...]]:
    sections, parse_codes = _parse_sections(item)
    if parse_codes:
        return (), parse_codes

    issue_evidence = (_issue_evidence(snapshot, item),)
    findings: list[ReadinessFinding] = []
    if not item.assignees:
        findings.append(
            _finding(
                "operations.owner_required",
                f"Release operations Issue #{item.number} has no owner",
                f"Assign an owner to release operations Issue #{item.number}",
                issue_evidence,
            )
        )
    elif any(
        not isinstance(owner, str)
        or owner != owner.strip()
        or not owner
        or not owner.isprintable()
        for owner in item.assignees
    ):
        return (), (f"operations.invalid_owner:{item.number}",)

    for section in _REQUIRED_SECTIONS:
        if not _valid_section(sections.get(section)):
            findings.append(
                _finding(
                    "operations.section_required",
                    f"Release operations Issue #{item.number} lacks '{section}'",
                    f"Complete the '{section}' section on Issue #{item.number}",
                    issue_evidence,
                )
            )

    migration = sections.get("Migration evidence")
    if migration is not None:
        migration_findings, migration_codes = _evaluate_migration(
            snapshot, item, migration
        )
        findings.extend(migration_findings)
        if migration_codes:
            return tuple(findings), migration_codes
    return tuple(findings), ()


def _parse_sections(
    item: GitHubItem,
) -> tuple[dict[str, str], tuple[str, ...]]:
    if not isinstance(item.body, str) or len(item.body) > 65_536:
        return {}, (f"operations.invalid_body:{item.number}",)

    values: dict[str, list[str]] = {field: [] for field in _FIELDS}
    current: str | None = None
    buffer: list[str] = []

    def finish() -> None:
        nonlocal buffer
        if current is not None:
            values[current].append("\n".join(buffer).strip())
        buffer = []

    for line in item.body.splitlines():
        heading = _HEADING.fullmatch(line)
        if heading is not None:
            finish()
            title = heading.group(1)
            current = title if title in values else None
            continue
        if current is not None:
            buffer.append(line)
    finish()

    if any(len(candidates) > 1 for candidates in values.values()):
        return {}, (f"operations.conflicting_fields:{item.number}",)
    return {
        field: candidates[0] for field, candidates in values.items() if candidates
    }, ()


def _valid_section(value: str | None) -> bool:
    if value is None:
        return False
    canonical = value.strip()
    return (
        bool(canonical)
        and canonical.casefold() not in _PLACEHOLDERS
        and _HTML_COMMENT.fullmatch(canonical) is None
        and _UNCHECKED.search(canonical) is None
    )


def _evaluate_migration(
    snapshot: ReleaseSnapshot,
    item: GitHubItem,
    value: str,
) -> tuple[tuple[ReadinessFinding, ...], tuple[str, ...]]:
    migration_url = value.strip()
    if (
        not _valid_section(value)
        or "\n" in migration_url
        or not (_is_connected_check_url(migration_url, snapshot.repository_full_name))
    ):
        return (), (f"migration.invalid_evidence:{item.number}",)

    check_state = _analyze_evidence(snapshot)
    if check_state.codes:
        return (), check_state.codes
    matches = tuple(check for check in check_state.checks if check.url == migration_url)
    if len(matches) > 1:
        return (), (f"migration.conflicting_checks:{item.number}",)
    if matches and _is_success(matches[0]):
        return (), ()

    evidence = [_issue_evidence(snapshot, item)]
    if matches:
        evidence.append(_check_evidence(snapshot, matches[0]))
    return (
        (
            _finding(
                "operations.migration_evidence_required",
                f"Migration evidence for Issue #{item.number} is not successful",
                (
                    "Link a successful connected-repository check run in the "
                    f"'Migration evidence' section on Issue #{item.number}"
                ),
                tuple(evidence),
            ),
        ),
        (),
    )


def _is_connected_check_url(url: str, repository: str) -> bool:
    path = _canonical_github_path(url)
    if path is None:
        return False
    prefix = f"/{repository}/"
    if not path.startswith(prefix):
        return False
    parts = path.removeprefix(prefix).split("/")
    if len(parts) == 2 and parts[0] == "runs":
        return _is_bounded_decimal(parts[1])
    if len(parts) == 4 and parts[0] == "runs" and parts[2] == "jobs":
        return _is_bounded_decimal(parts[1]) and _is_bounded_decimal(parts[3])
    return (
        len(parts) == 5
        and parts[:2] == ["actions", "runs"]
        and parts[3] in {"job", "jobs"}
        and _is_bounded_decimal(parts[2])
        and _is_bounded_decimal(parts[4])
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


def _finding_sort_key(finding: ReadinessFinding) -> tuple[object, ...]:
    return (
        finding.rule_id,
        finding.summary,
        tuple(ref.fingerprint for ref in finding.evidence),
    )
