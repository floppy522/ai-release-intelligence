from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Literal, Self
from uuid import UUID

import yaml
from pydantic import Field, TypeAdapter, field_validator, model_validator

from release_intelligence.application.explanations import (
    AIExplanationRejected,
    ExplanationValidator,
    build_explanation_input,
)
from release_intelligence.benchmark.schema import StrictModel, read_bounded_text
from release_intelligence.domain.models import ReleaseSnapshot
from release_intelligence.ports.ai import (
    AIExplanation,
    ExplanationEvidence,
    ExplanationFinding,
    ExplanationInput,
    FindingSeverity,
)
from release_intelligence.ports.repositories import StoredAnalysisRun

_ID = re.compile(r"^[a-z][a-z0-9_-]{0,99}$")
_RULE_ID = re.compile(r"^[a-z][a-z0-9_.:-]{0,238}$")
_VERSION = re.compile(r"^[1-9][0-9]{0,2}\.[0-9]{1,3}\.[0-9]{1,3}$")
_PACKET_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
ClaimKind = Literal[
    "summary",
    "group_title",
    "group_explanation",
    "action",
    "limitation",
    "confidence",
]


def _valid_version(value: str) -> str:
    if _VERSION.fullmatch(value) is None:
        raise ValueError("version must be bounded semantic version")
    return value


class CitedEvidence(StrictModel):
    evidence_id: str = Field(min_length=1, max_length=255)
    source_type: str = Field(min_length=1, max_length=200)
    source_id: str = Field(min_length=1, max_length=200)

    @field_validator("evidence_id", "source_type", "source_id")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("cited evidence fields must not be blank")
        return value.strip()


def _evidence_key(evidence: CitedEvidence) -> tuple[str, str, str]:
    return evidence.evidence_id, evidence.source_type, evidence.source_id


class CitedFact(StrictModel):
    scenario_id: str
    finding_id: str = Field(min_length=1, max_length=255)
    rule_id: str
    severity: FindingSeverity
    summary: str = Field(min_length=1, max_length=200)
    required_action: str = Field(min_length=1, max_length=200)
    evidence: tuple[CitedEvidence, ...] = Field(min_length=1, max_length=100)

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

    @model_validator(mode="after")
    def exact_ordered_evidence(self) -> Self:
        keys = [_evidence_key(item) for item in self.evidence]
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise ValueError("cited evidence must be sorted and unique")
        return self

    @property
    def source_ids(self) -> tuple[str, ...]:
        return tuple(sorted({item.source_id for item in self.evidence}))

    @property
    def source_types(self) -> tuple[str, ...]:
        return tuple(sorted({item.source_type for item in self.evidence}))

    @property
    def evidence_ids(self) -> tuple[str, ...]:
        return tuple(item.evidence_id for item in self.evidence)


def _fact_key(fact: CitedFact) -> tuple[object, ...]:
    return (
        fact.scenario_id,
        fact.finding_id,
        fact.rule_id,
        fact.severity,
        fact.summary,
        fact.required_action,
        tuple(_evidence_key(item) for item in fact.evidence),
    )


class AtomicClaim(StrictModel):
    claim_id: str
    kind: ClaimKind
    text: str = Field(min_length=1, max_length=2_000)
    cited_facts: tuple[CitedFact, ...] = Field(min_length=1, max_length=1_000)

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
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise ValueError("cited facts must be sorted and unique")
        if self.claim_id != stable_claim_id(self.kind, self.text, self.cited_facts):
            raise ValueError("claim ID must be derived from its atomic content")
        return self


def stable_claim_id(
    kind: ClaimKind, text: str, cited_facts: tuple[CitedFact, ...]
) -> str:
    canonical = {
        "kind": kind,
        "text": text.strip(),
        "cited_facts": [fact.model_dump(mode="json") for fact in cited_facts],
    }
    return "claim-" + _digest(canonical).removeprefix("sha256:")


class StoredSnapshotIdentity(StrictModel):
    repository_id: str = Field(min_length=1, max_length=255)
    repository_full_name: str = Field(min_length=1, max_length=255)
    snapshot_version: str = Field(min_length=1, max_length=50)
    release_name: str = Field(min_length=1, max_length=200)
    milestone_number: int = Field(ge=1)
    candidate_ref: str = Field(min_length=1, max_length=255)
    candidate_sha: str = Field(pattern=r"^(?:[0-9a-f]{40})?$")
    source_fetched_at: datetime

    @field_validator(
        "repository_id",
        "repository_full_name",
        "snapshot_version",
        "release_name",
        "candidate_ref",
    )
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("snapshot identity fields must not be blank")
        return value.strip()

    @field_validator("source_fetched_at")
    @classmethod
    def aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("source timestamp must be timezone-aware")
        return value


class AssessmentReference(StrictModel):
    artifact_version: str
    artifact_kind: Literal["stored_analysis_run"]
    analysis_run_id: str
    repository_id: str
    repository_full_name: str
    snapshot_fingerprint: str
    assessment_digest: str

    @field_validator("artifact_version")
    @classmethod
    def valid_version(cls, value: str) -> str:
        return _valid_version(value)

    @field_validator("analysis_run_id")
    @classmethod
    def valid_run_id(cls, value: str) -> str:
        try:
            parsed = UUID(value)
        except ValueError as error:
            raise ValueError("analysis run ID must be a UUID") from error
        if str(parsed) != value:
            raise ValueError("analysis run ID must be canonical")
        return value

    @field_validator("repository_id", "repository_full_name")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip() or len(value) > 255:
            raise ValueError("repository identity must be bounded and nonblank")
        return value.strip()

    @field_validator("snapshot_fingerprint", "assessment_digest")
    @classmethod
    def canonical_digest(cls, value: str) -> str:
        if _PACKET_HASH.fullmatch(value) is None:
            raise ValueError("trusted artifact digest must be canonical SHA-256")
        return value


class StoredAssessmentArtifact(StrictModel):
    version: str
    artifact_kind: Literal["stored_analysis_run"] = "stored_analysis_run"
    analysis_run_id: str
    policy_version: str = Field(min_length=1, max_length=255)
    snapshot: StoredSnapshotIdentity
    source: ExplanationInput
    snapshot_fingerprint: str
    assessment_digest: str

    @field_validator("version")
    @classmethod
    def valid_version(cls, value: str) -> str:
        return _valid_version(value)

    @field_validator("analysis_run_id")
    @classmethod
    def valid_run_id(cls, value: str) -> str:
        return AssessmentReference.valid_run_id(value)

    @field_validator("policy_version")
    @classmethod
    def nonblank_policy(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("policy version must not be blank")
        return value.strip()

    @field_validator("snapshot_fingerprint", "assessment_digest")
    @classmethod
    def canonical_digest(cls, value: str) -> str:
        return AssessmentReference.canonical_digest(value)

    @model_validator(mode="after")
    def canonical_trusted_artifact(self) -> Self:
        canonical_source = _canonical_source(self.source)
        expected_assessment = _assessment_digest(
            self.version,
            self.artifact_kind,
            self.analysis_run_id,
            self.policy_version,
            self.snapshot,
            self.snapshot_fingerprint,
            canonical_source,
        )
        if self.assessment_digest != expected_assessment:
            raise ValueError("assessment digest does not match trusted artifact")
        try:
            source_time = datetime.fromisoformat(canonical_source.source_fetched_at)
        except ValueError as error:
            raise ValueError("source timestamp is not canonical") from error
        if (
            canonical_source.release_name != self.snapshot.release_name
            or source_time != self.snapshot.source_fetched_at
        ):
            raise ValueError("source facts do not match snapshot identity")
        object.__setattr__(self, "source", canonical_source)
        return self

    @property
    def reference(self) -> AssessmentReference:
        return AssessmentReference(
            artifact_version=self.version,
            artifact_kind=self.artifact_kind,
            analysis_run_id=self.analysis_run_id,
            repository_id=self.snapshot.repository_id,
            repository_full_name=self.snapshot.repository_full_name,
            snapshot_fingerprint=self.snapshot_fingerprint,
            assessment_digest=self.assessment_digest,
        )


class ClaimsDocument(StrictModel):
    version: str
    scenario_id: str
    assessment: AssessmentReference
    source: ExplanationInput
    explanation: AIExplanation
    claims: tuple[AtomicClaim, ...] = Field(min_length=1, max_length=10_000)
    packet_hash: str

    @field_validator("version")
    @classmethod
    def valid_version(cls, value: str) -> str:
        return _valid_version(value)

    @field_validator("scenario_id")
    @classmethod
    def valid_scenario_id(cls, value: str) -> str:
        if _ID.fullmatch(value) is None:
            raise ValueError("scenario ID must be canonical")
        return value

    @field_validator("packet_hash")
    @classmethod
    def valid_packet_hash(cls, value: str) -> str:
        if _PACKET_HASH.fullmatch(value) is None:
            raise ValueError("packet hash must be canonical SHA-256")
        return value

    @model_validator(mode="after")
    def validate_grounded_packet(self) -> Self:
        if self.scenario_id != _run_scenario_id(self.assessment.analysis_run_id):
            raise ValueError("scenario ID does not match analysis run identity")
        source = _canonical_source(self.source)
        explanation = _canonical_explanation(source, self.explanation)
        expected = _claims_for(self.scenario_id, source, explanation)
        supplied = tuple(sorted(self.claims, key=lambda claim: claim.claim_id))
        if supplied != expected:
            raise ValueError("claim export does not exactly match source artifacts")
        expected_hash = _packet_digest(
            self.version,
            self.scenario_id,
            self.assessment,
            source,
            explanation,
            expected,
        )
        if self.packet_hash != expected_hash:
            raise ValueError("packet hash does not match canonical source artifacts")
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "explanation", explanation)
        object.__setattr__(self, "claims", expected)
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
    packet_hash: str
    decisions: tuple[ClaimReview, ...] = Field(max_length=1_000)

    @field_validator("version")
    @classmethod
    def valid_version(cls, value: str) -> str:
        return _valid_version(value)

    @field_validator("packet_hash")
    @classmethod
    def valid_packet_hash(cls, value: str) -> str:
        if _PACKET_HASH.fullmatch(value) is None:
            raise ValueError("packet hash must be canonical SHA-256")
        return value

    @model_validator(mode="after")
    def unique_decisions(self) -> Self:
        ids = [decision.claim_id for decision in self.decisions]
        if len(ids) != len(set(ids)):
            raise ValueError("review decisions must be unique")
        return self


class ReviewResult(StrictModel):
    packet_hash: str
    complete: bool
    accepted: bool
    unsupported_claim_rate: float | None
    missing_claim_ids: tuple[str, ...]
    decisions: tuple[ClaimReview, ...]


def export_stored_assessment(
    run: StoredAnalysisRun,
    *,
    version: str = "1.0.0",
) -> StoredAssessmentArtifact:
    _valid_version(version)
    source = _canonical_source(build_explanation_input(run))
    snapshot = StoredSnapshotIdentity(
        repository_id=run.snapshot.repository_id,
        repository_full_name=run.snapshot.repository_full_name,
        snapshot_version=run.snapshot.snapshot_version.value,
        release_name=run.snapshot.release_name,
        milestone_number=run.snapshot.milestone_number,
        candidate_ref=run.snapshot.candidate_ref,
        candidate_sha=run.snapshot.candidate_sha,
        source_fetched_at=run.source_fetched_at,
    )
    snapshot_fingerprint = _snapshot_digest(run.snapshot)
    assessment_digest = _assessment_digest(
        version,
        "stored_analysis_run",
        str(run.id),
        run.policy_version,
        snapshot,
        snapshot_fingerprint,
        source,
    )
    return StoredAssessmentArtifact(
        version=version,
        analysis_run_id=str(run.id),
        policy_version=run.policy_version,
        snapshot=snapshot,
        source=source,
        snapshot_fingerprint=snapshot_fingerprint,
        assessment_digest=assessment_digest,
    )


def export_claim_packet(
    *,
    run: StoredAnalysisRun,
    explanation: AIExplanation,
    version: str = "1.0.0",
) -> ClaimsDocument:
    artifact = export_stored_assessment(run, version=version)
    scenario_id = _run_scenario_id(artifact.analysis_run_id)
    canonical_source = artifact.source
    canonical_explanation = ExplanationValidator(canonical_source).validate(explanation)
    claims = _claims_for(scenario_id, canonical_source, canonical_explanation)
    packet_hash = _packet_digest(
        version,
        scenario_id,
        artifact.reference,
        canonical_source,
        canonical_explanation,
        claims,
    )
    return ClaimsDocument(
        version=version,
        scenario_id=scenario_id,
        assessment=artifact.reference,
        source=canonical_source,
        explanation=canonical_explanation,
        claims=claims,
        packet_hash=packet_hash,
    )


def evaluate_review(
    claims: ClaimsDocument,
    review: ReviewDocument,
    assessment: StoredAssessmentArtifact,
) -> ReviewResult:
    _validate_trusted_assessment(claims, assessment)
    if claims.version != review.version:
        raise ValueError("claim and review versions do not match")
    if claims.packet_hash != review.packet_hash:
        raise ValueError("review packet hash does not match claim packet hash")
    claim_ids = {claim.claim_id for claim in claims.claims}
    decision_ids = {decision.claim_id for decision in review.decisions}
    unknown = sorted(decision_ids - claim_ids)
    if unknown:
        raise ValueError(f"unknown claim decision: {unknown[0]}")
    missing = tuple(sorted(claim_ids - decision_ids))
    decisions = tuple(sorted(review.decisions, key=lambda item: item.claim_id))
    if missing:
        return ReviewResult(
            packet_hash=claims.packet_hash,
            complete=False,
            accepted=False,
            unsupported_claim_rate=None,
            missing_claim_ids=missing,
            decisions=decisions,
        )
    unsupported = sum(item.verdict == "unsupported" for item in decisions)
    return ReviewResult(
        packet_hash=claims.packet_hash,
        complete=True,
        accepted=unsupported == 0,
        unsupported_claim_rate=unsupported / len(decisions),
        missing_claim_ids=(),
        decisions=decisions,
    )


def _validate_trusted_assessment(
    claims: ClaimsDocument, assessment: StoredAssessmentArtifact
) -> None:
    if claims.assessment != assessment.reference or claims.source != assessment.source:
        raise ValueError("claim packet does not match trusted assessment artifact")


def _run_scenario_id(analysis_run_id: str) -> str:
    return f"run-{analysis_run_id}"


def _canonical_source(source: ExplanationInput) -> ExplanationInput:
    findings = tuple(
        finding.model_copy(
            update={
                "evidence": tuple(
                    sorted(finding.evidence, key=lambda evidence: evidence.evidence_id)
                )
            }
        )
        for finding in sorted(source.findings, key=lambda finding: finding.finding_id)
    )
    return source.model_copy(
        update={"findings": findings, "limitations": tuple(sorted(source.limitations))}
    )


def _normalized_explanation(explanation: AIExplanation) -> dict[str, object]:
    groups = sorted(
        (
            {
                **group.model_dump(mode="json"),
                "finding_ids": sorted(group.finding_ids),
                "evidence_ids": sorted(group.evidence_ids),
            }
            for group in explanation.groups
        ),
        key=lambda group: json.dumps(group, sort_keys=True),
    )
    actions = sorted(
        (
            {
                **action.model_dump(mode="json"),
                "finding_ids": sorted(action.finding_ids),
                "evidence_ids": sorted(action.evidence_ids),
            }
            for action in explanation.actions
        ),
        key=lambda action: json.dumps(action, sort_keys=True),
    )
    return {
        "summary": explanation.summary,
        "groups": groups,
        "actions": actions,
        "limitations": sorted(explanation.limitations),
        "confidence": explanation.confidence,
        "finding_ids": sorted(explanation.finding_ids),
        "evidence_ids": sorted(explanation.evidence_ids),
    }


def _canonical_explanation(
    source: ExplanationInput, explanation: AIExplanation
) -> AIExplanation:
    try:
        canonical = ExplanationValidator(source).validate(explanation)
    except (AIExplanationRejected, TypeError, ValueError) as error:
        raise ValueError("explanation is not grounded in source facts") from error
    if _normalized_explanation(explanation) != _normalized_explanation(canonical):
        raise ValueError("explanation is not the canonical validated artifact")
    return canonical


def _facts_for(
    scenario_id: str,
    source: ExplanationInput,
    finding_ids: tuple[str, ...],
) -> tuple[CitedFact, ...]:
    by_id = {finding.finding_id: finding for finding in source.findings}
    if set(finding_ids) - set(by_id):
        raise ValueError("claim references an unknown finding")
    return tuple(
        _fact(scenario_id, by_id[finding_id]) for finding_id in sorted(finding_ids)
    )


def _fact(scenario_id: str, finding: ExplanationFinding) -> CitedFact:
    evidence: tuple[ExplanationEvidence, ...] = finding.evidence
    return CitedFact(
        scenario_id=scenario_id,
        finding_id=finding.finding_id,
        rule_id=finding.rule_id,
        severity=finding.severity,
        summary=finding.summary,
        required_action=finding.required_action,
        evidence=tuple(
            sorted(
                (
                    CitedEvidence(
                        evidence_id=item.evidence_id,
                        source_type=item.source_type,
                        source_id=item.source_id,
                    )
                    for item in evidence
                ),
                key=_evidence_key,
            )
        ),
    )


def _claim(kind: ClaimKind, text: str, facts: tuple[CitedFact, ...]) -> AtomicClaim:
    ordered = tuple(sorted(facts, key=_fact_key))
    return AtomicClaim(
        claim_id=stable_claim_id(kind, text, ordered),
        kind=kind,
        text=text,
        cited_facts=ordered,
    )


def _claims_for(
    scenario_id: str,
    source: ExplanationInput,
    explanation: AIExplanation,
) -> tuple[AtomicClaim, ...]:
    all_ids = tuple(finding.finding_id for finding in source.findings)
    all_facts = _facts_for(scenario_id, source, all_ids)
    claims = [_claim("summary", explanation.summary, all_facts)]
    for group in explanation.groups:
        facts = _facts_for(scenario_id, source, group.finding_ids)
        claims.append(_claim("group_title", group.title, facts))
        claims.append(_claim("group_explanation", group.explanation, facts))
    for action in explanation.actions:
        claims.append(
            _claim(
                "action",
                action.action,
                _facts_for(scenario_id, source, action.finding_ids),
            )
        )
    claims.extend(
        _claim("limitation", limitation, all_facts)
        for limitation in explanation.limitations
    )
    claims.append(
        _claim("confidence", f"Confidence: {explanation.confidence}", all_facts)
    )
    ordered = tuple(sorted(claims, key=lambda claim: claim.claim_id))
    if len({claim.claim_id for claim in ordered}) != len(ordered):
        raise ValueError("canonical explanation produced duplicate atomic claims")
    return ordered


def _packet_digest(
    version: str,
    scenario_id: str,
    assessment: AssessmentReference,
    source: ExplanationInput,
    explanation: AIExplanation,
    claims: tuple[AtomicClaim, ...],
) -> str:
    return _digest(
        {
            "version": version,
            "scenario_id": scenario_id,
            "assessment": assessment.model_dump(mode="json"),
            "source": source.model_dump(mode="json"),
            "explanation": explanation.model_dump(mode="json"),
            "claims": [claim.model_dump(mode="json") for claim in claims],
        }
    )


def _assessment_digest(
    version: str,
    artifact_kind: str,
    analysis_run_id: str,
    policy_version: str,
    snapshot: StoredSnapshotIdentity,
    snapshot_fingerprint: str,
    source: ExplanationInput,
) -> str:
    return _digest(
        {
            "version": version,
            "artifact_kind": artifact_kind,
            "analysis_run_id": analysis_run_id,
            "policy_version": policy_version,
            "snapshot": snapshot.model_dump(mode="json"),
            "snapshot_fingerprint": snapshot_fingerprint,
            "source": source.model_dump(mode="json"),
        }
    )


def _snapshot_digest(snapshot: ReleaseSnapshot) -> str:
    canonical = TypeAdapter(ReleaseSnapshot).dump_python(snapshot, mode="json")
    return _digest(canonical)


def _digest(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _load(path: Path) -> object:
    text = read_bounded_text(path)
    if path.suffix.casefold() == ".json":
        return json.loads(text)
    return yaml.safe_load(text)


def main() -> int:
    parser = argparse.ArgumentParser(description="Review grounded benchmark AI claims")
    parser.add_argument("--claims", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--assessment", type=Path)
    args = parser.parse_args()
    try:
        if args.assessment is None:
            raise ValueError("trusted assessment artifact is required")
        claims = ClaimsDocument.model_validate(_load(args.claims))
        review = ReviewDocument.model_validate(_load(args.review))
        assessment = StoredAssessmentArtifact.model_validate(_load(args.assessment))
        result = evaluate_review(claims, review, assessment)
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
