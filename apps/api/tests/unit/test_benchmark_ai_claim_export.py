from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from release_intelligence.application.explanations import ExplanationValidator
from release_intelligence.benchmark.review import (
    ClaimsDocument,
    ReviewDocument,
    evaluate_review,
    export_claim_packet,
)
from release_intelligence.ports.ai import (
    AIExplanation,
    ExplanationAction,
    ExplanationEvidence,
    ExplanationFinding,
    ExplanationGroup,
    ExplanationInput,
)


def _source(*, reverse: bool = False) -> ExplanationInput:
    findings = (
        ExplanationFinding(
            finding_id="finding-1",
            rule_id="checks.blocking_not_successful",
            severity="BLOCKING",
            summary="Blocking check failed",
            required_action="Fix the blocking check",
            evidence=(
                ExplanationEvidence(
                    evidence_id="github-check-101",
                    source_type="github_check_run",
                    source_id="101",
                ),
            ),
        ),
        ExplanationFinding(
            finding_id="finding-2",
            rule_id="scope.code_change_requires_pr",
            severity="BLOCKING",
            summary="Issue has no pull request",
            required_action="Link a merged pull request",
            evidence=(
                ExplanationEvidence(
                    evidence_id="github-issue-110",
                    source_type="github_issue",
                    source_id="110",
                ),
            ),
        ),
    )
    return ExplanationInput(
        deterministic_status="NOT_READY",
        release_name="Release 2026.08.17",
        source_fetched_at="2026-08-16T12:00:00+00:00",
        findings=tuple(reversed(findings)) if reverse else findings,
        limitations=("Deterministic readiness remains authoritative.",),
    )


def _explanation(source: ExplanationInput, *, reverse: bool = False) -> AIExplanation:
    groups = (
        ExplanationGroup(
            title="first",
            explanation="first",
            severity="BLOCKING",
            finding_ids=("finding-1",),
            evidence_ids=("github-check-101",),
        ),
        ExplanationGroup(
            title="second",
            explanation="second",
            severity="BLOCKING",
            finding_ids=("finding-2",),
            evidence_ids=("github-issue-110",),
        ),
    )
    actions = (
        ExplanationAction(
            action="Fix the blocking check",
            finding_ids=("finding-1",),
            evidence_ids=("github-check-101",),
        ),
        ExplanationAction(
            action="Link a merged pull request",
            finding_ids=("finding-2",),
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
    source = _source(reverse=reverse)
    return export_claim_packet(
        scenario_id="release-2026-08-17",
        source=source,
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
    assert len(packet.claims) == 9
    assert all(claim.cited_facts for claim in packet.claims)
    assert {
        (fact.finding_id, fact.rule_id, fact.evidence_ids)
        for claim in packet.claims
        for fact in claim.cited_facts
    } == {
        ("finding-1", "checks.blocking_not_successful", ("github-check-101",)),
        ("finding-2", "scope.code_change_requires_pr", ("github-issue-110",)),
    }


def test_citation_preserves_exact_evidence_identity_mapping() -> None:
    packet = _packet()
    fact = next(
        fact
        for claim in packet.claims
        for fact in claim.cited_facts
        if fact.finding_id == "finding-1"
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
    review = ReviewDocument.model_validate(
        {
            "version": packet.version,
            "packet_hash": packet.packet_hash,
            "decisions": _decisions(packet),
        }
    )
    changed = export_claim_packet(
        scenario_id="another-release",
        source=_source(),
        explanation=_explanation(_source()),
    )

    assert evaluate_review(packet, review).accepted is True
    with pytest.raises(ValueError, match="packet hash"):
        evaluate_review(changed, review)
