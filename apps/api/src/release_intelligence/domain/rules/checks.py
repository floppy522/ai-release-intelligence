from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from urllib.parse import urlparse

from release_intelligence.domain.models import (
    EvidenceRef,
    ReadinessFinding,
    ReleaseSnapshot,
    SnapshotVersion,
)
from release_intelligence.domain.policy import CheckCategory, ReleasePolicy
from release_intelligence.ports.github import GitHubCheck

_REPOSITORY_PART = re.compile(r"^[A-Za-z0-9_.-]+$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_FINGERPRINT = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_OWNER_LENGTH = 39
_MAX_REPOSITORY_LENGTH = 100
_MAX_SIGNED_BIGINT = 2**63 - 1
_STATUSES = frozenset(
    {"queued", "in_progress", "completed", "waiting", "requested", "pending"}
)
_CONCLUSIONS = frozenset(
    {
        "action_required",
        "cancelled",
        "failure",
        "neutral",
        "success",
        "skipped",
        "stale",
        "startup_failure",
        "timed_out",
    }
)


class CheckEvidenceError(Exception):
    """Signal incomplete CI evidence while preserving independent findings."""

    def __init__(
        self,
        *,
        findings: tuple[ReadinessFinding, ...],
        codes: Iterable[str],
    ) -> None:
        super().__init__("Check evidence is incomplete")
        self.findings = findings
        self.codes = tuple(sorted(set(codes)))


@dataclass(frozen=True, slots=True)
class CheckFingerprint:
    repository: str
    candidate_sha: str
    check_name: str
    run_id: int
    conclusion: str | None

    @property
    def value(self) -> str:
        return _digest(
            {
                "repository": self.repository,
                "candidate_sha": self.candidate_sha,
                "check_name": self.check_name,
                "run_id": self.run_id,
                "conclusion": self.conclusion,
            }
        )


class CheckDecision(Protocol):
    """Minimal Task 9 boundary for a decision already resolved as current."""

    fingerprint: str

    @property
    def blocks_release(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class _EvidenceState:
    checks: tuple[GitHubCheck, ...]
    quarantined_names: frozenset[str]
    codes: tuple[str, ...]


class _DecisionState(StrEnum):
    NONE = "NONE"
    ACCEPTED = "ACCEPTED"
    BLOCKER = "BLOCKER"
    CONFLICTING = "CONFLICTING"
    INVALID = "INVALID"


def evaluate_checks(
    snapshot: ReleaseSnapshot,
    policy: ReleasePolicy,
    *,
    decisions: Iterable[CheckDecision],
) -> tuple[ReadinessFinding, ...]:
    """Classify candidate-head check runs without selecting favorable evidence."""

    prerequisite_errors = _prerequisite_error_codes(snapshot, policy)
    if prerequisite_errors:
        raise CheckEvidenceError(findings=(), codes=prerequisite_errors)

    current_decisions = tuple(decisions)
    evidence = _analyze_evidence(snapshot)
    findings: list[ReadinessFinding] = []
    observed_names = {check.name for check in evidence.checks}
    for check in evidence.checks:
        category = policy.check_categories.get(check.name)
        if category is CheckCategory.BLOCKING:
            if not _is_success(check):
                findings.append(_blocking_finding(snapshot, check))
        elif category is CheckCategory.ADVISORY:
            if not _is_success(check):
                decision = _decision_state(
                    _fingerprint(snapshot, check), current_decisions
                )
                if decision is _DecisionState.CONFLICTING:
                    evidence = _with_code(
                        evidence, f"decision.conflicting:{check.run_id}"
                    )
                elif decision is _DecisionState.INVALID:
                    evidence = _with_code(evidence, f"decision.invalid:{check.run_id}")
                elif decision is _DecisionState.NONE:
                    findings.append(_advisory_finding(snapshot, check, blocker=False))
                elif decision is _DecisionState.BLOCKER:
                    findings.append(_advisory_finding(snapshot, check, blocker=True))
        elif category is None:
            findings.append(_unknown_finding(snapshot, check))

    for check_name, category in policy.check_categories.items():
        if (
            category is CheckCategory.BLOCKING
            and check_name not in observed_names
            and check_name not in evidence.quarantined_names
        ):
            findings.append(_missing_blocking_finding(snapshot, check_name))

    result = tuple(sorted(findings, key=_finding_sort_key))
    if evidence.codes:
        raise CheckEvidenceError(findings=result, codes=evidence.codes)
    return result


def _prerequisite_error_codes(
    snapshot: ReleaseSnapshot, policy: ReleasePolicy
) -> tuple[str, ...]:
    errors: list[str] = []
    if snapshot.snapshot_version is not SnapshotVersion.GITHUB_V1:
        errors.append("snapshot.invalid_version")
    if not snapshot.complete:
        errors.append("snapshot.incomplete")
    if snapshot.source_errors:
        errors.append("snapshot.source_errors")
    if (
        snapshot.milestone_number != policy.milestone_number
        or snapshot.candidate_ref != policy.candidate_branch
    ):
        errors.append("snapshot.policy_mismatch")
    if (
        not isinstance(snapshot.repository_id, str)
        or not snapshot.repository_id.strip()
        or not _is_repository_name(snapshot.repository_full_name)
    ):
        errors.append("snapshot.invalid_repository")
    if not _is_sha(snapshot.candidate_sha):
        errors.append("snapshot.invalid_candidate")
    started_at = snapshot.fetch_started_at
    fetched_at = snapshot.fetched_at
    if not (_is_aware(started_at) and _is_aware(fetched_at)) or (
        isinstance(started_at, datetime)
        and isinstance(fetched_at, datetime)
        and started_at > fetched_at
    ):
        errors.append("snapshot.invalid_timestamps")
    if not _is_valid_policy_categories(policy.check_categories):
        errors.append("policy.invalid_check_categories")
    return tuple(sorted(set(errors)))


def _analyze_evidence(snapshot: ReleaseSnapshot) -> _EvidenceState:
    canonical = {_check_canonical_key(check): check for check in snapshot.checks}
    records = tuple(sorted(canonical.values(), key=_check_canonical_key))
    codes: list[str] = []
    quarantined_keys: set[str] = set()
    quarantined_names: set[str] = set()

    by_run: defaultdict[int, list[GitHubCheck]] = defaultdict(list)
    for check in records:
        if (
            isinstance(check.run_id, int)
            and not isinstance(check.run_id, bool)
            and check.run_id > 0
        ):
            by_run[check.run_id].append(check)
    for run_id, candidates in sorted(by_run.items()):
        if len(candidates) > 1:
            codes.append(f"check.conflicting_run:{run_id}")
            for check in candidates:
                quarantined_keys.add(_check_canonical_key(check))
                if _is_canonical_name(check.name):
                    quarantined_names.add(check.name)

    for check in records:
        key = _check_canonical_key(check)
        if key in quarantined_keys:
            continue
        invalid_identity = not _is_valid_identity(snapshot, check)
        invalid_matrix = not _is_valid_matrix(snapshot, check)
        coordinate = _check_coordinate(check)
        if invalid_identity:
            codes.append(f"check.invalid_identity:{coordinate}")
        if invalid_matrix:
            codes.append(f"check.invalid_matrix:{coordinate}")
        if invalid_identity or invalid_matrix:
            quarantined_keys.add(key)
            if _is_canonical_name(check.name):
                quarantined_names.add(check.name)

    by_name: defaultdict[str, list[GitHubCheck]] = defaultdict(list)
    for check in records:
        if _check_canonical_key(check) not in quarantined_keys:
            by_name[check.name].append(check)
    for name, candidates in sorted(by_name.items()):
        if len(candidates) > 1:
            run_ids = ":".join(
                str(run_id) for run_id in sorted(c.run_id for c in candidates)
            )
            codes.append(f"check.conflicting_name:{run_ids}")
            quarantined_names.add(name)
            quarantined_keys.update(_check_canonical_key(check) for check in candidates)

    valid = tuple(
        check
        for check in records
        if _check_canonical_key(check) not in quarantined_keys
    )
    return _EvidenceState(
        checks=valid,
        quarantined_names=frozenset(quarantined_names),
        codes=tuple(sorted(set(codes))),
    )


def _with_code(evidence: _EvidenceState, code: str) -> _EvidenceState:
    return _EvidenceState(
        checks=evidence.checks,
        quarantined_names=evidence.quarantined_names,
        codes=tuple(sorted({*evidence.codes, code})),
    )


def _decision_state(
    fingerprint: CheckFingerprint, decisions: Iterable[CheckDecision]
) -> _DecisionState:
    outcomes: set[bool] = set()
    for decision in decisions:
        try:
            decision_fingerprint = decision.fingerprint
            blocks_release = decision.blocks_release
        except (AttributeError, TypeError, ValueError):
            return _DecisionState.INVALID
        if (
            type(decision_fingerprint) is not str
            or _FINGERPRINT.fullmatch(decision_fingerprint) is None
            or type(blocks_release) is not bool
        ):
            return _DecisionState.INVALID
        if decision_fingerprint == fingerprint.value:
            outcomes.add(blocks_release)
    if not outcomes:
        return _DecisionState.NONE
    if len(outcomes) > 1:
        return _DecisionState.CONFLICTING
    return _DecisionState.BLOCKER if outcomes.pop() else _DecisionState.ACCEPTED


def _is_success(check: GitHubCheck) -> bool:
    return check.status == "completed" and check.conclusion == "success"


def _is_valid_identity(snapshot: ReleaseSnapshot, check: GitHubCheck) -> bool:
    return (
        isinstance(check.run_id, int)
        and not isinstance(check.run_id, bool)
        and 0 < check.run_id <= _MAX_SIGNED_BIGINT
        and check.source_id == str(check.run_id)
        and _is_canonical_name(check.name)
        and check.head_sha == snapshot.candidate_sha
        and _is_sha(check.head_sha)
        and _is_check_url(check.url, snapshot.repository_full_name, check.run_id)
    )


def _is_valid_matrix(snapshot: ReleaseSnapshot, check: GitHubCheck) -> bool:
    if not isinstance(check.status, str) or check.status not in _STATUSES:
        return False
    if check.conclusion is not None and not isinstance(check.conclusion, str):
        return False
    started = check.started_at
    completed = check.completed_at
    fetched = snapshot.fetched_at
    if started is not None and not _is_aware(started):
        return False
    if completed is not None and not _is_aware(completed):
        return False
    if _is_aware(fetched) and isinstance(fetched, datetime):
        if started is not None and started > fetched:
            return False
        if completed is not None and completed > fetched:
            return False
    if started is not None and completed is not None and started > completed:
        return False
    if check.status == "completed":
        return (
            check.conclusion in _CONCLUSIONS
            and started is not None
            and completed is not None
        )
    if check.status == "in_progress" and started is None:
        return False
    return check.conclusion is None and completed is None


def _fingerprint(snapshot: ReleaseSnapshot, check: GitHubCheck) -> CheckFingerprint:
    return CheckFingerprint(
        repository=snapshot.repository_full_name,
        candidate_sha=snapshot.candidate_sha,
        check_name=check.name,
        run_id=check.run_id,
        conclusion=check.conclusion,
    )


def _check_evidence(snapshot: ReleaseSnapshot, check: GitHubCheck) -> EvidenceRef:
    return EvidenceRef(
        evidence_id=f"github-check-{check.run_id}",
        source_type="github_check_run",
        source_id=str(check.run_id),
        url=check.url,
        fingerprint=_fingerprint(snapshot, check).value,
    )


def _blocking_finding(
    snapshot: ReleaseSnapshot, check: GitHubCheck
) -> ReadinessFinding:
    return ReadinessFinding(
        rule_id="checks.blocking_not_successful",
        severity="BLOCKING",
        summary=f"Blocking check '{check.name}' is not successful",
        required_action=f"Make check '{check.name}' complete successfully",
        evidence=(_check_evidence(snapshot, check),),
    )


def _missing_blocking_finding(
    snapshot: ReleaseSnapshot, check_name: str
) -> ReadinessFinding:
    facts = {
        "repository": snapshot.repository_full_name,
        "candidate_sha": snapshot.candidate_sha,
        "check_name": check_name,
        "missing": True,
    }
    digest = _digest(facts)
    digest_value = digest.removeprefix("sha256:")
    return ReadinessFinding(
        rule_id="checks.blocking_not_successful",
        severity="BLOCKING",
        summary=f"Blocking check '{check_name}' is missing",
        required_action=f"Run blocking check '{check_name}' on the candidate commit",
        evidence=(
            EvidenceRef(
                evidence_id=f"github-check-missing-{digest_value}",
                source_type="github_check_run",
                source_id=f"missing:{digest_value}",
                url=(
                    f"https://github.com/{snapshot.repository_full_name}/commit/"
                    f"{snapshot.candidate_sha}/checks"
                ),
                fingerprint=digest,
            ),
        ),
    )


def _advisory_finding(
    snapshot: ReleaseSnapshot,
    check: GitHubCheck,
    *,
    blocker: bool,
) -> ReadinessFinding:
    return ReadinessFinding(
        rule_id="checks.advisory_requires_decision",
        severity="BLOCKING" if blocker else "DECISION_REQUIRED",
        summary=(
            f"Advisory check '{check.name}' was marked as a release blocker"
            if blocker
            else f"Advisory check '{check.name}' requires a human decision"
        ),
        required_action=(
            f"Resolve release blocker decision for check '{check.name}'"
            if blocker
            else f"Accept the risk or mark check '{check.name}' as a release blocker"
        ),
        evidence=(_check_evidence(snapshot, check),),
    )


def _unknown_finding(snapshot: ReleaseSnapshot, check: GitHubCheck) -> ReadinessFinding:
    return ReadinessFinding(
        rule_id="checks.unknown_requires_classification",
        severity="DECISION_REQUIRED",
        summary=f"Check '{check.name}' has no release policy category",
        required_action=f"Classify check '{check.name}' in the release policy",
        evidence=(_check_evidence(snapshot, check),),
    )


def _finding_sort_key(finding: ReadinessFinding) -> tuple[str, str, str]:
    return (
        finding.rule_id,
        finding.summary,
        finding.evidence[0].fingerprint,
    )


def _check_canonical_key(check: GitHubCheck) -> str:
    return json.dumps(_jsonable(asdict(check)), sort_keys=True, separators=(",", ":"))


def _digest(facts: Mapping[str, object]) -> str:
    payload = json.dumps(facts, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


def _is_valid_policy_categories(value: object) -> bool:
    if not isinstance(value, Mapping) or len(value) > 100:
        return False
    for name, category in value.items():
        if not _is_canonical_name(name) or not isinstance(category, CheckCategory):
            return False
    return True


def _is_repository_name(repository: object) -> bool:
    if not isinstance(repository, str):
        return False
    parts = repository.split("/")
    return (
        len(repository) <= 255
        and len(parts) == 2
        and 0 < len(parts[0]) <= _MAX_OWNER_LENGTH
        and 0 < len(parts[1]) <= _MAX_REPOSITORY_LENGTH
        and all(
            part not in {".", ".."} and _REPOSITORY_PART.fullmatch(part) is not None
            for part in parts
        )
    )


def _check_coordinate(check: GitHubCheck) -> str:
    if (
        isinstance(check.run_id, int)
        and not isinstance(check.run_id, bool)
        and check.run_id > 0
    ):
        return str(check.run_id)
    digest = hashlib.sha256(_check_canonical_key(check).encode()).hexdigest()
    return f"record-{digest[:16]}"


def _is_canonical_name(value: object) -> bool:
    return (
        isinstance(value, str)
        and value == value.strip()
        and 0 < len(value) <= 255
        and all(character.isprintable() for character in value)
    )


def _is_sha(value: object) -> bool:
    return isinstance(value, str) and _SHA.fullmatch(value) is not None


def _is_aware(value: object) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _is_check_url(url: str, repository: str, run_id: int) -> bool:
    return _is_canonical_github_url(url, f"/{repository}/runs/{run_id}")


def _is_canonical_github_url(url: object, expected_path: str) -> bool:
    if not isinstance(url, str):
        return False
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


def _jsonable(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value
