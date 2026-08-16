from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from release_intelligence.benchmark.review import (
    AtomicClaim,
    CitedFact,
    ClaimReview,
    ClaimsDocument,
    ReviewDocument,
    evaluate_review,
    main,
    stable_claim_id,
)


def _claims() -> ClaimsDocument:
    first_fact = CitedFact(
        scenario_id="critical-check",
        rule_id="checks.blocking_not_successful",
        source_id="101",
        evidence_ids=("github-check-101",),
    )
    second_fact = CitedFact(
        scenario_id="critical-check",
        rule_id="release.status",
        source_id="critical-check",
        evidence_ids=("github-check-101",),
    )
    return ClaimsDocument(
        version="1.0.0",
        claims=(
            AtomicClaim(
                claim_id=stable_claim_id("Blocking check failed.", (first_fact,)),
                text="Blocking check failed.",
                cited_facts=(first_fact,),
            ),
            AtomicClaim(
                claim_id=stable_claim_id("Release is not ready.", (second_fact,)),
                text="Release is not ready.",
                cited_facts=(second_fact,),
            ),
        ),
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
    fact = CitedFact(
        scenario_id="critical-check",
        rule_id="checks.blocking_not_successful",
        source_id="101",
        evidence_ids=("github-check-101",),
    )

    with pytest.raises(ValidationError):
        AtomicClaim(
            claim_id="claim-arbitrary",
            text="Blocking check failed.",
            cited_facts=(fact,),
        )
    identifier = stable_claim_id("Blocking check failed.", (fact,))
    claim = AtomicClaim(
        claim_id=identifier,
        text="Blocking check failed.",
        cited_facts=(fact,),
    )

    assert claim.claim_id == identifier
    assert stable_claim_id("A different claim.", (fact,)) != identifier


def test_atomic_claim_requires_nonempty_cited_facts_and_stable_unique_id() -> None:
    with pytest.raises(ValidationError):
        ClaimsDocument.model_validate(
            {
                "version": "1.0.0",
                "claims": [
                    {"claim_id": "claim-1", "text": "Unsupported by construction."},
                    {"claim_id": "claim-1", "text": "Duplicate."},
                ],
            }
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
        {"version": "1.0.0", "decisions": [_decision(_claim_id(1))]}
    )

    result = evaluate_review(claims, review)

    assert result.complete is False
    assert result.accepted is False
    assert result.unsupported_claim_rate is None
    assert result.missing_claim_ids == (_claim_id(2),)


def test_any_unsupported_claim_fails_and_rate_uses_all_reviewed_claims() -> None:
    claims = _claims()
    review = ReviewDocument.model_validate(
        {
            "version": "1.0.0",
            "decisions": [
                _decision(_claim_id(2), "unsupported"),
                _decision(_claim_id(1)),
            ],
        }
    )

    result = evaluate_review(claims, review)

    assert result.complete is True
    assert result.accepted is False
    assert result.unsupported_claim_rate == 0.5
    assert [decision.claim_id for decision in result.decisions] == sorted(
        (_claim_id(1), _claim_id(2))
    )


def test_duplicate_conflicting_or_unknown_decisions_are_rejected() -> None:
    claims = _claims()
    with pytest.raises(ValidationError):
        ReviewDocument.model_validate(
            {
                "version": "1.0.0",
                "decisions": [_decision(_claim_id(1)), _decision(_claim_id(1))],
            }
        )
    unknown = ReviewDocument.model_validate(
        {"version": "1.0.0", "decisions": [_decision("not-a-claim")]}
    )
    with pytest.raises(ValueError, match="unknown claim"):
        evaluate_review(claims, unknown)


def test_claim_and_review_versions_must_match() -> None:
    claims = _claims()
    review = ReviewDocument.model_validate(
        {
            "version": "2.0.0",
            "decisions": [_decision(_claim_id(1)), _decision(_claim_id(2))],
        }
    )

    with pytest.raises(ValueError, match="versions do not match"):
        evaluate_review(claims, review)


def test_review_cli_returns_nonzero_for_incomplete_and_unsupported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    claims_path = tmp_path / "claims.json"
    claims_path.write_text(_claims().model_dump_json())
    review_path = tmp_path / "review.yaml"
    review_path.write_text(
        "version: 1.0.0\n"
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


def test_complete_supported_review_cli_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    claims_path = tmp_path / "claims.json"
    claims_path.write_text(_claims().model_dump_json())
    review_path = tmp_path / "review.json"
    review_path.write_text(
        json.dumps(
            {
                "version": "1.0.0",
                "decisions": [_decision(_claim_id(2)), _decision(_claim_id(1))],
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
