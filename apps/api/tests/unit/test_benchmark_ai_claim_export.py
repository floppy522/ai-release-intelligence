from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
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
    evaluate_review,
    export_claim_packet,
    export_stored_assessment,
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
    ExplanationInput,
)
from release_intelligence.ports.repositories import (
    StoredAnalysisRun,
    StoredFindingMetadata,
)

NOW = datetime(2026, 8, 16, 12, tzinfo=UTC)
FIRST_ID = UUID("10000000-0000-0000-0000-000000000001")
SECOND_ID = UUID("10000000-0000-0000-0000-000000000002")
RUN_ID = UUID("20000000-0000-0000-0000-000000000001")


def _run(*, reverse: bool = False) -> StoredAnalysisRun:
    findings = (
        ReadinessFinding(
            rule_id="checks.blocking_not_successful",
            severity="BLOCKING",
            summary="Blocking check failed",
            required_action="Fix the blocking check",
            evidence=(
                EvidenceRef(
                    evidence_id="github-check-101",
                    source_type="github_check_run",
                    source_id="101",
                    url="https://github.com/acme/widgets/runs/101",
                    fingerprint="sha256:" + "1" * 64,
                ),
            ),
        ),
        ReadinessFinding(
            rule_id="scope.code_change_requires_pr",
            severity="BLOCKING",
            summary="Issue has no pull request",
            required_action="Link a merged pull request",
            evidence=(
                EvidenceRef(
                    evidence_id="github-issue-110",
                    source_type="github_issue",
                    source_id="110",
                    url="https://github.com/acme/widgets/issues/11",
                    fingerprint="sha256:" + "2" * 64,
                ),
            ),
        ),
    )
    selected = tuple(reversed(findings)) if reverse else findings
    ids = (SECOND_ID, FIRST_ID) if reverse else (FIRST_ID, SECOND_ID)
    snapshot = ReleaseSnapshot(
        release_name="Release 2026.08.17",
        issue_number="",
        milestone_number=7,
        issue_labels=(),
        linked_pr_numbers=(),
        issue_evidence=findings[0].evidence[0],
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
        findings=selected,
        assessment=ReadinessAssessment(
            status=ReleaseStatus.NOT_READY, findings=selected
        ),
        policy_version="configuration:1",
        source_fetched_at=NOW,
        finding_metadata=tuple(
            StoredFindingMetadata(finding_id=identifier, finding=finding)
            for identifier, finding in zip(ids, selected, strict=True)
        ),
    )


def _explanation(source: ExplanationInput, *, reverse: bool = False) -> AIExplanation:
    groups = (
        ExplanationGroup(
            title="first",
            explanation="first",
            severity="BLOCKING",
            finding_ids=(str(FIRST_ID),),
            evidence_ids=("github-check-101",),
        ),
        ExplanationGroup(
            title="second",
            explanation="second",
            severity="BLOCKING",
            finding_ids=(str(SECOND_ID),),
            evidence_ids=("github-issue-110",),
        ),
    )
    actions = (
        ExplanationAction(
            action="Fix the blocking check",
            finding_ids=(str(FIRST_ID),),
            evidence_ids=("github-check-101",),
        ),
        ExplanationAction(
            action="Link a merged pull request",
            finding_ids=(str(SECOND_ID),),
            evidence_ids=("github-issue-110",),
        ),
    )
    raw = AIExplanation(
        summary="model prose is replaced",
        groups=tuple(reversed(groups)) if reverse else groups,
        actions=tuple(reversed(actions)) if reverse else actions,
        limitations=source.limitations,
        confidence="LOW",
        finding_ids=tuple(item.finding_id for item in source.findings),
        evidence_ids=tuple(
            evidence.evidence_id
            for finding in source.findings
            for evidence in finding.evidence
        ),
    )
    return ExplanationValidator(source).validate(raw)


def _packet(*, reverse: bool = False) -> ClaimsDocument:
    run = _run(reverse=reverse)
    source = build_explanation_input(run)
    return export_claim_packet(
        run=run,
        explanation=_explanation(source, reverse=reverse),
    )


def _decisions(packet: ClaimsDocument) -> list[dict[str, object]]:
    return [
        {
            "claim_id": claim.claim_id,
            "verdict": "supported",
            "reviewer": "release-reviewer",
            "rationale": "The immutable cited facts support this exact claim.",
            "reviewed_at": datetime(2026, 8, 16, 12, tzinfo=UTC).isoformat(),
        }
        for claim in packet.claims
    ]


def test_real_validated_ai_explanation_exports_every_prose_field() -> None:
    packet = _packet()

    assert {claim.kind for claim in packet.claims} == {
        "action",
        "confidence",
        "group_explanation",
        "group_title",
        "limitation",
        "summary",
    }
    assert len(packet.claims) == 10
    assert all(claim.cited_facts for claim in packet.claims)
    assert {
        (fact.finding_id, fact.rule_id, fact.evidence_ids)
        for claim in packet.claims
        for fact in claim.cited_facts
    } == {
        (str(FIRST_ID), "checks.blocking_not_successful", ("github-check-101",)),
        (str(SECOND_ID), "scope.code_change_requires_pr", ("github-issue-110",)),
    }


def test_citation_preserves_exact_evidence_identity_mapping() -> None:
    packet = _packet()
    fact = next(
        fact
        for claim in packet.claims
        for fact in claim.cited_facts
        if fact.finding_id == str(FIRST_ID)
    )

    assert fact.model_dump()["evidence"] == (
        {
            "evidence_id": "github-check-101",
            "source_type": "github_check_run",
            "source_id": "101",
        },
    )


def test_export_normalizes_source_group_action_and_fact_order() -> None:
    assert _packet().model_dump(mode="json") == _packet(reverse=True).model_dump(
        mode="json"
    )


def test_packet_parser_normalizes_claim_order() -> None:
    packet = _packet()
    payload = packet.model_dump(mode="json")
    payload["claims"].reverse()

    assert ClaimsDocument.model_validate(payload) == packet


@pytest.mark.parametrize("mutation", ["omit", "invent", "change"])
def test_packet_rejects_omitted_invented_or_changed_claims(mutation: str) -> None:
    payload = _packet().model_dump(mode="json")
    claims = payload["claims"]
    assert isinstance(claims, list)
    if mutation == "omit":
        claims.pop()
    elif mutation == "invent":
        invented = deepcopy(claims[0])
        invented["cited_facts"][0]["scenario_id"] = "invented-scenario"
        invented["cited_facts"][0]["rule_id"] = "invented.rule"
        invented["cited_facts"][0]["evidence"][0]["evidence_id"] = "invented-evidence"
        claims.append(invented)
    else:
        claims[0]["text"] = "Changed after export"

    with pytest.raises(ValidationError):
        ClaimsDocument.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "invented"),
    [
        ("rule_id", "invented.rule"),
        ("summary", "Invented deterministic summary"),
        ("required_action", "Invented deterministic action"),
    ],
)
def test_packet_rejects_changed_source_facts_with_stale_claims(
    field: str, invented: str
) -> None:
    payload = _packet().model_dump(mode="json")
    payload["source"]["findings"][0][field] = invented

    with pytest.raises(ValidationError):
        ClaimsDocument.model_validate(payload)


def test_packet_rejects_changed_scenario_identity_with_stale_claims() -> None:
    payload = _packet().model_dump(mode="json")
    payload["scenario_id"] = "invented-scenario"

    with pytest.raises(ValidationError):
        ClaimsDocument.model_validate(payload)


def test_review_decisions_are_bound_to_exact_packet_hash() -> None:
    packet = _packet()
    artifact = export_stored_assessment(_run())
    review = ReviewDocument.model_validate(
        {
            "version": packet.version,
            "packet_hash": packet.packet_hash,
            "decisions": _decisions(packet),
        }
    )
    changed_run = replace(_run(), id=UUID("20000000-0000-0000-0000-000000000002"))
    changed_source = build_explanation_input(changed_run)
    changed = export_claim_packet(
        run=changed_run,
        explanation=_explanation(changed_source),
    )

    assert evaluate_review(packet, review, artifact).accepted is True
    with pytest.raises(ValueError, match="packet hash"):
        evaluate_review(changed, review, export_stored_assessment(changed_run))
