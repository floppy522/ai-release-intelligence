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
        url=f"https://github.com/acme/widgets/runs/{run_id}",
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


class _ExplodingDecision:
    def __init__(self, error: BaseException | None = None) -> None:
        self._error = error or ValueError("untrusted decision")

    @property
    def fingerprint(self) -> str:
        raise self._error

    @property
    def blocks_release(self) -> bool:
        raise self._error


class _ExplodingBlocksDecision:
    fingerprint = CheckFingerprint(
        repository="acme/widgets",
        candidate_sha=CANDIDATE_SHA,
        check_name="security",
        run_id=201,
        conclusion="failure",
    ).value

    @property
    def blocks_release(self) -> bool:
        raise RuntimeError("untrusted decision")


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


def test_check_run_uses_the_check_runs_api_html_url() -> None:
    check = replace(_check(), url="https://github.com/acme/widgets/runs/101")

    assert evaluate_checks(_snapshot(check), _policy(), decisions=()) == ()


def test_workflow_run_url_is_not_valid_check_run_evidence() -> None:
    workflow_run = replace(
        _check(), url="https://github.com/acme/widgets/actions/runs/101"
    )
    with pytest.raises(CheckEvidenceError) as raised:
        evaluate_checks(_snapshot(workflow_run), _policy(), decisions=())

    assert raised.value.codes == ("check.invalid_identity:101",)


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/acme/widgets/runs/800/jobs/700",
        "https://github.com/acme/widgets/actions/runs/800/job/700",
        "https://github.com/acme/widgets/actions/runs/800/jobs/700",
    ],
)
def test_check_run_accepts_known_actions_job_urls(url: str) -> None:
    assert evaluate_checks(
        _snapshot(replace(_check(), url=url)), _policy(), decisions=()
    ) == ()


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/acme/widgets/runs/102",
        "https://github.com/acme/widgets/runs/0/jobs/700",
        "https://github.com/acme/widgets/runs/800/jobs/0",
        "https://github.com/acme/widgets/runs/800/jobs/not-a-number",
        f"https://github.com/acme/widgets/runs/{2**63}/jobs/700",
        f"https://github.com/acme/widgets/runs/800/jobs/{2**63}",
        f"https://github.com/acme/widgets/runs/{'9' * 5000}/jobs/700",
        "https://github.com/acme/widgets/runs/800/jobs/700/extra",
        "https://github.com/acme/widgets/actions/runs/0/jobs/700",
        "https://github.com/acme/widgets/actions/runs/800/job/not-a-number",
        f"https://github.com/acme/widgets/actions/runs/800/jobs/{2**63}",
        "https://github.com/acme/widgets/actions/runs/800/jobs/700/extra",
        "https://github.com/acme/widgets/actions/runs/800/job/700?attempt=2",
        "https://github.com/other/widgets/actions/runs/800/jobs/700",
    ],
)
def test_check_run_rejects_unsafe_or_ambiguous_actions_paths(url: str) -> None:
    with pytest.raises(CheckEvidenceError) as raised:
        evaluate_checks(
            _snapshot(replace(_check(), url=url)), _policy(), decisions=()
        )

    assert raised.value.codes == ("check.invalid_identity:101",)


def test_actions_job_url_preserves_check_identity_and_raw_safe_evidence_url() -> None:
    url = "https://github.com/acme/widgets/actions/runs/800/jobs/700"
    finding = evaluate_checks(
        _snapshot(replace(_check(conclusion="failure"), url=url)),
        _policy(),
        decisions=(),
    )[0]

    assert finding.evidence[0].source_id == "101"
    assert finding.evidence[0].url == url
    assert finding.evidence[0].fingerprint == (
        "sha256:b37be0d5dd38444c6bc42fd66a72e0a11b21dd160d664de7dc122707b0fca917"
    )


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
        f"https://github.com/acme/widgets/commit/{CANDIDATE_SHA}/checks"
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
    "decision",
    [
        _Decision(fingerprint=cast(str, None), blocks_release=False),
        _Decision(fingerprint=cast(str, 0), blocks_release=False),
        _Decision(fingerprint="", blocks_release=False),
        _Decision(
            fingerprint=_security_fingerprint().value,
            blocks_release=cast(bool, 0),
        ),
        _Decision(
            fingerprint=_security_fingerprint().value,
            blocks_release=cast(bool, ""),
        ),
        _ExplodingDecision(),
        _ExplodingDecision(RuntimeError("untrusted decision")),
        _ExplodingDecision(LookupError("untrusted decision")),
        _ExplodingBlocksDecision(),
    ],
)
def test_malformed_decision_is_typed_insufficiency(decision: object) -> None:
    check = _check(name="security", conclusion="failure", run_id=201)
    policy = _policy({"security": CheckCategory.ADVISORY})

    with pytest.raises(CheckEvidenceError) as raised:
        evaluate_checks(
            _snapshot(check),
            policy,
            decisions=(cast(_Decision, decision),),
        )

    assert raised.value.codes == ("decision.invalid:201",)
    assert raised.value.findings == ()


@pytest.mark.parametrize("error", [KeyboardInterrupt(), SystemExit(2)])
def test_decision_process_control_exceptions_propagate(error: BaseException) -> None:
    check = _check(name="security", conclusion="failure", run_id=201)
    policy = _policy({"security": CheckCategory.ADVISORY})

    with pytest.raises(type(error)):
        evaluate_checks(
            _snapshot(check),
            policy,
            decisions=(cast(_Decision, _ExplodingDecision(error)),),
        )


def test_exact_duplicate_decisions_are_idempotent() -> None:
    check = _check(name="security", conclusion="failure", run_id=201)
    policy = _policy({"security": CheckCategory.ADVISORY})
    decision = _Decision(
        fingerprint=_security_fingerprint().value, blocks_release=False
    )

    assert (
        evaluate_checks(_snapshot(check), policy, decisions=(decision, decision)) == ()
    )


def test_conflicting_matching_decisions_are_insufficient() -> None:
    check = _check(name="security", conclusion="failure", run_id=201)
    policy = _policy({"security": CheckCategory.ADVISORY})
    fingerprint = _security_fingerprint().value

    with pytest.raises(CheckEvidenceError) as raised:
        evaluate_checks(
            _snapshot(check),
            policy,
            decisions=(
                _Decision(fingerprint=fingerprint, blocks_release=False),
                _Decision(fingerprint=fingerprint, blocks_release=True),
            ),
        )

    assert raised.value.codes == ("decision.conflicting:201",)
    assert raised.value.findings == ()


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


@pytest.mark.parametrize(
    "conflicting",
    [
        replace(_check(), conclusion="failure"),
        replace(_check(), source_id="different-source"),
    ],
)
def test_same_run_and_name_with_contradictory_fields_is_insufficient(
    conflicting: GitHubCheck,
) -> None:
    with pytest.raises(CheckEvidenceError) as raised:
        evaluate_checks(
            _snapshot(_check(), _check(), conflicting),
            _policy(),
            decisions=(),
        )

    assert raised.value.codes == ("check.conflicting_run:101",)
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
        {"url": "http://github.com/acme/widgets/runs/101"},
        {"url": "https://github.com/acme/widgets/runs/102"},
        {"url": "https://github.com/acme/widgets/runs/101?x=1"},
        {"url": "https://user@github.com/acme/widgets/runs/101"},
        {"url": "https://github.com:443/acme/widgets/runs/101"},
        {"source_id": "102"},
        {
            "run_id": 0,
            "source_id": "0",
            "url": "https://github.com/acme/widgets/runs/0",
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
    "change",
    [
        {"started_at": None},
        {"completed_at": None},
        {"started_at": NOW.replace(tzinfo=None)},
        {"completed_at": NOW.replace(tzinfo=None)},
        {"started_at": datetime(2026, 8, 7, 13, tzinfo=UTC)},
        {"completed_at": datetime(2026, 8, 7, 13, tzinfo=UTC)},
    ],
)
def test_success_with_incomplete_or_future_timestamps_is_insufficient(
    change: dict[str, object],
) -> None:
    check = replace(
        _check(),
        url="https://github.com/acme/widgets/runs/101",
        **change,
    )

    with pytest.raises(CheckEvidenceError) as raised:
        evaluate_checks(_snapshot(check), _policy(), decisions=())

    assert raised.value.codes == ("check.invalid_matrix:101",)
    assert raised.value.findings == ()


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


@pytest.mark.parametrize(
    "repository",
    [f"{'o' * 40}/widgets", f"acme/{'r' * 101}"],
)
def test_repository_component_overflow_is_typed_insufficiency(
    repository: str,
) -> None:
    with pytest.raises(CheckEvidenceError) as raised:
        evaluate_checks(
            replace(_snapshot(), repository_full_name=repository),
            _policy(),
            decisions=(),
        )

    assert raised.value.codes == ("snapshot.invalid_repository",)


def test_repository_component_limits_are_accepted() -> None:
    repository = f"{'o' * 39}/{'r' * 100}"

    assert (
        evaluate_checks(
            replace(_snapshot(), repository_full_name=repository),
            _policy({}),
            decisions=(),
        )
        == ()
    )


def test_run_id_must_fit_a_signed_bigint() -> None:
    overflow = 2**63
    check = replace(
        _check(run_id=overflow),
        url=f"https://github.com/acme/widgets/runs/{overflow}",
    )

    with pytest.raises(CheckEvidenceError) as raised:
        evaluate_checks(_snapshot(check), _policy(), decisions=())

    code = raised.value.codes[0]
    assert code.startswith("check.invalid_identity:run-")
    assert len(code) == len("check.invalid_identity:run-") + 16
    assert str(overflow) not in code


def test_huge_run_id_error_coordinate_is_fixed_and_deterministic() -> None:
    huge = 2**1000
    checks = (
        replace(_check(run_id=huge), url=f"https://github.com/acme/widgets/runs/{huge}"),
        replace(
            _check(run_id=huge),
            source_id="conflicting",
            url=f"https://github.com/acme/widgets/runs/{huge}",
        ),
    )

    codes = []
    for ordered in (checks, tuple(reversed(checks))):
        with pytest.raises(CheckEvidenceError) as raised:
            evaluate_checks(_snapshot(*ordered), _policy(), decisions=())
        codes.append(raised.value.codes)

    assert codes[0] == codes[1]
    assert codes[0][0].startswith("check.conflicting_run:run-")
    assert len(codes[0][0]) == len("check.conflicting_run:run-") + 16
    assert str(huge) not in codes[0][0]


def test_maximum_signed_bigint_run_id_fits_persisted_source_identity() -> None:
    run_id = 2**63 - 1
    check = _check(run_id=run_id, conclusion="failure")

    finding = evaluate_checks(_snapshot(check), _policy(), decisions=())[0]

    assert finding.evidence[0].source_id == str(run_id)
    assert len(finding.evidence[0].source_id) <= 255


def test_missing_check_evidence_fits_persistence_and_links_to_commit_checks() -> None:
    check_name = "x" * 255
    finding = evaluate_checks(
        _snapshot(),
        _policy({check_name: CheckCategory.BLOCKING}),
        decisions=(),
    )[0]
    evidence = finding.evidence[0]

    assert len(evidence.evidence_id) <= 255
    assert len(evidence.source_id) <= 255
    assert check_name not in evidence.source_id
    assert evidence.url == (
        f"https://github.com/acme/widgets/commit/{CANDIDATE_SHA}/checks"
    )


def test_invalid_check_preserves_independent_blocking_finding() -> None:
    policy = _policy(
        {"api": CheckCategory.BLOCKING, "security": CheckCategory.ADVISORY}
    )
    invalid = replace(
        _check(name="security", run_id=202),
        url="https://github.com/acme/widgets/runs/999",
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
            url="https://github.com/acme/widgets/runs/999",
        ),
    )

    outcomes = []
    for ordered in (records, tuple(reversed(records))):
        with pytest.raises(CheckEvidenceError) as raised:
            evaluate_checks(_snapshot(*ordered), policy, decisions=())
        outcomes.append((raised.value.codes, raised.value.findings))

    assert outcomes[0] == outcomes[1]
