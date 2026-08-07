from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import select
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
    StoredAnalysisRun,
)


class AnalysisRepository:
    """PostgreSQL-backed storage for append-only release analysis runs."""

    def __init__(self, database_url: str) -> None:
        self._engine: AsyncEngine = create_async_engine(database_url, pool_pre_ping=True)
        self._sessions = async_sessionmaker(self._engine, expire_on_commit=False)

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

        async with self._sessions() as session:
            async with session.begin():
                release = await self._release_for_snapshot(session, snapshot, policy_version)
                run = AnalysisRunRow(
                    release=release,
                    policy_version=policy_version,
                    source_fetched_at=source_fetched_at,
                    state="COMPLETED",
                    assessment_status=assessment.status.value,
                    completed_at=source_fetched_at,
                )
                session.add(run)
                await session.flush()
                session.add(
                    ReleaseSnapshotRow(
                        analysis_run=run,
                        payload=self._snapshot_payload(snapshot),
                    )
                )
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

    async def get_run(self, run_id: UUID) -> StoredAnalysisRun:
        statement = (
            select(AnalysisRunRow)
            .where(AnalysisRunRow.id == run_id)
            .options(
                selectinload(AnalysisRunRow.snapshot),
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
        return StoredAnalysisRun(
            id=run.id,
            snapshot=self._snapshot_from_payload(run.snapshot.payload),
            findings=findings,
            assessment=ReadinessAssessment(
                status=ReleaseStatus(run.assessment_status), findings=findings
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
        repository = await session.scalar(
            select(RepositoryConnectionRow).where(
                RepositoryConnectionRow.provider == "fixture",
                RepositoryConnectionRow.external_repository_id
                == "example/release-intelligence",
            )
        )
        if repository is None:
            repository = RepositoryConnectionRow(
                provider="fixture",
                external_repository_id="example/release-intelligence",
                full_name="example/release-intelligence",
            )
            session.add(repository)
            await session.flush()

        policy = await session.scalar(
            select(ReleasePolicyRow).where(
                ReleasePolicyRow.repository_id == repository.id,
                ReleasePolicyRow.version == policy_version,
            )
        )
        if policy is None:
            session.add(ReleasePolicyRow(repository=repository, version=policy_version))

        milestone_number = self._milestone_number(snapshot.issue_number)
        release = await session.scalar(
            select(ReleaseRow).where(
                ReleaseRow.repository_id == repository.id,
                ReleaseRow.github_milestone_number == milestone_number,
            )
        )
        if release is None:
            release = ReleaseRow(
                repository=repository,
                github_milestone_number=milestone_number,
                name=snapshot.release_name,
            )
            session.add(release)
        return release

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

    @staticmethod
    def _milestone_number(issue_number: str) -> int:
        try:
            return int(issue_number)
        except ValueError as error:
            raise ValueError("release issue_number must be numeric") from error

    @staticmethod
    def _snapshot_payload(snapshot: ReleaseSnapshot) -> dict[str, object]:
        return {
            "release_name": snapshot.release_name,
            "issue_number": snapshot.issue_number,
            "issue_labels": list(snapshot.issue_labels),
            "linked_pr_numbers": list(snapshot.linked_pr_numbers),
            "issue_evidence": {
                "evidence_id": snapshot.issue_evidence.evidence_id,
                "source_type": snapshot.issue_evidence.source_type,
                "source_id": snapshot.issue_evidence.source_id,
                "url": snapshot.issue_evidence.url,
                "fingerprint": snapshot.issue_evidence.fingerprint,
            },
        }

    @staticmethod
    def _snapshot_from_payload(payload: dict[str, object]) -> ReleaseSnapshot:
        evidence_payload = cast(dict[str, str], payload["issue_evidence"])
        return ReleaseSnapshot(
            release_name=cast(str, payload["release_name"]),
            issue_number=cast(str, payload["issue_number"]),
            issue_labels=tuple(cast(list[str], payload["issue_labels"])),
            linked_pr_numbers=tuple(cast(list[str], payload["linked_pr_numbers"])),
            issue_evidence=EvidenceRef(**evidence_payload),
        )

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
