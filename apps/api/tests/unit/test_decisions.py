from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from release_intelligence.application.decisions import (
    DecisionKind,
    DecisionService,
    DecisionValidationError,
)
from release_intelligence.domain.models import (
    EvidenceRef,
    ReleaseSnapshot,
    SnapshotVersion,
)
from release_intelligence.domain.policy import CheckCategory, ReleasePolicy
from release_intelligence.domain.rules.checks import CheckFingerprint, evaluate_checks
from release_intelligence.ports.github import GitHubCheck

CANDIDATE_SHA = "a" * 40
NOW = datetime(2026, 8, 7, 12, tzinfo=UTC)
ACTOR = "github:7"
FINGERPRINT = CheckFingerprint(
    repository="acme/widgets",
    candidate_sha=CANDIDATE_SHA,
    check_name="security",
    run_id=201,
    conclusion="failure",
).value
POLICY = ReleasePolicy(
    main_branch="main",
    candidate_branch="release/2026-08-10",
    milestone_number=7,
    code_change_label="code-change",
    release_ops_label="release-ops",
    blocker_label="release-blocker",
    check_categories={"security": CheckCategory.ADVISORY},
)


@pytest.fixture
def decision_service() -> DecisionService:
    return DecisionService(
        clock=lambda: NOW,
        id_factory=lambda: UUID("11111111-1111-1111-1111-111111111111"),
    )


def advisory_check(*, run_id: int = 201) -> GitHubCheck:
    return GitHubCheck(
        source_id=str(run_id),
        run_id=run_id,
        name="security",
        url=f"https://github.com/acme/widgets/runs/{run_id}",
        head_sha=CANDIDATE_SHA,
        status="completed",
        conclusion="failure",
        started_at=NOW,
        completed_at=NOW,
    )


def snapshot(check: GitHubCheck) -> ReleaseSnapshot:
    return ReleaseSnapshot(
        release_name="weekly",
        issue_number="7",
        milestone_number=7,
        issue_labels=(),
        linked_pr_numbers=(),
        issue_evidence=EvidenceRef(
            "unused", "unused", "unused", "https://github.com", "unused"
        ),
        snapshot_version=SnapshotVersion.GITHUB_V1,
        repository_id="repo-1",
        repository_full_name="acme/widgets",
        fetch_started_at=NOW,
        fetched_at=NOW,
        candidate_ref="release/2026-08-10",
        candidate_sha=CANDIDATE_SHA,
        checks=(check,),
    )


def test_decision_requires_non_blank_reason(
    decision_service: DecisionService,
) -> None:
    with pytest.raises(DecisionValidationError):
        decision_service.record(FINGERPRINT, DecisionKind.ACCEPTED_RISK, "  ", ACTOR)


def test_recorded_decision_is_canonical_and_satisfies_check_protocol(
    decision_service: DecisionService,
) -> None:
    decision = decision_service.record(
        FINGERPRINT,
        DecisionKind.RELEASE_BLOCKER,
        "  Security review must finish.  ",
        ACTOR,
    )

    assert decision.id == UUID("11111111-1111-1111-1111-111111111111")
    assert decision.fingerprint == FINGERPRINT
    assert decision.reason == "Security review must finish."
    assert decision.actor_id == ACTOR
    assert decision.decided_at == NOW
    assert decision.blocks_release is True
    findings = evaluate_checks(
        snapshot(advisory_check()), POLICY, decisions=(decision,)
    )
    assert findings[0].blocks_release is True
    assert findings[0].requires_decision is False


def test_changed_run_id_invalidates_old_decision(
    decision_service: DecisionService,
) -> None:
    accepted_decision = decision_service.record(
        FINGERPRINT, DecisionKind.ACCEPTED_RISK, "Reviewed by release lead", ACTOR
    )
    changed = replace(
        advisory_check(),
        run_id=202,
        source_id="202",
        url="https://github.com/acme/widgets/runs/202",
    )

    findings = evaluate_checks(
        snapshot(changed), POLICY, decisions=(accepted_decision,)
    )

    assert findings[0].requires_decision is True


@pytest.mark.parametrize(
    ("fingerprint", "actor"),
    [
        ("", ACTOR),
        ("sha256:not-a-digest", ACTOR),
        (FINGERPRINT, "  "),
    ],
)
def test_decision_rejects_untrusted_identity_fields(
    decision_service: DecisionService, fingerprint: str, actor: str
) -> None:
    with pytest.raises(DecisionValidationError):
        decision_service.record(
            fingerprint, DecisionKind.ACCEPTED_RISK, "Reviewed", actor
        )


def test_accepted_risk_never_blocks_release(
    decision_service: DecisionService,
) -> None:
    decision = decision_service.record(
        FINGERPRINT, DecisionKind.ACCEPTED_RISK, "Reviewed", ACTOR
    )

    assert decision.blocks_release is False
