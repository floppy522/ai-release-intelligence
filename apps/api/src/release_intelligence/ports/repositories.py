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


@dataclass(frozen=True)
class StoredAnalysisRun:
    id: UUID
    snapshot: ReleaseSnapshot
    findings: tuple[ReadinessFinding, ...]
    assessment: ReadinessAssessment
    policy_version: str
    source_fetched_at: datetime


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
