from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import cast

import pytest

from release_intelligence.domain.models import (
    EvidenceRef,
    ReleaseSnapshot,
    SnapshotVersion,
)
from release_intelligence.domain.policy import CheckCategory, ReleasePolicy
from release_intelligence.domain.rules.checks import (
    CheckEvidenceError,
    CheckFingerprint,
    evaluate_checks,
)
from release_intelligence.ports.github import GitHubCheck

CANDIDATE_SHA = "a" * 40
NOW = datetime(2026, 8, 7, 12, tzinfo=UTC)


def _policy(
    categories: dict[str, CheckCategory] | None = None,
) -> ReleasePolicy:
    return ReleasePolicy(
        main_branch="main",
        candidate_branch="release/2026-08-10",
        milestone_number=7,
        code_change_label="code-change",
        release_ops_label="release-ops",
        blocker_label="release-blocker",
        check_categories=(
            {"api": CheckCategory.BLOCKING} if categories is None else categories
        ),
    )


def _check(
    *,
    name: str = "api",
    status: str = "completed",
    conclusion: str | None = "success",
    run_id: int = 101,
) -> GitHubCheck:
    return GitHubCheck(
        source_id=str(run_id),
        run_id=run_id,
        name=name,
        url=f"https://github.com/acme/widgets/actions/runs/{run_id}",
        head_sha=CANDIDATE_SHA,
        status=status,
        conclusion=conclusion,
        started_at=NOW,
        completed_at=NOW if status == "completed" else None,
    )


def _snapshot(*checks: GitHubCheck) -> ReleaseSnapshot:
    return ReleaseSnapshot(
        release_name="weekly",
        issue_number="7",
        milestone_number=7,
        issue_labels=(),
        linked_pr_numbers=(),
        issue_evidence=_placeholder_evidence(),
        snapshot_version=SnapshotVersion.GITHUB_V1,
        repository_id="repo-1",
        repository_full_name="acme/widgets",
        fetch_started_at=NOW,
        fetched_at=NOW,
        candidate_ref="release/2026-08-10",
        candidate_sha=CANDIDATE_SHA,
        checks=checks,
    )


def _placeholder_evidence() -> EvidenceRef:
    return EvidenceRef("unused", "unused", "unused", "https://github.com", "unused")


@dataclass(frozen=True, slots=True)
class _Decision:
    fingerprint: str
    blocks_release: bool


@pytest.mark.parametrize(
    ("status", "conclusion"),
    [
        ("queued", None),
        ("in_progress", None),
        ("completed", "failure"),
        ("completed", "cancelled"),
        ("completed", "timed_out"),
        ("completed", "skipped"),
        ("completed", "neutral"),
    ],
)
def test_blocking_check_requires_completed_success(
    status: str, conclusion: str | None
) -> None:
    findings = evaluate_checks(
        _snapshot(_check(status=status, conclusion=conclusion)),
        _policy(),
        decisions=(),
    )

    assert [finding.rule_id for finding in findings] == [
        "checks.blocking_not_successful"
    ]
    assert findings[0].blocks_release is True


def test_completed_success_is_the_only_passing_blocking_state() -> None:
    assert evaluate_checks(_snapshot(_check()), _policy(), decisions=()) == ()


def test_unknown_successful_check_still_requires_classification() -> None:
    findings = evaluate_checks(
        _snapshot(_check(name="new-scan")), _policy(), decisions=()
    )

    unknown = next(
        finding
        for finding in findings
        if finding.rule_id == "checks.unknown_requires_classification"
    )
    assert unknown.requires_decision is True


@pytest.mark.parametrize(
    "conclusion",
    [
        "action_required",
        "cancelled",
        "failure",
        "neutral",
        "skipped",
        "stale",
        "startup_failure",
        "timed_out",
    ],
)
def test_every_documented_non_success_conclusion_blocks_a_blocking_check(
    conclusion: str,
) -> None:
    findings = evaluate_checks(
        _snapshot(_check(conclusion=conclusion)), _policy(), decisions=()
    )

    assert len(findings) == 1
    assert findings[0].blocks_release


@pytest.mark.parametrize(
    "status",
    ["queued", "in_progress", "waiting", "requested", "pending"],
)
def test_every_documented_non_completed_status_blocks_a_blocking_check(
    status: str,
) -> None:
    findings = evaluate_checks(
        _snapshot(_check(status=status, conclusion=None)), _policy(), decisions=()
    )

    assert len(findings) == 1
    assert findings[0].blocks_release


def test_missing_configured_blocking_check_is_a_stable_blocker() -> None:
    first = evaluate_checks(_snapshot(), _policy(), decisions=())
    second = evaluate_checks(_snapshot(), _policy(), decisions=())

    assert [finding.rule_id for finding in first] == ["checks.blocking_not_successful"]
    assert first[0].blocks_release
    assert first[0].evidence == second[0].evidence
    assert first[0].evidence[0].url == (
        f"https://github.com/acme/widgets/tree/{CANDIDATE_SHA}"
    )


def test_missing_advisory_and_ignored_checks_have_no_finding() -> None:
    policy = _policy(
        {"security": CheckCategory.ADVISORY, "docs": CheckCategory.IGNORED}
    )

    assert evaluate_checks(_snapshot(), policy, decisions=()) == ()


def test_advisory_success_passes_and_ignored_failure_has_no_effect() -> None:
    policy = _policy(
        {"security": CheckCategory.ADVISORY, "docs": CheckCategory.IGNORED}
    )

    findings = evaluate_checks(
        _snapshot(
            _check(name="security", run_id=201),
            _check(name="docs", conclusion="failure", run_id=202),
        ),
        policy,
        decisions=(),
    )

    assert findings == ()


@pytest.mark.parametrize(
    ("status", "conclusion"),
    [("queued", None), ("in_progress", None), ("completed", "failure")],
)
def test_advisory_non_success_requires_a_decision(
    status: str, conclusion: str | None
) -> None:
    policy = _policy({"security": CheckCategory.ADVISORY})

    findings = evaluate_checks(
        _snapshot(
            _check(name="security", status=status, conclusion=conclusion, run_id=201)
        ),
        policy,
        decisions=(),
    )

    assert [finding.rule_id for finding in findings] == [
        "checks.advisory_requires_decision"
    ]
    assert findings[0].requires_decision


def _security_fingerprint() -> CheckFingerprint:
    return CheckFingerprint(
        repository="acme/widgets",
        candidate_sha=CANDIDATE_SHA,
        check_name="security",
        run_id=201,
        conclusion="failure",
    )


def test_matching_accepted_decision_resolves_advisory_finding() -> None:
    check = _check(name="security", conclusion="failure", run_id=201)
    policy = _policy({"security": CheckCategory.ADVISORY})

    findings = evaluate_checks(
        _snapshot(check),
        policy,
        decisions=(
            _Decision(fingerprint=_security_fingerprint().value, blocks_release=False),
        ),
    )

    assert findings == ()


def test_matching_blocker_decision_turns_advisory_into_a_blocker() -> None:
    check = _check(name="security", conclusion="failure", run_id=201)
    policy = _policy({"security": CheckCategory.ADVISORY})

    findings = evaluate_checks(
        _snapshot(check),
        policy,
        decisions=(
            _Decision(fingerprint=_security_fingerprint().value, blocks_release=True),
        ),
    )

    assert len(findings) == 1
    assert findings[0].rule_id == "checks.advisory_requires_decision"
    assert findings[0].blocks_release
    assert not findings[0].requires_decision


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("repository", "other/widgets"),
        ("candidate_sha", "b" * 40),
        ("check_name", "security-v2"),
        ("run_id", 202),
        ("conclusion", "cancelled"),
    ],
)
def test_any_fingerprint_change_invalidates_an_old_decision(
    field: str, changed: object
) -> None:
    check = _check(name="security", conclusion="failure", run_id=201)
    policy = _policy({"security": CheckCategory.ADVISORY})
    stale = replace(_security_fingerprint(), **{field: changed})

    findings = evaluate_checks(
        _snapshot(check),
        policy,
        decisions=(_Decision(fingerprint=stale.value, blocks_release=False),),
    )

    assert len(findings) == 1
    assert findings[0].requires_decision


def test_check_evidence_uses_the_exact_decision_fingerprint() -> None:
    finding = evaluate_checks(
        _snapshot(_check(conclusion="failure")), _policy(), decisions=()
    )[0]
    assert finding.evidence[0].fingerprint == (
        "sha256:b37be0d5dd38444c6bc42fd66a72e0a11b21dd160d664de7dc122707b0fca917"
    )


def test_one_shot_decision_iterable_applies_to_every_advisory_check() -> None:
    checks = (
        _check(name="security", conclusion="failure", run_id=201),
        _check(name="quality", conclusion="failure", run_id=202),
    )
    policy = _policy(
        {"security": CheckCategory.ADVISORY, "quality": CheckCategory.ADVISORY}
    )
    decisions = (
        _Decision(
            fingerprint=CheckFingerprint(
                repository="acme/widgets",
                candidate_sha=CANDIDATE_SHA,
                check_name=check.name,
                run_id=check.run_id,
                conclusion=check.conclusion,
            ).value,
            blocks_release=False,
        )
        for check in checks
    )

    assert evaluate_checks(_snapshot(*checks), policy, decisions=decisions) == ()


def test_exact_duplicate_check_records_are_idempotent() -> None:
    check = _check(conclusion="failure")

    one = evaluate_checks(_snapshot(check), _policy(), decisions=())
    duplicated = evaluate_checks(_snapshot(check, check), _policy(), decisions=())

    assert duplicated == one


def test_distinct_runs_for_one_check_name_are_insufficient_not_favorably_selected() -> (
    None
):
    with pytest.raises(CheckEvidenceError) as raised:
        evaluate_checks(
            _snapshot(_check(), _check(conclusion="failure", run_id=102)),
            _policy(),
            decisions=(),
        )

    assert raised.value.codes == ("check.conflicting_name:101:102",)
    assert raised.value.findings == ()


def test_conflicting_duplicate_run_id_is_insufficient() -> None:
    policy = _policy(
        {"api": CheckCategory.BLOCKING, "security": CheckCategory.ADVISORY}
    )

    with pytest.raises(CheckEvidenceError) as raised:
        evaluate_checks(
            _snapshot(_check(), _check(name="security")),
            policy,
            decisions=(),
        )

    assert raised.value.codes == ("check.conflicting_run:101",)
    assert raised.value.findings == ()


def test_unknown_checks_each_require_classification_in_stable_order() -> None:
    findings = evaluate_checks(
        _snapshot(
            _check(name="z-scan", run_id=202),
            _check(name="a-scan", run_id=201),
        ),
        _policy({}),
        decisions=(),
    )

    assert [finding.summary for finding in findings] == [
        "Check 'a-scan' has no release policy category",
        "Check 'z-scan' has no release policy category",
    ]


@pytest.mark.parametrize(
    "change",
    [
        {"head_sha": "b" * 40},
        {"head_sha": "ABC"},
        {"url": "http://github.com/acme/widgets/actions/runs/101"},
        {"url": "https://github.com/acme/widgets/actions/runs/102"},
        {"url": "https://github.com/acme/widgets/actions/runs/101?x=1"},
        {"url": "https://user@github.com/acme/widgets/actions/runs/101"},
        {"url": "https://github.com:443/acme/widgets/actions/runs/101"},
        {"source_id": "102"},
        {
            "run_id": 0,
            "source_id": "0",
            "url": "https://github.com/acme/widgets/actions/runs/0",
        },
        {"name": " api"},
        {"name": ""},
        {"name": "x" * 256},
        {"status": "unknown"},
        {"status": "completed", "conclusion": None},
        {"status": "queued", "conclusion": "success", "completed_at": None},
        {"status": "queued", "conclusion": None, "completed_at": NOW},
        {"status": "completed", "conclusion": "bogus"},
        {"status": cast(str, ["queued"])},
        {"run_id": cast(int, "101")},
        {"started_at": NOW.replace(tzinfo=None)},
        {"completed_at": NOW.replace(tzinfo=None)},
        {
            "started_at": datetime(2026, 8, 7, 13, tzinfo=UTC),
            "completed_at": NOW,
        },
    ],
)
def test_malformed_check_evidence_is_typed_insufficiency(
    change: dict[str, object],
) -> None:
    malformed = replace(_check(conclusion="failure"), **change)

    with pytest.raises(CheckEvidenceError) as raised:
        evaluate_checks(_snapshot(malformed), _policy(), decisions=())

    assert raised.value.codes
    assert all(code.startswith("check.") for code in raised.value.codes)


@pytest.mark.parametrize(
    "snapshot_change",
    [
        {"snapshot_version": SnapshotVersion.LEGACY},
        {"complete": False},
        {"repository_full_name": "../widgets"},
        {"repository_id": ""},
        {"candidate_sha": "not-a-sha"},
        {"candidate_ref": "release/2026-08-17"},
        {"fetch_started_at": None},
        {"fetched_at": NOW.replace(tzinfo=None)},
    ],
)
def test_malformed_snapshot_prerequisite_is_typed_insufficiency(
    snapshot_change: dict[str, object],
) -> None:
    with pytest.raises(CheckEvidenceError) as raised:
        evaluate_checks(
            replace(_snapshot(_check()), **snapshot_change),
            _policy(),
            decisions=(),
        )

    assert raised.value.findings == ()
    assert all(code.startswith("snapshot.") for code in raised.value.codes)


def test_malformed_policy_categories_are_typed_insufficiency() -> None:
    policy = ReleasePolicy.model_construct(
        **{
            **_policy().model_dump(),
            "check_categories": {"api": "BLOCKING"},
        }
    )

    with pytest.raises(CheckEvidenceError) as raised:
        evaluate_checks(_snapshot(_check()), policy, decisions=())

    assert raised.value.codes == ("policy.invalid_check_categories",)
    assert raised.value.findings == ()


def test_invalid_check_preserves_independent_blocking_finding() -> None:
    policy = _policy(
        {"api": CheckCategory.BLOCKING, "security": CheckCategory.ADVISORY}
    )
    invalid = replace(
        _check(name="security", run_id=202),
        url="https://github.com/acme/widgets/actions/runs/999",
    )

    with pytest.raises(CheckEvidenceError) as raised:
        evaluate_checks(
            _snapshot(_check(conclusion="failure"), invalid),
            policy,
            decisions=(),
        )

    assert [finding.rule_id for finding in raised.value.findings] == [
        "checks.blocking_not_successful"
    ]


def test_source_order_does_not_change_findings_or_insufficiency() -> None:
    policy = _policy(
        {"api": CheckCategory.BLOCKING, "security": CheckCategory.ADVISORY}
    )
    records = (
        _check(conclusion="failure"),
        replace(
            _check(name="security", run_id=202),
            url="https://github.com/acme/widgets/actions/runs/999",
        ),
    )

    outcomes = []
    for ordered in (records, tuple(reversed(records))):
        with pytest.raises(CheckEvidenceError) as raised:
            evaluate_checks(_snapshot(*ordered), policy, decisions=())
        outcomes.append((raised.value.codes, raised.value.findings))

    assert outcomes[0] == outcomes[1]
