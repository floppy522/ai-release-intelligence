from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from release_intelligence.domain.models import (
    ReadinessAssessment,
    ReadinessFinding,
    ReleaseSnapshot,
)


class ImmutableSnapshotError(RuntimeError):
    """Raised when code attempts to revise a historical source snapshot."""


class IncompatibleSnapshotError(RuntimeError):
    """A stored snapshot cannot be decoded by this application version."""

    def __init__(self, repository_id: str) -> None:
        super().__init__("Stored analysis snapshot version is unsupported")
        self.repository_id = repository_id


@dataclass(frozen=True)
class StoredFindingMetadata:
    finding_id: UUID
    finding: ReadinessFinding
    decision_eligible: bool = False
    decision_fingerprint: str | None = None


@dataclass(frozen=True)
class StoredAnalysisRun:
    id: UUID
    snapshot: ReleaseSnapshot
    findings: tuple[ReadinessFinding, ...]
    assessment: ReadinessAssessment
    policy_version: str
    source_fetched_at: datetime
    finding_metadata: tuple[StoredFindingMetadata, ...] = ()


class AnalysisRepositoryPort(Protocol):
    async def close(self) -> None: ...

    async def create_run(
        self,
        *,
        snapshot: ReleaseSnapshot,
        findings: tuple[ReadinessFinding, ...],
        assessment: ReadinessAssessment,
        policy_version: str,
        source_fetched_at: datetime,
    ) -> UUID: ...

    async def get_run(self, run_id: UUID) -> StoredAnalysisRun: ...

    async def replace_snapshot(
        self, run_id: UUID, snapshot: ReleaseSnapshot
    ) -> None: ...
