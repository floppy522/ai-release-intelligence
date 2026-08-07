from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import selectinload

from release_intelligence.adapters.persistence.models import (
    AnalysisRunRow,
    FindingEvidenceRow,
    ReadinessFindingRow,
    ReleasePolicyRow,
    ReleaseRow,
    ReleaseSnapshotRow,
    RepositoryConnectionRow,
)
from release_intelligence.domain.models import (
    EvidenceRef,
    ReadinessAssessment,
    ReadinessFinding,
    ReleaseSnapshot,
    ReleaseStatus,
)
from release_intelligence.ports.repositories import (
    ImmutableSnapshotError,
    IncompatibleSnapshotError,
    StoredAnalysisRun,
)

logger = logging.getLogger(__name__)


class AnalysisRepository:
    """PostgreSQL-backed storage for append-only release analysis runs."""

    def __init__(
        self,
        database_url: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._engine: AsyncEngine = create_async_engine(database_url, pool_pre_ping=True)
        self._sessions = async_sessionmaker(self._engine, expire_on_commit=False)
        self._clock = clock or (lambda: datetime.now(UTC))

    async def close(self) -> None:
        """Release pooled asyncpg connections before the owning event loop closes."""
        await self._engine.dispose()

    async def create_run(
        self,
        *,
        snapshot: ReleaseSnapshot,
        findings: tuple[ReadinessFinding, ...],
        assessment: ReadinessAssessment,
        policy_version: str,
        source_fetched_at: datetime,
    ) -> UUID:
        self._validate_run(findings, assessment, policy_version, source_fetched_at)

        started_at = self._clock_now()
        try:
            async with self._sessions() as session:
                async with session.begin():
                    release = await self._release_for_snapshot(
                        session, snapshot, policy_version
                    )
                    completed_at = self._clock_now()
                    run = AnalysisRunRow(
                        release_id=release.id,
                        policy_version=policy_version,
                        source_fetched_at=source_fetched_at,
                        state="COMPLETED",
                        assessment_status=assessment.status.value,
                        started_at=started_at,
                        completed_at=completed_at,
                    )
                    session.add(run)
                    await session.flush()
                    session.add(
                        ReleaseSnapshotRow(
                            analysis_run=run,
                            payload=self._snapshot_payload(snapshot),
                        )
                    )
                    await session.flush()
                    for finding_position, finding in enumerate(findings):
                        finding_row = ReadinessFindingRow(
                            analysis_run=run,
                            position=finding_position,
                            rule_id=finding.rule_id,
                            severity=finding.severity,
                            summary=finding.summary,
                            required_action=finding.required_action,
                        )
                        session.add(finding_row)
                        for evidence_position, evidence in enumerate(finding.evidence):
                            session.add(
                                FindingEvidenceRow(
                                    finding=finding_row,
                                    position=evidence_position,
                                    evidence_id=evidence.evidence_id,
                                    source_type=evidence.source_type,
                                    source_id=evidence.source_id,
                                    url=evidence.url,
                                    fingerprint=evidence.fingerprint,
                                )
                            )
                return run.id
        except SQLAlchemyError:
            try:
                await self._record_failed_run(snapshot, policy_version, source_fetched_at)
            except Exception:  # noqa: BLE001 - never mask the original database error
                # Preserve the original write failure; the audit attempt is best-effort
                # when the database itself is unavailable.
                logger.error("Failed analysis audit could not be persisted")
            raise

    async def get_run(self, run_id: UUID) -> StoredAnalysisRun:
        statement = (
            select(AnalysisRunRow)
            .where(AnalysisRunRow.id == run_id)
            .options(
                selectinload(AnalysisRunRow.snapshot),
                selectinload(AnalysisRunRow.release).selectinload(
                    ReleaseRow.repository
                ),
                selectinload(AnalysisRunRow.findings).selectinload(
                    ReadinessFindingRow.evidence
                ),
            )
        )
        async with self._sessions() as session:
            run = (await session.execute(statement)).scalar_one_or_none()
        if run is None:
            raise KeyError(f"Unknown analysis run: {run_id}")

        findings = tuple(self._finding_from_row(finding) for finding in run.findings)
        snapshot = self._decode_snapshot(
            run.snapshot.payload,
            repository_id=run.release.repository.external_repository_id,
        )
        return StoredAnalysisRun(
            id=run.id,
            snapshot=snapshot,
            findings=findings,
            assessment=ReadinessAssessment(
                status=ReleaseStatus(self._assessment_status(run)), findings=findings
            ),
            policy_version=run.policy_version,
            source_fetched_at=run.source_fetched_at,
        )

    async def replace_snapshot(self, run_id: UUID, snapshot: ReleaseSnapshot) -> None:
        del run_id, snapshot
        raise ImmutableSnapshotError("Release snapshots are immutable once persisted")

    async def _release_for_snapshot(
        self,
        session: AsyncSession,
        snapshot: ReleaseSnapshot,
        policy_version: str,
    ) -> ReleaseRow:
        provider = "fixture" if snapshot.repository_id == "fixture" else "github"
        await session.execute(
            insert(RepositoryConnectionRow)
            .values(
                provider=provider,
                external_repository_id=snapshot.repository_id,
                full_name=snapshot.repository_full_name,
            )
            .on_conflict_do_nothing(constraint="uq_repository_identity")
        )
        repository = await session.scalar(
            select(RepositoryConnectionRow)
            .where(
                RepositoryConnectionRow.provider == provider,
                RepositoryConnectionRow.external_repository_id
                == snapshot.repository_id,
            )
            .order_by(RepositoryConnectionRow.id)
        )
        if repository is None:
            raise RuntimeError("repository identity was not persisted")

        await session.execute(
            insert(ReleasePolicyRow)
            .values(repository_id=repository.id, version=policy_version)
            .on_conflict_do_nothing(constraint="uq_release_policy_version")
        )

        milestone_number = snapshot.milestone_number
        await session.execute(
            insert(ReleaseRow)
            .values(
                repository_id=repository.id,
                github_milestone_number=milestone_number,
                name=snapshot.release_name,
            )
            .on_conflict_do_nothing(constraint="uq_release_repository_milestone")
        )
        release = await session.scalar(
            select(ReleaseRow)
            .where(
                ReleaseRow.repository_id == repository.id,
                ReleaseRow.github_milestone_number == milestone_number,
            )
            .order_by(ReleaseRow.id)
        )
        if release is None:
            raise RuntimeError("release identity was not persisted")
        return release

    async def _record_failed_run(
        self,
        snapshot: ReleaseSnapshot,
        policy_version: str,
        source_fetched_at: datetime,
    ) -> None:
        started_at = self._clock_now()
        async with self._sessions() as session, session.begin():
            release = await self._release_for_snapshot(session, snapshot, policy_version)
            completed_at = self._clock_now()
            session.add(
                AnalysisRunRow(
                    release_id=release.id,
                    policy_version=policy_version,
                    source_fetched_at=source_fetched_at,
                    state="FAILED",
                    assessment_status=None,
                    started_at=started_at,
                    completed_at=completed_at,
                )
            )

    @staticmethod
    def _validate_run(
        findings: tuple[ReadinessFinding, ...],
        assessment: ReadinessAssessment,
        policy_version: str,
        source_fetched_at: datetime,
    ) -> None:
        if not policy_version:
            raise ValueError("policy_version is required")
        if source_fetched_at.tzinfo is None:
            raise ValueError("source_fetched_at must be timezone-aware")
        if assessment.findings != findings:
            raise ValueError("assessment findings must match persisted findings")
        if any(not finding.evidence for finding in findings):
            raise ValueError("each readiness finding requires evidence")

    def _clock_now(self) -> datetime:
        timestamp = self._clock()
        if timestamp.tzinfo is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return timestamp.astimezone(UTC)

    @staticmethod
    def _assessment_status(run: AnalysisRunRow) -> str:
        if run.assessment_status is None:
            raise ValueError("failed analysis runs do not have an assessment")
        return run.assessment_status

    @staticmethod
    def _snapshot_payload(snapshot: ReleaseSnapshot) -> dict[str, object]:
        return cast(
            dict[str, object],
            TypeAdapter(ReleaseSnapshot).dump_python(snapshot, mode="json"),
        )

    @staticmethod
    def _snapshot_from_payload(payload: dict[str, object]) -> ReleaseSnapshot:
        return TypeAdapter(ReleaseSnapshot).validate_python(payload)

    @classmethod
    def _decode_snapshot(
        cls, payload: dict[str, object], *, repository_id: str
    ) -> ReleaseSnapshot:
        try:
            return cls._snapshot_from_payload(payload)
        except ValidationError:
            raise IncompatibleSnapshotError(repository_id) from None

    @staticmethod
    def _finding_from_row(row: ReadinessFindingRow) -> ReadinessFinding:
        return ReadinessFinding(
            rule_id=row.rule_id,
            severity=row.severity,
            summary=row.summary,
            required_action=row.required_action,
            evidence=tuple(
                EvidenceRef(
                    evidence_id=evidence.evidence_id,
                    source_type=evidence.source_type,
                    source_id=evidence.source_id,
                    url=evidence.url,
                    fingerprint=evidence.fingerprint,
                )
                for evidence in row.evidence
            ),
        )
