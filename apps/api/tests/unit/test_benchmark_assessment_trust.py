from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from release_intelligence.application.explanations import (
    ExplanationValidator,
    build_explanation_input,
)
from release_intelligence.benchmark.review import (
    ClaimsDocument,
    ReviewDocument,
    StoredAssessmentArtifact,
    evaluate_review,
    export_claim_packet,
    export_stored_assessment,
    main,
)
from release_intelligence.domain.models import (
    EvidenceRef,
    ReadinessAssessment,
    ReadinessFinding,
    ReleaseSnapshot,
    ReleaseStatus,
    SnapshotVersion,
)
from release_intelligence.ports.ai import (
    AIExplanation,
    ExplanationAction,
    ExplanationGroup,
)
from release_intelligence.ports.repositories import (
    StoredAnalysisRun,
    StoredFindingMetadata,
)

NOW = datetime(2026, 8, 16, 12, tzinfo=UTC)
RUN_ID = UUID("20000000-0000-0000-0000-000000000001")
FINDING_ID = UUID("10000000-0000-0000-0000-000000000001")


def _run(*, status: ReleaseStatus = ReleaseStatus.NOT_READY) -> StoredAnalysisRun:
    evidence = EvidenceRef(
        evidence_id="github-check-101",
        source_type="github_check_run",
        source_id="101",
        url="https://github.com/acme/widgets/runs/101",
        fingerprint="sha256:" + "1" * 64,
    )
    finding = ReadinessFinding(
        rule_id="checks.blocking_not_successful",
        severity="BLOCKING",
        summary="Blocking check failed",
        required_action="Fix the blocking check",
        evidence=(evidence,),
    )
    snapshot = ReleaseSnapshot(
        release_name="Release 2026.08.17",
        issue_number="",
        milestone_number=7,
        issue_labels=(),
        linked_pr_numbers=(),
        issue_evidence=evidence,
        snapshot_version=SnapshotVersion.GITHUB_V1,
        repository_id="77",
        repository_full_name="acme/widgets",
        fetch_started_at=NOW,
        fetched_at=NOW,
        candidate_ref="release/2026-08-17",
        candidate_sha="a" * 40,
    )
    return StoredAnalysisRun(
        id=RUN_ID,
        snapshot=snapshot,
        findings=(finding,),
        assessment=ReadinessAssessment(status=status, findings=(finding,)),
        policy_version="configuration:1",
        source_fetched_at=NOW,
        finding_metadata=(
            StoredFindingMetadata(finding_id=FINDING_ID, finding=finding),
        ),
    )


def _explanation(run: StoredAnalysisRun) -> AIExplanation:
    source = build_explanation_input(run)
    raw = AIExplanation(
        summary="model prose is replaced",
        groups=(
            ExplanationGroup(
                title="blocking",
                explanation="blocking",
                severity="BLOCKING",
                finding_ids=(str(FINDING_ID),),
                evidence_ids=("github-check-101",),
            ),
        ),
        actions=(
            ExplanationAction(
                action="Fix the blocking check",
                finding_ids=(str(FINDING_ID),),
                evidence_ids=("github-check-101",),
            ),
        ),
        limitations=source.limitations,
        confidence="LOW",
        finding_ids=(str(FINDING_ID),),
        evidence_ids=("github-check-101",),
    )
    return ExplanationValidator(source).validate(raw)


def _packet(run: StoredAnalysisRun | None = None) -> ClaimsDocument:
    selected = run or _run()
    return export_claim_packet(run=selected, explanation=_explanation(selected))


def _review(packet: ClaimsDocument) -> ReviewDocument:
    return ReviewDocument.model_validate(
        {
            "version": packet.version,
            "packet_hash": packet.packet_hash,
            "decisions": [
                {
                    "claim_id": claim.claim_id,
                    "verdict": "supported",
                    "reviewer": "release-reviewer",
                    "rationale": "Trusted deterministic facts support this claim.",
                    "reviewed_at": NOW.isoformat(),
                }
                for claim in packet.claims
            ],
        }
    )


def test_happy_path_exports_from_immutable_stored_run_and_trusted_artifact() -> None:
    run = _run()
    artifact = export_stored_assessment(run)
    packet = _packet(run)

    result = evaluate_review(packet, _review(packet), artifact)

    assert result.accepted is True
    assert packet.assessment.analysis_run_id == str(run.id)
    assert packet.assessment.repository_id == run.snapshot.repository_id
    assert packet.assessment.snapshot_fingerprint == artifact.snapshot_fingerprint
    assert packet.assessment.assessment_digest == artifact.assessment_digest


def test_direct_arbitrary_explanation_input_cannot_be_exported() -> None:
    run = _run()
    source = build_explanation_input(run)

    with pytest.raises(TypeError):
        export_claim_packet(  # type: ignore[call-arg]
            scenario_id="invented",
            source=source,
            explanation=_explanation(run),
        )


def test_self_consistent_invented_ready_blocking_packet_fails_trusted_boundary() -> (
    None
):
    trusted = export_stored_assessment(_run())
    invented_run = _run(status=ReleaseStatus.READY)
    invented_packet = _packet(invented_run)
    invented_review = _review(invented_packet)

    with pytest.raises(ValueError, match="trusted assessment"):
        evaluate_review(invented_packet, invented_review, trusted)


@pytest.mark.parametrize(
    "mismatch", ["run", "repository", "snapshot", "snapshot_payload", "facts"]
)
def test_run_repository_snapshot_or_fact_mismatch_fails(
    mismatch: str,
) -> None:
    run = _run()
    packet = _packet(run)
    changed = run
    if mismatch == "run":
        changed = replace(run, id=UUID("20000000-0000-0000-0000-000000000002"))
    elif mismatch == "repository":
        changed = replace(
            run,
            snapshot=replace(
                run.snapshot,
                repository_id="88",
                repository_full_name="acme/other",
            ),
        )
    elif mismatch == "snapshot":
        changed = replace(
            run,
            snapshot=replace(run.snapshot, candidate_sha="b" * 40),
        )
    elif mismatch == "snapshot_payload":
        changed = replace(
            run,
            snapshot=replace(run.snapshot, complete=False),
        )
    else:
        finding = replace(run.assessment.findings[0], summary="Invented summary")
        changed = replace(
            run,
            findings=(finding,),
            assessment=replace(run.assessment, findings=(finding,)),
            finding_metadata=(
                StoredFindingMetadata(finding_id=FINDING_ID, finding=finding),
            ),
        )

    with pytest.raises(ValueError, match="trusted assessment"):
        evaluate_review(packet, _review(packet), export_stored_assessment(changed))


def test_changed_trusted_artifact_fails_its_canonical_digest() -> None:
    payload = export_stored_assessment(_run()).model_dump(mode="json")
    payload["source"]["findings"][0]["summary"] = "Changed after export"

    with pytest.raises(ValidationError):
        StoredAssessmentArtifact.model_validate(payload)


def test_trusted_export_supports_immutable_incomplete_snapshot_identity() -> None:
    run = _run()
    incomplete = replace(
        run,
        snapshot=replace(run.snapshot, complete=False, candidate_sha=""),
    )

    artifact = export_stored_assessment(incomplete)

    assert artifact.snapshot.candidate_sha == ""
    assert (
        artifact.snapshot_fingerprint
        != export_stored_assessment(run).snapshot_fingerprint
    )


def test_review_cli_fails_closed_without_independent_assessment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet = _packet()
    claims_path = tmp_path / "claims.json"
    claims_path.write_text(packet.model_dump_json())
    review_path = tmp_path / "review.json"
    review_path.write_text(_review(packet).model_dump_json())
    monkeypatch.setattr(
        "sys.argv",
        [
            "benchmark-review",
            "--claims",
            str(claims_path),
            "--review",
            str(review_path),
        ],
    )

    assert main() == 2


def test_review_cli_accepts_matching_independent_assessment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _run()
    artifact = export_stored_assessment(run)
    packet = _packet(run)
    claims_path = tmp_path / "claims.json"
    claims_path.write_text(packet.model_dump_json())
    review_path = tmp_path / "review.json"
    review_path.write_text(_review(packet).model_dump_json())
    assessment_path = tmp_path / "assessment.json"
    assessment_path.write_text(json.dumps(artifact.model_dump(mode="json")))
    monkeypatch.setattr(
        "sys.argv",
        [
            "benchmark-review",
            "--claims",
            str(claims_path),
            "--review",
            str(review_path),
            "--assessment",
            str(assessment_path),
        ],
    )

    assert main() == 0
