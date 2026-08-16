from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Self

import yaml
from pydantic import Field, field_validator, model_validator

from release_intelligence.benchmark.schema import StrictModel, read_bounded_text

_ID = re.compile(r"^[a-z][a-z0-9_-]{0,99}$")
_RULE_ID = re.compile(r"^[a-z][a-z0-9_.:-]{0,238}$")
_VERSION = re.compile(r"^[1-9][0-9]{0,2}\.[0-9]{1,3}\.[0-9]{1,3}$")


def _valid_version(value: str) -> str:
    if _VERSION.fullmatch(value) is None:
        raise ValueError("version must be bounded semantic version")
    return value


class CitedFact(StrictModel):
    scenario_id: str
    rule_id: str
    source_id: str = Field(min_length=1, max_length=255)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=10)

    @field_validator("scenario_id")
    @classmethod
    def valid_scenario_id(cls, value: str) -> str:
        if _ID.fullmatch(value) is None:
            raise ValueError("scenario ID must be canonical")
        return value

    @field_validator("rule_id")
    @classmethod
    def valid_rule_id(cls, value: str) -> str:
        if _RULE_ID.fullmatch(value) is None:
            raise ValueError("rule ID must be canonical")
        return value

    @field_validator("evidence_ids")
    @classmethod
    def valid_evidence_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)) or any(
            not item.strip() or len(item) > 255 for item in value
        ):
            raise ValueError("evidence IDs must be bounded, nonblank, and unique")
        return value


def _fact_key(fact: CitedFact) -> tuple[str, str, str, tuple[str, ...]]:
    return (
        fact.scenario_id,
        fact.rule_id,
        fact.source_id,
        tuple(sorted(fact.evidence_ids)),
    )


def stable_claim_id(text: str, cited_facts: tuple[CitedFact, ...]) -> str:
    canonical = {
        "text": text.strip(),
        "cited_facts": [
            {
                "scenario_id": fact.scenario_id,
                "rule_id": fact.rule_id,
                "source_id": fact.source_id,
                "evidence_ids": sorted(fact.evidence_ids),
            }
            for fact in sorted(cited_facts, key=_fact_key)
        ],
    }
    encoded = json.dumps(
        canonical, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "claim-" + hashlib.sha256(encoded).hexdigest()


class AtomicClaim(StrictModel):
    claim_id: str
    text: str = Field(min_length=1, max_length=1000)
    cited_facts: tuple[CitedFact, ...] = Field(min_length=1, max_length=20)

    @field_validator("claim_id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        if _ID.fullmatch(value) is None:
            raise ValueError("claim ID must be canonical")
        return value

    @field_validator("text")
    @classmethod
    def atomic_text(cls, value: str) -> str:
        if not value.strip() or "\n" in value or "\r" in value:
            raise ValueError("claim text must be a single atomic statement")
        return value.strip()

    @model_validator(mode="after")
    def stable_identity_and_unique_facts(self) -> Self:
        keys = [_fact_key(fact) for fact in self.cited_facts]
        if len(keys) != len(set(keys)):
            raise ValueError("cited facts must be unique")
        if self.claim_id != stable_claim_id(self.text, self.cited_facts):
            raise ValueError("claim ID must be derived from its atomic content")
        return self


class ClaimsDocument(StrictModel):
    version: str
    claims: tuple[AtomicClaim, ...] = Field(min_length=1, max_length=1000)

    @field_validator("version")
    @classmethod
    def valid_version(cls, value: str) -> str:
        return _valid_version(value)

    @model_validator(mode="after")
    def unique_claims(self) -> Self:
        ids = [claim.claim_id for claim in self.claims]
        if len(ids) != len(set(ids)):
            raise ValueError("claim IDs must be unique")
        return self


class ClaimReview(StrictModel):
    claim_id: str
    verdict: str
    reviewer: str = Field(min_length=1, max_length=255)
    rationale: str = Field(min_length=1, max_length=2000)
    reviewed_at: datetime

    @field_validator("claim_id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        if _ID.fullmatch(value) is None:
            raise ValueError("claim ID must be canonical")
        return value

    @field_validator("verdict")
    @classmethod
    def valid_verdict(cls, value: str) -> str:
        if value not in {"supported", "unsupported"}:
            raise ValueError("verdict must be supported or unsupported")
        return value

    @field_validator("reviewer", "rationale")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("review field must not be blank")
        return value.strip()

    @field_validator("reviewed_at")
    @classmethod
    def aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("review timestamp must be timezone-aware")
        return value


class ReviewDocument(StrictModel):
    version: str
    decisions: tuple[ClaimReview, ...] = Field(max_length=1000)

    @field_validator("version")
    @classmethod
    def valid_version(cls, value: str) -> str:
        return _valid_version(value)

    @model_validator(mode="after")
    def unique_decisions(self) -> Self:
        ids = [decision.claim_id for decision in self.decisions]
        if len(ids) != len(set(ids)):
            raise ValueError("review decisions must be unique")
        return self


class ReviewResult(StrictModel):
    complete: bool
    accepted: bool
    unsupported_claim_rate: float | None
    missing_claim_ids: tuple[str, ...]
    decisions: tuple[ClaimReview, ...]


def evaluate_review(claims: ClaimsDocument, review: ReviewDocument) -> ReviewResult:
    if claims.version != review.version:
        raise ValueError("claim and review versions do not match")
    claim_ids = {claim.claim_id for claim in claims.claims}
    decision_ids = {decision.claim_id for decision in review.decisions}
    unknown = sorted(decision_ids - claim_ids)
    if unknown:
        raise ValueError(f"unknown claim decision: {unknown[0]}")
    missing = tuple(sorted(claim_ids - decision_ids))
    decisions = tuple(sorted(review.decisions, key=lambda item: item.claim_id))
    if missing:
        return ReviewResult(
            complete=False,
            accepted=False,
            unsupported_claim_rate=None,
            missing_claim_ids=missing,
            decisions=decisions,
        )
    unsupported = sum(item.verdict == "unsupported" for item in decisions)
    rate = unsupported / len(decisions)
    return ReviewResult(
        complete=True,
        accepted=unsupported == 0,
        unsupported_claim_rate=rate,
        missing_claim_ids=(),
        decisions=decisions,
    )


def _load(path: Path) -> object:
    text = read_bounded_text(path)
    if path.suffix.casefold() == ".json":
        return json.loads(text)
    return yaml.safe_load(text)


def main() -> int:
    parser = argparse.ArgumentParser(description="Review atomic benchmark AI claims")
    parser.add_argument("--claims", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    args = parser.parse_args()
    try:
        claims = ClaimsDocument.model_validate(_load(args.claims))
        review = ReviewDocument.model_validate(_load(args.review))
        result = evaluate_review(claims, review)
    except (
        OSError,
        UnicodeError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
        yaml.YAMLError,
    ):
        print("benchmark review input is invalid", file=sys.stderr)
        return 2
    print(result.model_dump_json(indent=2))
    return 0 if result.accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
