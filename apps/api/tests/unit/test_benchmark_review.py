from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from release_intelligence.application.explanations import ExplanationValidator
from release_intelligence.benchmark.review import (
    AtomicClaim,
    ClaimReview,
    ClaimsDocument,
    ReviewDocument,
    evaluate_review,
    export_claim_packet,
    main,
    stable_claim_id,
)
from release_intelligence.ports.ai import (
    AIExplanation,
    ExplanationAction,
    ExplanationEvidence,
    ExplanationFinding,
    ExplanationGroup,
    ExplanationInput,
)


def _claims() -> ClaimsDocument:
    source = ExplanationInput(
        deterministic_status="NOT_READY",
        release_name="Release 2026.08.17",
        source_fetched_at="2026-08-16T12:00:00+00:00",
        findings=(
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
        ),
        limitations=("Deterministic readiness remains authoritative.",),
    )
    raw = AIExplanation(
        summary="model prose is replaced",
        groups=(
            ExplanationGroup(
                title="blocking check",
                explanation="blocking check",
                severity="BLOCKING",
                finding_ids=("finding-1",),
                evidence_ids=("github-check-101",),
            ),
        ),
        actions=(
            ExplanationAction(
                action="Fix the blocking check",
                finding_ids=("finding-1",),
                evidence_ids=("github-check-101",),
            ),
        ),
        limitations=source.limitations,
        confidence="LOW",
        finding_ids=("finding-1",),
        evidence_ids=("github-check-101",),
    )
    return export_claim_packet(
        scenario_id="critical-check",
        source=source,
        explanation=ExplanationValidator(source).validate(raw),
    )


def _claim_id(position: int) -> str:
    return _claims().claims[position - 1].claim_id


def _decision(claim_id: str, verdict: str = "supported") -> dict[str, object]:
    return {
        "claim_id": claim_id,
        "verdict": verdict,
        "reviewer": "release-reviewer",
        "rationale": "The cited deterministic fact supports the atomic claim.",
        "reviewed_at": datetime(2026, 8, 16, 12, tzinfo=UTC).isoformat(),
    }


def test_claim_identity_is_content_addressed_from_text_and_cited_facts() -> None:
    fact = _claims().claims[0].cited_facts[0]

    with pytest.raises(ValidationError):
        AtomicClaim(
            claim_id="claim-arbitrary",
            kind="summary",
            text="Blocking check failed.",
            cited_facts=(fact,),
        )
    identifier = stable_claim_id("summary", "Blocking check failed.", (fact,))
    claim = AtomicClaim(
        claim_id=identifier,
        kind="summary",
        text="Blocking check failed.",
        cited_facts=(fact,),
    )

    assert claim.claim_id == identifier
    assert stable_claim_id("summary", "A different claim.", (fact,)) != identifier


def test_atomic_claim_requires_nonempty_cited_facts_and_stable_unique_id() -> None:
    fact = _claims().claims[0].cited_facts[0]
    with pytest.raises(ValidationError):
        AtomicClaim.model_validate(
            {
                "claim_id": "claim-1",
                "kind": "summary",
                "text": "Unsupported by construction.",
                "cited_facts": [],
            }
        )
    identifier = stable_claim_id("summary", "Duplicate fact.", (fact, fact))
    with pytest.raises(ValidationError):
        AtomicClaim(
            claim_id=identifier,
            kind="summary",
            text="Duplicate fact.",
            cited_facts=(fact, fact),
        )


@pytest.mark.parametrize(
    "updates",
    [
        {"reviewer": ""},
        {"rationale": ""},
        {"reviewed_at": "2026-08-16T12:00:00"},
    ],
)
def test_review_decision_requires_identity_rationale_and_aware_timestamp(
    updates: dict[str, object],
) -> None:
    payload = _decision(_claim_id(1))
    payload.update(updates)

    with pytest.raises(ValidationError):
        ClaimReview.model_validate(payload)


def test_missing_review_is_failed_not_zero_unsupported_rate() -> None:
    claims = _claims()
    review = ReviewDocument.model_validate(
        {
            "version": "1.0.0",
            "packet_hash": claims.packet_hash,
            "decisions": [_decision(_claim_id(1))],
        }
    )

    result = evaluate_review(claims, review)

    assert result.complete is False
    assert result.accepted is False
    assert result.unsupported_claim_rate is None
    assert result.missing_claim_ids == tuple(
        sorted({claim.claim_id for claim in claims.claims} - {_claim_id(1)})
    )


def test_any_unsupported_claim_fails_and_rate_uses_all_reviewed_claims() -> None:
    claims = _claims()
    decisions = [
        _decision(claim.claim_id, "unsupported" if index == 0 else "supported")
        for index, claim in enumerate(claims.claims)
    ]
    review = ReviewDocument.model_validate(
        {
            "version": "1.0.0",
            "packet_hash": claims.packet_hash,
            "decisions": list(reversed(decisions)),
        }
    )

    result = evaluate_review(claims, review)

    assert result.complete is True
    assert result.accepted is False
    assert result.unsupported_claim_rate == 1 / len(claims.claims)
    assert [decision.claim_id for decision in result.decisions] == sorted(
        claim.claim_id for claim in claims.claims
    )


def test_duplicate_conflicting_or_unknown_decisions_are_rejected() -> None:
    claims = _claims()
    with pytest.raises(ValidationError):
        ReviewDocument.model_validate(
            {
                "version": "1.0.0",
                "packet_hash": claims.packet_hash,
                "decisions": [_decision(_claim_id(1)), _decision(_claim_id(1))],
            }
        )
    unknown = ReviewDocument.model_validate(
        {
            "version": "1.0.0",
            "packet_hash": claims.packet_hash,
            "decisions": [_decision("not-a-claim")],
        }
    )
    with pytest.raises(ValueError, match="unknown claim"):
        evaluate_review(claims, unknown)


def test_claim_and_review_versions_must_match() -> None:
    claims = _claims()
    review = ReviewDocument.model_validate(
        {
            "version": "2.0.0",
            "packet_hash": claims.packet_hash,
            "decisions": [_decision(claim.claim_id) for claim in claims.claims],
        }
    )

    with pytest.raises(ValueError, match="versions do not match"):
        evaluate_review(claims, review)


def test_review_cli_returns_nonzero_for_incomplete_and_unsupported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    claims = _claims()
    claims_path = tmp_path / "claims.json"
    claims_path.write_text(claims.model_dump_json())
    review_path = tmp_path / "review.yaml"
    review_path.write_text(
        "version: 1.0.0\n"
        f"packet_hash: '{claims.packet_hash}'\n"
        "decisions:\n"
        f"  - claim_id: {_claim_id(1)}\n"
        "    verdict: unsupported\n"
        "    reviewer: release-reviewer\n"
        "    rationale: Deterministic facts do not support this.\n"
        "    reviewed_at: '2026-08-16T12:00:00+00:00'\n"
    )
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

    assert main() != 0


def test_review_cli_rejects_oversized_claim_export_without_echoing_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    claims_path = tmp_path / "claims.json"
    claims_path.write_text("secret" * 400_000)
    review_path = tmp_path / "review.yaml"
    review_path.write_text("version: 1.0.0\ndecisions: []\n")
    monkeypatch.setattr(
        "release_intelligence.benchmark.review.json.loads",
        lambda _: (_ for _ in ()).throw(AssertionError("JSON parser was called")),
    )
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
    captured = capsys.readouterr()
    assert "secret" not in captured.err


def test_review_cli_rejects_arbitrary_prebuilt_claims(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    claims_path = tmp_path / "claims.json"
    claims_path.write_text(
        json.dumps(
            {
                "version": "1.0.0",
                "claims": [
                    {
                        "claim_id": "claim-arbitrary",
                        "kind": "summary",
                        "text": "Trust this claim without source artifacts.",
                        "cited_facts": [],
                    }
                ],
            }
        )
    )
    review_path = tmp_path / "review.yaml"
    review_path.write_text("version: 1.0.0\ndecisions: []\n")
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
    assert capsys.readouterr().err == "benchmark review input is invalid\n"


def test_complete_supported_review_cli_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    claims = _claims()
    claims_path = tmp_path / "claims.json"
    claims_path.write_text(claims.model_dump_json())
    review_path = tmp_path / "review.json"
    review_path.write_text(
        json.dumps(
            {
                "version": "1.0.0",
                "packet_hash": claims.packet_hash,
                "decisions": [
                    _decision(claim.claim_id) for claim in reversed(claims.claims)
                ],
            }
        )
    )
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

    assert main() == 0
    first = capsys.readouterr().out
    assert main() == 0
    second = capsys.readouterr().out
    assert first == second


def test_review_json_schema_rejects_whitespace_only_human_fields() -> None:
    schema = json.loads(
        (Path(__file__).parents[4] / "benchmarks/reviews/schema.json").read_text()
    )
    properties = schema["properties"]["decisions"]["items"]["properties"]

    for field in ("reviewer", "rationale"):
        pattern = properties[field]["pattern"]
        assert re.fullmatch(pattern, "   ") is None
        assert re.fullmatch(pattern, "reviewed") is not None
