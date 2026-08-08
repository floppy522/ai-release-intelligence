from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

from release_intelligence.domain.models import ReadinessAssessment

_FINGERPRINT = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_REASON_LENGTH = 4_000
_MAX_ACTOR_LENGTH = 255


class DecisionKind(StrEnum):
    ACCEPTED_RISK = "ACCEPTED_RISK"
    RELEASE_BLOCKER = "RELEASE_BLOCKER"


class DecisionValidationError(ValueError):
    """A human decision does not satisfy the trusted domain boundary."""


class DecisionFindingNotFoundError(Exception):
    """The requested finding is absent, stale, or not decision eligible."""


class DecisionConflictError(Exception):
    """The immutable run no longer has the decision state the caller observed."""


class DecisionPersistenceError(Exception):
    """Sanitized decision persistence failure safe for route handling."""

    def __init__(self, _detail: str | None = None) -> None:
        super().__init__("Decision persistence unavailable")


@dataclass(frozen=True, slots=True)
class HumanDecision:
    id: UUID
    fingerprint: str
    kind: DecisionKind
    reason: str
    actor_id: str
    decided_at: datetime
    analysis_run_id: UUID | None = None
    finding_id: UUID | None = None
    supersedes_decision_id: UUID | None = None

    @property
    def blocks_release(self) -> bool:
        return self.kind is DecisionKind.RELEASE_BLOCKER


@dataclass(frozen=True, slots=True)
class DecisionResult:
    decision: HumanDecision
    assessment: ReadinessAssessment


class DecisionStore(Protocol):
    async def persist_decision(
        self,
        *,
        run_id: UUID,
        finding_id: UUID,
        authorized_repository_id: str,
        decision: HumanDecision,
    ) -> DecisionResult: ...


class DecisionService:
    """Create canonical immutable decisions before they cross persistence boundaries."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], UUID] | None = None,
        store: DecisionStore | None = None,
    ) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or uuid4
        self._store = store

    def record(
        self,
        fingerprint: str,
        kind: DecisionKind,
        reason: str,
        actor: str,
        *,
        analysis_run_id: UUID | None = None,
        finding_id: UUID | None = None,
        supersedes_decision_id: UUID | None = None,
    ) -> HumanDecision:
        if type(fingerprint) is not str or _FINGERPRINT.fullmatch(fingerprint) is None:
            raise DecisionValidationError("fingerprint is invalid")
        if not isinstance(kind, DecisionKind):
            raise DecisionValidationError("decision kind is invalid")
        if type(reason) is not str:
            raise DecisionValidationError("reason is required")
        canonical_reason = reason.strip()
        if not canonical_reason:
            raise DecisionValidationError("reason is required")
        if len(canonical_reason) > _MAX_REASON_LENGTH:
            raise DecisionValidationError("reason is too long")
        if type(actor) is not str:
            raise DecisionValidationError("actor is required")
        canonical_actor = actor.strip()
        if not canonical_actor:
            raise DecisionValidationError("actor is required")
        if len(canonical_actor) > _MAX_ACTOR_LENGTH:
            raise DecisionValidationError("actor is too long")
        decided_at = self._clock()
        if decided_at.tzinfo is None:
            raise DecisionValidationError("decision clock must be timezone-aware")
        return HumanDecision(
            id=self._id_factory(),
            fingerprint=fingerprint,
            kind=kind,
            reason=canonical_reason,
            actor_id=canonical_actor,
            decided_at=decided_at.astimezone(UTC),
            analysis_run_id=analysis_run_id,
            finding_id=finding_id,
            supersedes_decision_id=supersedes_decision_id,
        )

    async def record_for_run(
        self,
        *,
        run_id: UUID,
        finding_id: UUID,
        fingerprint: str,
        kind: DecisionKind,
        reason: str,
        actor: str,
        authorized_repository_id: str,
    ) -> DecisionResult:
        decision = self.record(
            fingerprint,
            kind,
            reason,
            actor,
            analysis_run_id=run_id,
            finding_id=finding_id,
        )
        if self._store is None:
            raise DecisionPersistenceError()
        return await self._store.persist_decision(
            run_id=run_id,
            finding_id=finding_id,
            authorized_repository_id=authorized_repository_id,
            decision=decision,
        )
