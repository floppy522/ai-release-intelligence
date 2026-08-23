from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from release_intelligence.adapters.persistence.repositories import (
    reassess_stored_decision,
)
from release_intelligence.application.decisions import (
    DecisionConflictError,
    DecisionKind,
    HumanDecision,
)
from release_intelligence.domain.assessment import assess
from release_intelligence.domain.models import (
    EvidenceRef,
    ReleaseSnapshot,
    ReleaseStatus,
    SnapshotVersion,
)
from release_intelligence.domain.policy import CheckCategory, ReleasePolicy
from release_intelligence.ports.github import GitHubCheck

NOW = datetime(2026, 8, 7, 14, 30, tzinfo=UTC)
SHA = "a" * 40


def _policy() -> ReleasePolicy:
    return ReleasePolicy(
        main_branch="main",
        candidate_branch="release/2026-08-10",
        milestone_number=7,
        code_change_label="code-change",
        release_ops_label="release-ops",
        blocker_label="release-blocker",
        check_categories={"security": CheckCategory.ADVISORY},
    )


def _snapshot() -> ReleaseSnapshot:
    return ReleaseSnapshot(
        release_name="Milestone 7",
        issue_number="",
        milestone_number=7,
        issue_labels=(),
        linked_pr_numbers=(),
        issue_evidence=EvidenceRef(
            "milestone-7",
            "github_milestone",
            "7",
            "https://github.com/acme/widgets/milestone/7",
            "sha256:" + "7" * 64,
        ),
        snapshot_version=SnapshotVersion.GITHUB_V1,
        repository_id="987654",
        repository_full_name="acme/widgets",
        fetch_started_at=NOW,
        fetched_at=NOW,
        candidate_ref="release/2026-08-10",
        candidate_sha=SHA,
        checks=(
            GitHubCheck(
                source_id="201",
                run_id=201,
                name="security",
                url="https://github.com/acme/widgets/runs/201",
                head_sha=SHA,
                status="completed",
                conclusion="failure",
                started_at=NOW,
                completed_at=NOW,
            ),
        ),
    )


def _decision(snapshot: ReleaseSnapshot, *, at: datetime = NOW) -> HumanDecision:
    assessment = assess(snapshot, _policy(), (), now=NOW)
    fingerprint = next(
        finding.evidence[0].fingerprint
        for finding in assessment.findings
        if finding.rule_id == "checks.advisory_requires_decision"
    )
    return HumanDecision(
        id=uuid4(),
        fingerprint=fingerprint,
        kind=DecisionKind.ACCEPTED_RISK,
        reason="Reviewed",
        actor_id="github:7",
        decided_at=at,
    )


def test_reassessment_rejects_persisted_insufficient_status() -> None:
    candidate = _snapshot()

    with pytest.raises(DecisionConflictError):
        reassess_stored_decision(
            snapshot=candidate,
            policy=_policy(),
            active_decisions=(),
            decision=_decision(candidate),
            persisted_status=ReleaseStatus.INSUFFICIENT_DATA,
        )


def test_reassessment_rejects_snapshot_that_became_stale() -> None:
    candidate = _snapshot()

    with pytest.raises(DecisionConflictError):
        reassess_stored_decision(
            snapshot=candidate,
            policy=_policy(),
            active_decisions=(),
            decision=_decision(candidate, at=NOW + timedelta(minutes=11)),
            persisted_status=ReleaseStatus.NEEDS_DECISION,
        )


def test_full_reassessment_replays_release_blocker_decision() -> None:
    candidate = _snapshot()
    decision = _decision(candidate)
    decision = replace(decision, kind=DecisionKind.RELEASE_BLOCKER)

    _current, reassessed = reassess_stored_decision(
        snapshot=candidate,
        policy=_policy(),
        active_decisions=(),
        decision=decision,
        persisted_status=ReleaseStatus.NEEDS_DECISION,
    )

    assert reassessed.status is ReleaseStatus.NOT_READY
    assert any(
        finding.rule_id == "checks.advisory_requires_decision"
        and finding.severity == "BLOCKING"
        for finding in reassessed.findings
    )
