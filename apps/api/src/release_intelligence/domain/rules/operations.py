from __future__ import annotations

import html
import re
from collections.abc import Iterable

from release_intelligence.domain.models import (
    EvidenceRef,
    ReadinessFinding,
    ReleaseSnapshot,
)
from release_intelligence.domain.policy import ReleasePolicy
from release_intelligence.domain.rules.blockers import _analyze_issue_evidence
from release_intelligence.domain.rules.checks import (
    _analyze_evidence,
    _check_evidence,
    _is_success,
)
from release_intelligence.domain.rules.scope import (
    _issue_evidence,
    _normalized_labels,
    _prerequisite_error_codes,
)
from release_intelligence.ports.github import GitHubItem
from release_intelligence.security.urls import (
    GitHubEvidenceKind,
    InvalidEvidenceURL,
    parse_github_evidence_url,
)

_FIELDS = (
    "Before release",
    "During release",
    "After release",
    "Migration evidence",
)
_REQUIRED_SECTIONS = _FIELDS[:3]
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$")
_ATX_CLOSING = re.compile(r"[ \t]+#+[ \t]*$")
_FENCE = re.compile(r"^[ ]{0,3}(`{3,}|~{3,})")
_HTML_BLOCK = re.compile(
    r"^[ ]{0,3}<(?P<tag>address|article|aside|blockquote|details|dialog|div|"
    r"dl|fieldset|figcaption|figure|footer|form|h[1-6]|header|hr|main|menu|"
    r"nav|ol|p|pre|script|section|style|summary|table|ul)(?:[ \t>/]|$)",
    re.IGNORECASE,
)
_LIST_PREFIX = r"[ ]{0,3}(?:[-+*]|\d{1,9}[.)])[ \t]+"
_UNCHECKED = re.compile(rf"(?m)^{_LIST_PREFIX}\[[ \t]\](?:[ \t]|$)")
_HTML_COMMENT = re.compile(r"(?s)<!--.*?-->")
_HTML_TAG = re.compile(r"(?s)<[^>]*>")
_HORIZONTAL_RULE = re.compile(
    r"(?m)^[ ]{0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$"
)
_LIST_MARKER = re.compile(rf"(?m)^{_LIST_PREFIX}")
_PRESENTATION = re.compile(r"[*_~`]+")
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

    evidence = _analyze_issue_evidence(snapshot)
    findings: list[ReadinessFinding] = []
    codes = list(evidence.codes)
    codes.extend(
        f"operations.invalid_owner:{number}"
        for number in evidence.invalid_owner_numbers
    )
    codes.extend(
        f"issue.invalid_body:{number}" for number in evidence.invalid_body_numbers
    )
    for item in evidence.items:
        if (
            item.state != "open"
            or item.milestone_number != snapshot.milestone_number
            or policy.release_ops_label.casefold()
            not in _normalized_labels(item.labels)
        ):
            continue
        item_findings, item_codes = _evaluate_item(
            snapshot,
            item,
            owner_valid=item.number not in evidence.invalid_owner_numbers,
            body_valid=item.number not in evidence.invalid_body_numbers,
        )
        findings.extend(item_findings)
        codes.extend(item_codes)

    result = tuple(sorted(findings, key=_finding_sort_key))
    if codes:
        raise OperationsEvidenceError(findings=result, codes=codes)
    return result


def _evaluate_item(
    snapshot: ReleaseSnapshot,
    item: GitHubItem,
    *,
    owner_valid: bool,
    body_valid: bool,
) -> tuple[tuple[ReadinessFinding, ...], tuple[str, ...]]:
    issue_evidence = (_issue_evidence(snapshot, item),)
    findings: list[ReadinessFinding] = []
    codes: list[str] = []
    if owner_valid and not item.assignees:
        findings.append(
            _finding(
                "operations.owner_required",
                f"Release operations Issue #{item.number} has no owner",
                f"Assign an owner to release operations Issue #{item.number}",
                issue_evidence,
            )
        )

    if not body_valid:
        return tuple(findings), tuple(codes)

    sections, conflicts, parse_codes = _parse_sections(item)
    codes.extend(parse_codes)

    for section in _REQUIRED_SECTIONS:
        if section not in conflicts and not _valid_section(sections.get(section)):
            findings.append(
                _finding(
                    "operations.section_required",
                    f"Release operations Issue #{item.number} lacks '{section}'",
                    f"Complete the '{section}' section on Issue #{item.number}",
                    issue_evidence,
                )
            )

    migration = sections.get("Migration evidence")
    if migration is not None and "Migration evidence" not in conflicts:
        migration_findings, migration_codes = _evaluate_migration(
            snapshot, item, migration
        )
        findings.extend(migration_findings)
        codes.extend(migration_codes)
    return tuple(findings), tuple(codes)


def _parse_sections(
    item: GitHubItem,
) -> tuple[dict[str, str], frozenset[str], tuple[str, ...]]:
    if not isinstance(item.body, str) or len(item.body) > 65_536:
        return {}, frozenset(_FIELDS), (f"operations.invalid_body:{item.number}",)

    values: dict[str, dict[str, str]] = {field: {} for field in _FIELDS}
    current: str | None = None
    current_level: int | None = None
    buffer: list[str] = []
    fence_character: str | None = None
    fence_length = 0
    html_end: str | None = None

    def finish() -> None:
        nonlocal buffer, current, current_level
        if current is not None:
            value = _normalize_field_value(buffer)
            values[current].setdefault(value, value)
        buffer = []
        current = None
        current_level = None

    for line in item.body.splitlines():
        if fence_character is not None:
            if current is not None:
                buffer.append(line)
            stripped = line.lstrip(" ")
            if (
                len(line) - len(stripped) <= 3
                and stripped.startswith(fence_character * fence_length)
                and not stripped.lstrip(fence_character).strip()
            ):
                fence_character = None
                fence_length = 0
            continue

        fence = _FENCE.match(line)
        if fence is not None:
            marker = fence.group(1)
            fence_character = marker[0]
            fence_length = len(marker)
            if current is not None:
                buffer.append(line)
            continue

        if html_end is not None:
            if current is not None:
                buffer.append(line)
            if html_end in line.casefold():
                html_end = None
            continue

        html_end = _html_block_end(line)
        if html_end is not None:
            if current is not None:
                buffer.append(line)
            if html_end in line.casefold()[line.casefold().find("<") + 1 :]:
                html_end = None
            continue

        heading = _HEADING.fullmatch(line)
        if heading is not None:
            level = len(heading.group(1))
            if (
                current is not None
                and current_level is not None
                and level > current_level
            ):
                buffer.append(line)
                continue
            finish()
            title = _ATX_CLOSING.sub("", heading.group(2)).strip()
            current = title if title in values else None
            current_level = level if current is not None else None
            continue
        if current is not None:
            buffer.append(line)
    finish()

    conflicts = frozenset(
        field for field, candidates in values.items() if len(candidates) > 1
    )
    sections = {
        field: next(iter(candidates.values()))
        for field, candidates in values.items()
        if len(candidates) == 1
    }
    codes = (f"operations.conflicting_fields:{item.number}",) if conflicts else ()
    return sections, conflicts, codes


def _normalize_field_value(lines: list[str]) -> str:
    return "\n".join(line.rstrip() for line in lines).strip()


def _html_block_end(line: str) -> str | None:
    stripped = line.lstrip(" ")
    if len(line) - len(stripped) > 3:
        return None
    folded = stripped.casefold()
    if folded.startswith("<!--"):
        return "-->"
    block = _HTML_BLOCK.match(line)
    if block is None:
        return None
    tag = block.group("tag").casefold()
    if tag == "hr":
        return None
    return f"</{tag}>"


def _valid_section(value: str | None) -> bool:
    if value is None:
        return False
    canonical = _visible_text(value)
    return (
        bool(canonical)
        and canonical.casefold() not in _PLACEHOLDERS
        and _UNCHECKED.search(value) is None
    )


def _visible_text(value: str) -> str:
    visible = html.unescape(value)
    visible = _HTML_COMMENT.sub(" ", visible)
    visible = _HTML_TAG.sub(" ", visible)
    visible = _LIST_MARKER.sub("", visible)
    visible = _PRESENTATION.sub("", visible)
    visible = _HORIZONTAL_RULE.sub(" ", visible)
    return " ".join(visible.split())


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
    matches = tuple(check for check in check_state.checks if check.url == migration_url)
    if len(matches) > 1:
        return (), (f"migration.conflicting_checks:{item.number}",)
    if matches and _is_success(matches[0]):
        return (), check_state.codes

    if not matches and any(check.url == migration_url for check in snapshot.checks):
        return (), check_state.codes

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
        check_state.codes,
    )


def _is_connected_check_url(url: str, repository: str) -> bool:
    try:
        locator = parse_github_evidence_url(url, expected_repo=repository)
    except InvalidEvidenceURL:
        return False
    return locator.kind in {
        GitHubEvidenceKind.CHECK_RUN,
        GitHubEvidenceKind.ACTIONS_JOB,
    }


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
