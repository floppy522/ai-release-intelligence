from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from release_intelligence.domain.models import (
    EvidenceRef,
    ReadinessAssessment,
    ReadinessFinding,
    ReleaseSnapshot,
    ReleaseStatus,
    SnapshotVersion,
)
from release_intelligence.domain.policy import ReleasePolicy
from release_intelligence.domain.rules.backmerge import (
    BackmergeEvidenceError,
    evaluate_backmerge,
)
from release_intelligence.domain.rules.blockers import (
    BlockerEvidenceError,
    evaluate_blockers,
)
from release_intelligence.domain.rules.checks import (
    CheckDecision,
    CheckEvidenceError,
    evaluate_checks,
)
from release_intelligence.domain.rules.operations import (
    OperationsEvidenceError,
    evaluate_operations,
)
from release_intelligence.domain.rules.scope import ScopeEvidenceError, evaluate_scope

MAX_SNAPSHOT_AGE = timedelta(minutes=10)
_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_.:-]{0,254}$")


def assess(
    snapshot: ReleaseSnapshot,
    policy: ReleasePolicy | None,
    decisions: Iterable[CheckDecision],
    *,
    now: datetime,
) -> ReadinessAssessment:
    """Compose every approved deterministic rule with fixed status precedence."""

    if snapshot.snapshot_version is SnapshotVersion.LEGACY:
        if _is_trusted_legacy_fixture(snapshot):
            return assess_release(snapshot)
        return _assessment(ReleaseStatus.INSUFFICIENT_DATA, ())

    insufficiency_codes = set(_snapshot_error_codes(snapshot, now))
    if policy is None:
        if (
            insufficiency_codes
            or snapshot.items
            or snapshot.links
            or snapshot.pull_requests
            or snapshot.checks
            or snapshot.comparisons
        ):
            return _assessment(ReleaseStatus.INSUFFICIENT_DATA, ())
        return _assessment(ReleaseStatus.READY, ())
    if not isinstance(policy, ReleasePolicy):
        insufficiency_codes.add("policy.missing")
        return _assessment(ReleaseStatus.INSUFFICIENT_DATA, ())

    try:
        current_decisions = tuple(decisions)
    except Exception:  # noqa: BLE001 - untrusted Protocol collection boundary
        current_decisions = ()
        insufficiency_codes.add("decision.invalid_collection")

    findings: list[ReadinessFinding] = []
    try:
        findings.extend(evaluate_scope(snapshot, policy))
    except ScopeEvidenceError as error:
        findings.extend(error.findings)
        insufficiency_codes.update(_safe_codes(error.codes))

    try:
        findings.extend(evaluate_checks(snapshot, policy, decisions=current_decisions))
    except CheckEvidenceError as error:
        findings.extend(error.findings)
        insufficiency_codes.update(_safe_codes(error.codes))

    try:
        findings.extend(evaluate_blockers(snapshot, policy))
    except BlockerEvidenceError as error:
        findings.extend(error.findings)
        insufficiency_codes.update(_safe_codes(error.codes))

    try:
        findings.extend(evaluate_operations(snapshot, policy))
    except OperationsEvidenceError as error:
        findings.extend(error.findings)
        insufficiency_codes.update(_safe_codes(error.codes))

    if (
        policy.previous_milestone_number is not None
        and policy.previous_release_branch is not None
    ):
        try:
            findings.extend(evaluate_backmerge(snapshot, policy))
        except BackmergeEvidenceError as error:
            findings.extend(error.findings)
            insufficiency_codes.update(_safe_codes(error.codes))

    ordered = _ordered_findings(findings)
    if insufficiency_codes:
        status = ReleaseStatus.INSUFFICIENT_DATA
    elif any(finding.blocks_release for finding in ordered):
        status = ReleaseStatus.NOT_READY
    elif any(
        finding.requires_decision and finding.decision_allowed for finding in ordered
    ):
        status = ReleaseStatus.NEEDS_DECISION
    else:
        status = ReleaseStatus.READY
    return _assessment(status, ordered)


def assess_release(snapshot: ReleaseSnapshot) -> ReadinessAssessment:
    normalized_missing_pr = next(
        (
            item
            for item in snapshot.items
            if "code-change" in item.labels
            and not any(link.issue_number == item.number for link in snapshot.links)
        ),
        None,
    )
    if normalized_missing_pr is not None:
        evidence = EvidenceRef(
            evidence_id=f"github-issue-{normalized_missing_pr.source_id}",
            source_type="github_issue",
            source_id=str(normalized_missing_pr.number),
            url=normalized_missing_pr.url,
            fingerprint=(
                f"github:issue:{normalized_missing_pr.source_id}:"
                f"{normalized_missing_pr.updated_at.isoformat()}"
            ),
        )
        finding = ReadinessFinding(
            rule_id="scope.code_change_requires_pr",
            severity="BLOCKING",
            summary=f"Issue #{normalized_missing_pr.number} has no linked PR",
            required_action=f"Link a merged PR to Issue #{normalized_missing_pr.number}",
            evidence=(evidence,),
        )
        return _assessment(ReleaseStatus.NOT_READY, (finding,))

    if "code-change" in snapshot.issue_labels and not snapshot.linked_pr_numbers:
        finding = ReadinessFinding(
            rule_id="scope.code_change_requires_pr",
            severity="BLOCKING",
            summary=f"Issue #{snapshot.issue_number} has no linked PR",
            required_action=f"Link a merged PR to Issue #{snapshot.issue_number}",
            evidence=(snapshot.issue_evidence,),
        )
        return _assessment(ReleaseStatus.NOT_READY, (finding,))

    return _assessment(ReleaseStatus.READY, ())


def _snapshot_error_codes(snapshot: ReleaseSnapshot, now: datetime) -> tuple[str, ...]:
    codes: list[str] = []
    if snapshot.snapshot_version is not SnapshotVersion.GITHUB_V1:
        codes.append("snapshot.invalid_version")
    if not snapshot.complete:
        codes.append("snapshot.incomplete")
    if snapshot.source_errors:
        codes.append("snapshot.source_errors")
    if not snapshot.candidate_ref or not snapshot.candidate_sha:
        codes.append("snapshot.invalid_candidate")
    if (
        snapshot.milestone_number <= 0
        or not snapshot.repository_id
        or not snapshot.repository_full_name
    ):
        codes.append("snapshot.invalid_identity")

    started_at = snapshot.fetch_started_at
    fetched_at = snapshot.fetched_at
    if not (_is_aware(now) and _is_aware(started_at) and _is_aware(fetched_at)):
        codes.append("snapshot.invalid_timestamps")
        return tuple(sorted(set(codes)))

    assert started_at is not None
    assert fetched_at is not None
    effective_now = now.astimezone(UTC)
    effective_started = started_at.astimezone(UTC)
    effective_fetched = fetched_at.astimezone(UTC)
    if effective_started > effective_fetched or effective_fetched > effective_now:
        codes.append("snapshot.invalid_timestamps")
    elif effective_now - effective_fetched > MAX_SNAPSHOT_AGE:
        codes.append("snapshot.stale")
    return tuple(sorted(set(codes)))


def _is_aware(value: object) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _safe_codes(codes: Iterable[object]) -> tuple[str, ...]:
    safe: set[str] = set()
    for code in codes:
        if type(code) is str and _SAFE_CODE.fullmatch(code) is not None:
            safe.add(code)
        else:
            safe.add("evidence.invalid_error_code")
    return tuple(sorted(safe))


def _ordered_findings(
    findings: Iterable[ReadinessFinding],
) -> tuple[ReadinessFinding, ...]:
    return tuple(sorted(set(findings), key=_finding_sort_key))


def _finding_sort_key(finding: ReadinessFinding) -> tuple[object, ...]:
    return (
        finding.rule_id,
        finding.severity,
        finding.summary,
        finding.required_action,
        tuple(
            (
                evidence.source_type,
                evidence.source_id,
                evidence.evidence_id,
                evidence.fingerprint,
                evidence.url,
            )
            for evidence in finding.evidence
        ),
    )


def _assessment(
    status: ReleaseStatus, findings: tuple[ReadinessFinding, ...]
) -> ReadinessAssessment:
    return ReadinessAssessment(status=status, findings=findings)


def _is_trusted_legacy_fixture(snapshot: ReleaseSnapshot) -> bool:
    return (
        snapshot.repository_id == "fixture:demo"
        and snapshot.repository_full_name == "example/release-demo"
        and snapshot.complete
        and not snapshot.source_errors
        and snapshot.fetch_started_at is None
        and snapshot.fetched_at is None
        and not snapshot.candidate_ref
        and not snapshot.candidate_sha
        and not snapshot.items
        and not snapshot.links
        and not snapshot.pull_requests
        and not snapshot.checks
        and not snapshot.comparisons
    )
