from __future__ import annotations

import asyncio
import unicodedata
from collections.abc import Iterable
from typing import Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError

from release_intelligence.domain.models import ReadinessFinding
from release_intelligence.ports.ai import (
    AI_EXPLANATION_PENDING_CONTENT,
    AI_EXPLANATION_UNAVAILABLE_CONTENT,
    AIExplanation,
    AIExplanationProvider,
    AIExplanationStore,
    AIExplanationUnavailable,
    ExplanationAction,
    ExplanationEvidence,
    ExplanationFinding,
    ExplanationGroup,
    ExplanationInput,
    ExplanationMetadata,
    FindingSeverity,
)
from release_intelligence.ports.repositories import StoredAnalysisRun

MAX_UNTRUSTED_CHARACTERS = 200
MAX_WARNINGS = 20
PRIORITY_SEVERITIES = frozenset({"BLOCKING", "DECISION_REQUIRED", "INSUFFICIENT_DATA"})
SEVERITY_ORDER = {
    "BLOCKING": 0,
    "DECISION_REQUIRED": 1,
    "INSUFFICIENT_DATA": 2,
    "WARNING": 3,
}
INPUT_LIMITATIONS = (
    "The AI explanation may only summarize the supplied deterministic findings and evidence identifiers.",
    "The deterministic readiness status, severities, evidence, and human decisions remain authoritative.",
)


class AIExplanationRejected(RuntimeError):
    """Structured model output failed an application grounding invariant."""

    def __init__(self) -> None:
        super().__init__("AI explanation rejected")


class _CachedExplanation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["available"] = "available"
    explanation: AIExplanation
    metadata: ExplanationMetadata


def _safe_untrusted_text(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value)
    without_controls = "".join(
        " " if unicodedata.category(character) == "Cc" else character
        for character in normalized
    )
    return without_controls.strip()[:MAX_UNTRUSTED_CHARACTERS]


def _selected_findings(run: StoredAnalysisRun) -> tuple[ReadinessFinding, ...]:
    critical = tuple(
        item for item in run.assessment.findings if item.severity in PRIORITY_SEVERITIES
    )
    warnings = tuple(
        item
        for item in run.assessment.findings
        if item.severity not in PRIORITY_SEVERITIES
    )[:MAX_WARNINGS]
    return (*critical, *warnings)


def build_explanation_input(run: StoredAnalysisRun) -> ExplanationInput:
    """Project one immutable assessment onto the explicit AI input allowlist."""

    metadata_by_finding = {item.finding: item for item in run.finding_metadata}
    safe_findings: list[ExplanationFinding] = []
    finding_ids: set[str] = set()
    try:
        for finding in _selected_findings(run):
            metadata = metadata_by_finding[finding]
            finding_id = str(metadata.finding_id)
            if finding_id in finding_ids:
                raise AIExplanationUnavailable()
            finding_ids.add(finding_id)
            evidence_ids: set[str] = set()
            safe_evidence: list[ExplanationEvidence] = []
            for evidence in finding.evidence:
                if evidence.evidence_id in evidence_ids:
                    raise AIExplanationUnavailable()
                evidence_ids.add(evidence.evidence_id)
                safe_evidence.append(
                    ExplanationEvidence(
                        evidence_id=evidence.evidence_id,
                        source_type=evidence.source_type,
                        source_id=_safe_untrusted_text(evidence.source_id),
                    )
                )
            safe_findings.append(
                ExplanationFinding(
                    finding_id=finding_id,
                    rule_id=finding.rule_id,
                    severity=cast(FindingSeverity, finding.severity),
                    summary=_safe_untrusted_text(finding.summary),
                    required_action=_safe_untrusted_text(finding.required_action),
                    evidence=tuple(safe_evidence),
                )
            )
        return ExplanationInput(
            deterministic_status=run.assessment.status.value,
            release_name=_safe_untrusted_text(run.snapshot.release_name),
            source_fetched_at=run.source_fetched_at.isoformat(),
            findings=tuple(safe_findings),
            limitations=INPUT_LIMITATIONS,
        )
    except (KeyError, TypeError, ValueError, ValidationError):
        raise AIExplanationUnavailable() from None


class ExplanationValidator:
    def __init__(self, input: ExplanationInput) -> None:
        self._input = input
        self._findings = {item.finding_id: item for item in input.findings}
        self._evidence_to_findings: dict[str, set[str]] = {}
        for item in input.findings:
            for evidence in item.evidence:
                self._evidence_to_findings.setdefault(evidence.evidence_id, set()).add(
                    item.finding_id
                )

    def validate(self, explanation: AIExplanation) -> AIExplanation:
        try:
            candidate = AIExplanation.model_validate(explanation.model_dump())
            self._validate_references(candidate)
            self._validate_groups(candidate)
            self._validate_actions(candidate)
            self._validate_duplicates(candidate)
        except (TypeError, ValueError, ValidationError):
            raise AIExplanationRejected() from None
        normalized = candidate.model_copy(
            update={
                "groups": tuple(sorted(candidate.groups, key=_group_key)),
                "actions": tuple(sorted(candidate.actions, key=_action_key)),
                "limitations": tuple(
                    sorted(candidate.limitations, key=lambda value: value.casefold())
                ),
                "finding_ids": tuple(sorted(candidate.finding_ids)),
                "evidence_ids": tuple(sorted(candidate.evidence_ids)),
            }
        )
        if explanation.metadata is not None:
            normalized.attach_metadata(explanation.metadata)
        return normalized

    def _validate_references(self, explanation: AIExplanation) -> None:
        finding_ids = set(explanation.finding_ids)
        evidence_ids = set(explanation.evidence_ids)
        if not finding_ids.issubset(self._findings):
            raise ValueError("unsupported finding reference")
        if not evidence_ids.issubset(self._evidence_to_findings):
            raise ValueError("unsupported evidence reference")
        if any(
            not (self._evidence_to_findings[evidence_id] & finding_ids)
            for evidence_id in evidence_ids
        ):
            raise ValueError("evidence is not linked to a referenced finding")
        nested_finding_ids = {
            identifier
            for group in explanation.groups
            for identifier in group.finding_ids
        } | {
            identifier
            for action in explanation.actions
            for identifier in action.finding_ids
        }
        nested_evidence_ids = {
            identifier
            for group in explanation.groups
            for identifier in group.evidence_ids
        } | {
            identifier
            for action in explanation.actions
            for identifier in action.evidence_ids
        }
        if nested_finding_ids != finding_ids or nested_evidence_ids != evidence_ids:
            raise ValueError("summary references do not match grounded content")

    def _validate_groups(self, explanation: AIExplanation) -> None:
        top_findings = set(explanation.finding_ids)
        top_evidence = set(explanation.evidence_ids)
        for group in explanation.groups:
            finding_ids = set(group.finding_ids)
            evidence_ids = set(group.evidence_ids)
            if not finding_ids.issubset(top_findings):
                raise ValueError("group finding is unsupported")
            if not evidence_ids.issubset(top_evidence):
                raise ValueError("group evidence is unsupported")
            if any(
                self._findings[identifier].severity != group.severity
                for identifier in finding_ids
            ):
                raise ValueError("group severity conflicts with deterministic facts")
            self._require_linked_evidence(finding_ids, evidence_ids)

    def _validate_actions(self, explanation: AIExplanation) -> None:
        top_findings = set(explanation.finding_ids)
        top_evidence = set(explanation.evidence_ids)
        for action in explanation.actions:
            finding_ids = set(action.finding_ids)
            evidence_ids = set(action.evidence_ids)
            if not finding_ids or not finding_ids.issubset(top_findings):
                raise ValueError("action finding is unsupported")
            if not evidence_ids.issubset(top_evidence):
                raise ValueError("action evidence is unsupported")
            if any(
                self._findings[identifier].required_action != action.action
                for identifier in finding_ids
            ):
                raise ValueError("action was not supplied by deterministic findings")
            self._require_linked_evidence(finding_ids, evidence_ids)

    def _require_linked_evidence(
        self, finding_ids: set[str], evidence_ids: set[str]
    ) -> None:
        if any(
            not (self._evidence_to_findings[identifier] & finding_ids)
            for identifier in evidence_ids
        ):
            raise ValueError("evidence is not linked to this claim")

    @staticmethod
    def _validate_duplicates(explanation: AIExplanation) -> None:
        _require_unique(explanation.finding_ids)
        _require_unique(explanation.evidence_ids)
        _require_unique(explanation.limitations)
        _require_unique(group.model_dump_json() for group in explanation.groups)
        _require_unique(action.model_dump_json() for action in explanation.actions)
        _require_unique(
            identifier
            for group in explanation.groups
            for identifier in group.finding_ids
        )
        _require_unique(
            identifier
            for action in explanation.actions
            for identifier in action.finding_ids
        )
        for group in explanation.groups:
            _require_unique(group.finding_ids)
            _require_unique(group.evidence_ids)
        for action in explanation.actions:
            _require_unique(action.finding_ids)
            _require_unique(action.evidence_ids)


class ExplanationService:
    def __init__(
        self,
        provider: AIExplanationProvider,
        *,
        store: AIExplanationStore | None = None,
    ) -> None:
        self._provider = provider
        self._store = store
        self._locks: dict[UUID, asyncio.Lock] = {}
        self._attempted: set[UUID] = set()
        self._memory_results: dict[UUID, AIExplanation | None] = {}

    async def generate(self, run: StoredAnalysisRun) -> AIExplanation:
        lock = self._locks.setdefault(run.id, asyncio.Lock())
        async with lock:
            return await self._generate_once(run)

    async def _generate_once(self, run: StoredAnalysisRun) -> AIExplanation:
        input = build_explanation_input(run)
        reserved = False
        try:
            existing = await self._existing(run.id, input)
            if existing is not None:
                return existing
            reserved = await self._reserve(run.id)
            if not reserved:
                existing = await self._existing(run.id, input)
                if existing is not None:
                    return existing
                raise AIExplanationUnavailable()
            generated = await self._provider.explain(input)
            validated = ExplanationValidator(input).validate(generated)
            if validated.metadata is None:
                raise AIExplanationUnavailable()
            await self._complete(run.id, validated)
            return validated
        except (AIExplanationRejected, AIExplanationUnavailable):
            if reserved:
                await self._fail(run.id)
            raise
        except Exception:  # noqa: BLE001 - optional provider trust boundary
            if reserved:
                await self._fail(run.id)
            raise AIExplanationUnavailable() from None

    async def _existing(
        self, run_id: UUID, input: ExplanationInput
    ) -> AIExplanation | None:
        if self._store is None:
            if run_id not in self._attempted:
                return None
            cached = self._memory_results.get(run_id)
            if cached is None:
                raise AIExplanationUnavailable()
            return ExplanationValidator(input).validate(cached)
        content = await self._store.load_explanation(run_id)
        if content is None:
            return None
        if content in {
            AI_EXPLANATION_PENDING_CONTENT,
            AI_EXPLANATION_UNAVAILABLE_CONTENT,
        }:
            raise AIExplanationUnavailable()
        try:
            stored = _CachedExplanation.model_validate_json(content)
            stored.explanation.attach_metadata(stored.metadata)
            return ExplanationValidator(input).validate(stored.explanation)
        except (TypeError, ValueError, ValidationError, AIExplanationRejected):
            raise AIExplanationUnavailable() from None

    async def _reserve(self, run_id: UUID) -> bool:
        if self._store is not None:
            return await self._store.reserve_explanation(run_id)
        if run_id in self._attempted:
            return False
        self._attempted.add(run_id)
        self._memory_results[run_id] = None
        return True

    async def _complete(self, run_id: UUID, explanation: AIExplanation) -> None:
        metadata = explanation.metadata
        if metadata is None:
            raise AIExplanationUnavailable()
        if self._store is not None:
            content = _CachedExplanation(
                explanation=explanation,
                metadata=metadata,
            ).model_dump_json()
            await self._store.complete_explanation(run_id, content)
            return
        self._memory_results[run_id] = explanation

    async def _fail(self, run_id: UUID) -> None:
        if self._store is None:
            self._attempted.add(run_id)
            self._memory_results[run_id] = None
            return
        try:
            await self._store.fail_explanation(run_id)
        except Exception:  # noqa: BLE001 - fallback must retain the original safe error
            return


def _require_unique(values: Iterable[str]) -> None:
    materialized = tuple(values)
    if len(set(materialized)) != len(materialized):
        raise ValueError("duplicate output references")


def _group_key(group: ExplanationGroup) -> tuple[object, ...]:
    return (
        SEVERITY_ORDER[group.severity],
        tuple(sorted(group.finding_ids)),
        group.title.casefold(),
    )


def _action_key(action: ExplanationAction) -> tuple[object, ...]:
    return (tuple(sorted(action.finding_ids)), action.action.casefold())
