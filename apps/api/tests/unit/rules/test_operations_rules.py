from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from itertools import permutations
from pathlib import Path
from typing import Any

import pytest

from release_intelligence.adapters.persistence.repositories import AnalysisRepository
from release_intelligence.domain.models import (
    EvidenceRef,
    ReleaseSnapshot,
    SnapshotVersion,
)
from release_intelligence.domain.policy import ReleasePolicy
from release_intelligence.domain.rules.operations import (
    OperationsEvidenceError,
    evaluate_operations,
)
from release_intelligence.ports.github import GitHubCheck, GitHubItem, GitHubItemKind

NOW = datetime(2026, 8, 7, 12, tzinfo=UTC)
REPOSITORY = "acme/widgets"
CANDIDATE_SHA = "a" * 40
CHECK_URL = "https://github.com/acme/widgets/runs/101"
POLICY = ReleasePolicy(
    main_branch="main",
    candidate_branch="release/2026-08-10",
    milestone_number=7,
    code_change_label="code-change",
    release_ops_label="release-ops",
    blocker_label="release-blocker",
    check_categories={},
)
FIXTURE_PATH = (
    Path(__file__).parents[2] / "fixtures" / "github" / "operations_cases.json"
)
COMPLETE_BODY = (
    "### Before release\nConfirm the backup.\n\n"
    "### During release\nMonitor the rollout.\n\n"
    "### After release\nVerify service health."
)


def _issue(
    number: int = 51,
    *,
    body: str = COMPLETE_BODY,
    assignees: tuple[str, ...] = ("release-owner",),
    labels: tuple[str, ...] = ("release-ops",),
    source_id: str | None = None,
    url: str | None = None,
) -> GitHubItem:
    return GitHubItem(
        source_id=source_id or str(number * 10),
        number=number,
        kind=GitHubItemKind.ISSUE,
        url=url or f"https://github.com/{REPOSITORY}/issues/{number}",
        state="open",
        labels=labels,
        assignees=assignees,
        milestone_number=7,
        created_at=NOW,
        updated_at=NOW,
        body=body,
    )


def _check(
    *,
    conclusion: str | None = "success",
    status: str = "completed",
    url: str = CHECK_URL,
    run_id: int = 101,
    name: str = "migration",
    source_id: str | None = None,
) -> GitHubCheck:
    return GitHubCheck(
        source_id=source_id or str(run_id),
        run_id=run_id,
        name=name,
        url=url,
        head_sha=CANDIDATE_SHA,
        status=status,
        conclusion=conclusion,
        started_at=NOW,
        completed_at=NOW if status == "completed" else None,
    )


def _snapshot(
    *items: GitHubItem, checks: tuple[GitHubCheck, ...] = ()
) -> ReleaseSnapshot:
    return ReleaseSnapshot(
        release_name="Milestone 7",
        issue_number="51",
        milestone_number=7,
        issue_labels=(),
        linked_pr_numbers=(),
        issue_evidence=EvidenceRef(
            "unused", "unused", "unused", "https://github.com", "unused"
        ),
        snapshot_version=SnapshotVersion.GITHUB_V1,
        repository_id="repo-1",
        repository_full_name=REPOSITORY,
        fetch_started_at=NOW,
        fetched_at=NOW,
        candidate_ref=POLICY.candidate_branch,
        candidate_sha=CANDIDATE_SHA,
        items=items,
        checks=checks,
    )


@pytest.fixture(scope="module")
def cases() -> dict[str, dict[str, Any]]:
    document = json.loads(FIXTURE_PATH.read_text())
    return {case["name"]: case for case in document["cases"]}


@pytest.mark.parametrize(
    "missing",
    ["owner", "before", "during", "after"],
)
def test_release_ops_requires_structured_fields(missing: str) -> None:
    body = COMPLETE_BODY
    assignees = ("release-owner",)
    if missing == "owner":
        assignees = ()
    else:
        heading = missing.capitalize() + " release"
        body = body.replace(f"### {heading}", f"### Not {heading}")

    findings = evaluate_operations(
        _snapshot(_issue(body=body, assignees=assignees)), POLICY
    )

    assert findings
    assert findings[0].rule_id == (
        "operations.owner_required"
        if missing == "owner"
        else "operations.section_required"
    )
    assert findings[0].blocks_release
    assert not findings[0].decision_allowed


@pytest.mark.parametrize(
    "case_name",
    ["missing_before", "blank_during", "placeholder_after"],
)
def test_fixture_backed_missing_blank_and_template_sections_fail(
    case_name: str, cases: dict[str, dict[str, Any]]
) -> None:
    case = cases[case_name]
    findings = evaluate_operations(_snapshot(_issue(body=case["body"])), POLICY)

    assert [finding.rule_id for finding in findings] == case["expected_rules"]


@pytest.mark.parametrize(
    "placeholder",
    ["-", "none", "_No response_", "<!-- Describe the release step -->"],
)
def test_release_ops_rejects_placeholder_section_values(placeholder: str) -> None:
    body = COMPLETE_BODY.replace("Confirm the backup.", placeholder)

    findings = evaluate_operations(_snapshot(_issue(body=body)), POLICY)

    assert [finding.rule_id for finding in findings] == ["operations.section_required"]


def test_complete_operations_without_migrations_has_no_false_positive(
    cases: dict[str, dict[str, Any]],
) -> None:
    case = cases["complete_without_migration"]

    assert evaluate_operations(_snapshot(_issue(body=case["body"])), POLICY) == ()


def test_migration_evidence_must_reference_successful_connected_repo_check() -> None:
    body = COMPLETE_BODY + f"\n\n### Migration evidence\n{CHECK_URL}"
    findings = evaluate_operations(
        _snapshot(_issue(body=body), checks=(_check(conclusion="failure"),)),
        POLICY,
    )

    assert findings[0].rule_id == "operations.migration_evidence_required"
    assert findings[0].evidence[-1].source_type == "github_check_run"


def test_successful_migration_check_satisfies_evidence(
    cases: dict[str, dict[str, Any]],
) -> None:
    case = cases["successful_migration"]

    assert (
        evaluate_operations(
            _snapshot(_issue(body=case["body"]), checks=(_check(),)), POLICY
        )
        == ()
    )


@pytest.mark.parametrize(
    ("checks", "expected_evidence_count"),
    [
        ((), 1),
        ((_check(status="in_progress", conclusion=None),), 2),
    ],
)
def test_missing_or_unknown_migration_check_is_blocking(
    checks: tuple[GitHubCheck, ...], expected_evidence_count: int
) -> None:
    body = COMPLETE_BODY + f"\n\n### Migration evidence\n{CHECK_URL}"

    finding = evaluate_operations(_snapshot(_issue(body=body), checks=checks), POLICY)[
        0
    ]

    assert finding.rule_id == "operations.migration_evidence_required"
    assert len(finding.evidence) == expected_evidence_count


@pytest.mark.parametrize(
    "migration_url",
    [
        "https://github.com/other/widgets/runs/101",
        "https://user:secret@github.com/acme/widgets/runs/101",
        "https://github.com/acme/widgets/runs/101?attempt=2",
        "- [ ] paste migration evidence URL",
    ],
)
def test_malformed_or_cross_repo_migration_evidence_is_insufficient(
    migration_url: str,
) -> None:
    body = COMPLETE_BODY + f"\n\n### Migration evidence\n{migration_url}"

    with pytest.raises(OperationsEvidenceError) as raised:
        evaluate_operations(_snapshot(_issue(body=body)), POLICY)

    assert raised.value.findings == ()
    assert raised.value.codes == ("migration.invalid_evidence:51",)


def test_duplicate_structured_heading_is_insufficient() -> None:
    body = COMPLETE_BODY + "\n\n### Before release\nA conflicting second value."

    with pytest.raises(OperationsEvidenceError) as raised:
        evaluate_operations(_snapshot(_issue(body=body)), POLICY)

    assert raised.value.findings == ()
    assert raised.value.codes == ("operations.conflicting_fields:51",)


def test_identical_duplicate_structured_heading_is_idempotent() -> None:
    body = COMPLETE_BODY + "\n\n### Before release\nConfirm the backup."

    assert evaluate_operations(_snapshot(_issue(body=body)), POLICY) == ()


def test_headings_inside_fenced_examples_are_not_structured_fields() -> None:
    body = f"```markdown\n{COMPLETE_BODY}\n```"

    findings = evaluate_operations(_snapshot(_issue(body=body)), POLICY)

    assert [finding.rule_id for finding in findings] == [
        "operations.section_required",
        "operations.section_required",
        "operations.section_required",
    ]


def test_headings_inside_html_blocks_are_not_structured_fields() -> None:
    body = f"<details>\n{COMPLETE_BODY}\n</details>"

    findings = evaluate_operations(_snapshot(_issue(body=body)), POLICY)

    assert [finding.rule_id for finding in findings] == [
        "operations.section_required",
        "operations.section_required",
        "operations.section_required",
    ]


def test_nested_heading_does_not_terminate_parent_section() -> None:
    body = COMPLETE_BODY.replace(
        "### Before release\nConfirm the backup.",
        "### Before release\n#### Backup details\nConfirm the backup.",
    )

    assert evaluate_operations(_snapshot(_issue(body=body)), POLICY) == ()


def test_atx_closing_markers_and_crlf_are_supported() -> None:
    body = COMPLETE_BODY.replace("### ", "### ").replace(" release\n", " release ###\n")
    body = body.replace("\n", "\r\n")

    assert evaluate_operations(_snapshot(_issue(body=body)), POLICY) == ()


@pytest.mark.parametrize("empty_markup", ["<br>", "<p></p>", "&nbsp;", "- none"])
def test_rendering_empty_or_decorated_placeholders_do_not_satisfy_sections(
    empty_markup: str,
) -> None:
    body = COMPLETE_BODY.replace("Confirm the backup.", empty_markup)

    findings = evaluate_operations(_snapshot(_issue(body=body)), POLICY)

    assert [finding.rule_id for finding in findings] == ["operations.section_required"]


def test_substring_heading_is_not_a_structured_field() -> None:
    body = COMPLETE_BODY.replace("### Before release", "### Before release notes")

    findings = evaluate_operations(_snapshot(_issue(body=body)), POLICY)

    assert [finding.rule_id for finding in findings] == ["operations.section_required"]


def test_job_url_uses_normalized_check_identity_not_job_id_namespace() -> None:
    job_url = "https://github.com/acme/widgets/actions/runs/800/jobs/700"
    body = COMPLETE_BODY + f"\n\n### Migration evidence\n{job_url}"

    assert (
        evaluate_operations(
            _snapshot(_issue(body=body), checks=(_check(url=job_url),)), POLICY
        )
        == ()
    )


def test_plausible_check_url_cannot_substitute_for_normalized_job_url() -> None:
    job_url = "https://github.com/acme/widgets/actions/runs/800/jobs/700"
    body = COMPLETE_BODY + f"\n\n### Migration evidence\n{CHECK_URL}"

    findings = evaluate_operations(
        _snapshot(_issue(body=body), checks=(_check(url=job_url),)), POLICY
    )

    assert [finding.rule_id for finding in findings] == [
        "operations.migration_evidence_required"
    ]


def test_conflicting_operation_record_preserves_independent_owner_finding() -> None:
    incomplete = _issue(51, assignees=())
    conflicting = replace(_issue(52), source_id="5200", body="")

    with pytest.raises(OperationsEvidenceError) as raised:
        evaluate_operations(_snapshot(incomplete, _issue(52), conflicting), POLICY)

    assert [finding.rule_id for finding in raised.value.findings] == [
        "operations.owner_required"
    ]
    assert raised.value.codes == ("issue.conflicting_records:52",)


def test_duplicate_heading_error_preserves_missing_owner_finding() -> None:
    body = COMPLETE_BODY + "\n\n### Before release\nConflicting value."

    with pytest.raises(OperationsEvidenceError) as raised:
        evaluate_operations(_snapshot(_issue(body=body, assignees=())), POLICY)

    assert [finding.rule_id for finding in raised.value.findings] == [
        "operations.owner_required"
    ]
    assert raised.value.codes == ("operations.conflicting_fields:51",)


@pytest.mark.parametrize(
    "owner",
    [
        "release owner",
        " release-owner",
        "release-owner ",
        "rélease-owner",
        "release\nowner",
        "-release-owner",
        "release-owner-",
        "release--owner",
        "x" * 40,
    ],
)
def test_invalid_owner_is_typed_but_preserves_missing_section(
    owner: str,
) -> None:
    body = COMPLETE_BODY.replace("### Before release", "### Other field")

    with pytest.raises(OperationsEvidenceError) as raised:
        evaluate_operations(_snapshot(_issue(body=body, assignees=(owner,))), POLICY)

    assert [finding.rule_id for finding in raised.value.findings] == [
        "operations.section_required"
    ]
    assert raised.value.codes == ("operations.invalid_owner:51",)


def test_maximum_length_canonical_owner_is_valid() -> None:
    assert evaluate_operations(_snapshot(_issue(assignees=("x" * 39,))), POLICY) == ()


def test_unrelated_invalid_check_preserves_matching_failed_migration_finding() -> None:
    body = COMPLETE_BODY + f"\n\n### Migration evidence\n{CHECK_URL}"
    invalid = _check(
        run_id=202,
        name="unrelated",
        url="https://github.com/acme/widgets/runs/202",
        source_id="wrong",
    )

    with pytest.raises(OperationsEvidenceError) as raised:
        evaluate_operations(
            _snapshot(
                _issue(body=body),
                checks=(_check(conclusion="failure"), invalid),
            ),
            POLICY,
        )

    assert [finding.rule_id for finding in raised.value.findings] == [
        "operations.migration_evidence_required"
    ]
    assert raised.value.codes == ("check.invalid_identity:202",)


def test_mixed_evidence_findings_and_codes_are_permutation_stable() -> None:
    missing_owner = _issue(51, assignees=())
    invalid_owner = _issue(
        52,
        assignees=("bad owner",),
        body=COMPLETE_BODY.replace("### Before release", "### Other field"),
    )
    expected: tuple[object, ...] | None = None

    for item_order in permutations((missing_owner, invalid_owner)):
        with pytest.raises(OperationsEvidenceError) as raised:
            evaluate_operations(_snapshot(*item_order), POLICY)
        outcome = (raised.value.findings, raised.value.codes)
        if expected is None:
            expected = outcome
        assert outcome == expected

    assert expected is not None
    findings, codes = expected
    assert [finding.rule_id for finding in findings] == [
        "operations.owner_required",
        "operations.section_required",
    ]
    assert codes == ("operations.invalid_owner:52",)


def test_exact_duplicate_operations_are_idempotent() -> None:
    operation = _issue(assignees=())

    assert evaluate_operations(_snapshot(operation, operation), POLICY) == (
        evaluate_operations(_snapshot(operation), POLICY)
    )


def test_structured_issue_body_survives_snapshot_persistence_round_trip() -> None:
    snapshot = _snapshot(_issue())

    payload = AnalysisRepository._snapshot_payload(snapshot)

    assert AnalysisRepository._snapshot_from_payload(payload) == snapshot
    assert AnalysisRepository._snapshot_from_payload(payload).items[0].body == (
        COMPLETE_BODY
    )


def test_legacy_snapshot_payload_without_issue_body_uses_safe_default() -> None:
    snapshot = _snapshot(_issue(body=""))
    payload = AnalysisRepository._snapshot_payload(snapshot)
    raw_items = payload["items"]
    assert isinstance(raw_items, list)
    assert isinstance(raw_items[0], dict)
    raw_items[0].pop("body")

    assert AnalysisRepository._snapshot_from_payload(payload) == snapshot
