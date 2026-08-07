"""PostgreSQL contracts for persisted, immutable analysis runs.

These tests deliberately use ``DATABASE_URL`` and a real PostgreSQL schema.  A
missing URL is an error: SQLite, fake connections, and skipped integration
coverage would not exercise the persistence guarantees this suite protects.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict
from uuid import UUID

import asyncpg
import pytest
from sqlalchemy.exc import DBAPIError

from release_intelligence.adapters.persistence.repositories import (
    AnalysisRepository,
    ImmutableSnapshotError,
)
from release_intelligence.domain.models import (
    EvidenceRef,
    ReadinessAssessment,
    ReadinessFinding,
    ReleaseSnapshot,
    ReleaseStatus,
)

API_ROOT = Path(__file__).resolve().parents[2]


class CreateRunArguments(TypedDict):
    snapshot: ReleaseSnapshot
    findings: tuple[ReadinessFinding, ...]
    assessment: ReadinessAssessment
    policy_version: str
    source_fetched_at: datetime


def _require_database_url() -> str:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        raise RuntimeError(
            "DATABASE_URL is required for PostgreSQL persistence integration tests"
        )
    if not database_url.startswith(("postgresql://", "postgresql+asyncpg://")):
        raise RuntimeError("DATABASE_URL must use a PostgreSQL URL")
    return database_url


def _asyncpg_url(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


@pytest.fixture(scope="session")
def database_url() -> str:
    return _require_database_url()


@pytest.fixture(scope="session", autouse=True)
def migrated_database(database_url: str) -> None:
    """Apply the real migrations before exercising the repository boundary."""
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=API_ROOT,
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture
async def postgres(database_url: str) -> asyncpg.Connection:
    connection = await asyncpg.connect(_asyncpg_url(database_url))
    await connection.execute("TRUNCATE TABLE analysis_runs RESTART IDENTITY CASCADE")
    try:
        yield connection
    finally:
        await connection.close()


@pytest.fixture
async def repository(database_url: str) -> AsyncIterator[AnalysisRepository]:
    """Dispose each engine before pytest closes its function-scoped event loop."""
    analysis_repository = AnalysisRepository(database_url)
    try:
        yield analysis_repository
    finally:
        await analysis_repository.close()


@pytest.fixture
def fixture_run() -> CreateRunArguments:
    """Literal data that must survive a PostgreSQL round trip unchanged."""
    evidence = EvidenceRef(
        evidence_id="issue-142",
        source_type="github_issue",
        source_id="142",
        url="https://github.com/example/release-intelligence/issues/142",
        fingerprint="sha256:issue-142",
    )
    snapshot = ReleaseSnapshot(
        release_name="2026.08",
        issue_number="142",
        milestone_number=7,
        issue_labels=("code-change", "release-blocker"),
        linked_pr_numbers=(),
        issue_evidence=evidence,
        fetch_started_at=datetime(2026, 8, 7, 14, 29, tzinfo=UTC),
        fetched_at=datetime(2026, 8, 7, 14, 30, tzinfo=UTC),
        complete=True,
        candidate_ref="release/2026-08-10",
        candidate_sha="4" * 40,
    )
    finding = ReadinessFinding(
        rule_id="scope.code_change_requires_pr",
        severity="BLOCKING",
        summary="Issue #142 has no linked PR",
        required_action="Link a merged PR to Issue #142",
        evidence=(evidence,),
    )
    return {
        "snapshot": snapshot,
        "findings": (finding,),
        "assessment": ReadinessAssessment(
            status=ReleaseStatus.NOT_READY,
            findings=(finding,),
        ),
        "policy_version": "2026.08.1",
        "source_fetched_at": datetime(2026, 8, 7, 14, 30, tzinfo=UTC),
    }


async def test_create_run_persists_complete_analysis_atomically(
    repository: AnalysisRepository,
    fixture_run: CreateRunArguments,
    postgres: asyncpg.Connection,
) -> None:
    """A partial insert must not satisfy a successful analysis run."""
    run_id = await repository.create_run(**fixture_run)

    assert isinstance(run_id, UUID)
    assert await postgres.fetchval("SELECT count(*) FROM analysis_runs") == 1
    assert await postgres.fetchval("SELECT count(*) FROM release_snapshots") == 1
    assert await postgres.fetchval("SELECT count(*) FROM readiness_findings") == 1
    assert (
        await postgres.fetchval("SELECT github_milestone_number FROM releases") == 7
    )
    completion = await postgres.fetchrow(
        "SELECT started_at, completed_at, source_fetched_at FROM analysis_runs"
    )
    assert completion is not None
    assert completion["completed_at"] >= completion["started_at"]
    assert completion["completed_at"] != completion["source_fetched_at"]


async def test_create_run_records_failed_audit_after_snapshot_insert_is_rejected(
    repository: AnalysisRepository,
    fixture_run: CreateRunArguments,
    postgres: asyncpg.Connection,
) -> None:
    """A before-insert database failure retains only one immutable failed audit row."""
    await postgres.execute(
        """
        CREATE FUNCTION reject_test_snapshot_insert() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'test snapshot insert failure';
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER reject_test_snapshot_insert
        BEFORE INSERT ON release_snapshots
        FOR EACH ROW EXECUTE FUNCTION reject_test_snapshot_insert();
        """
    )
    try:
        with pytest.raises(DBAPIError, match="test snapshot insert failure"):
            await repository.create_run(**fixture_run)
    finally:
        await postgres.execute(
            "DROP TRIGGER IF EXISTS reject_test_snapshot_insert ON release_snapshots;"
            "DROP FUNCTION IF EXISTS reject_test_snapshot_insert();"
        )

    failed_run = await postgres.fetchrow(
        "SELECT state, assessment_status, policy_version, source_fetched_at "
        "FROM analysis_runs"
    )
    assert failed_run is not None
    assert failed_run["state"] == "FAILED"
    assert failed_run["assessment_status"] is None
    assert failed_run["policy_version"] == "2026.08.1"
    assert failed_run["source_fetched_at"] == datetime(2026, 8, 7, 14, 30, tzinfo=UTC)
    assert await postgres.fetchval("SELECT count(*) FROM analysis_runs") == 1
    assert await postgres.fetchval("SELECT count(*) FROM release_snapshots") == 0
    assert await postgres.fetchval("SELECT count(*) FROM readiness_findings") == 0


async def test_snapshot_update_is_rejected_and_original_audit_record_survives(
    repository: AnalysisRepository,
    fixture_run: CreateRunArguments,
    postgres: asyncpg.Connection,
) -> None:
    """Historical source state must be append-only rather than silently revised."""
    run_id = await repository.create_run(**fixture_run)
    original_snapshot = fixture_run["snapshot"]
    replacement_snapshot = replace(original_snapshot, issue_number="143")

    with pytest.raises(ImmutableSnapshotError):
        await repository.replace_snapshot(run_id, replacement_snapshot)

    stored = await repository.get_run(run_id)
    assert stored.snapshot == original_snapshot

    with pytest.raises(asyncpg.PostgresError, match="immutable analysis records"):
        await postgres.execute(
            "UPDATE release_snapshots SET payload = '{}'::jsonb "
            "WHERE analysis_run_id = $1",
            run_id,
        )
    assert (await repository.get_run(run_id)).snapshot == original_snapshot


async def test_get_run_retrieves_all_persisted_analysis_audit_fields(
    repository: AnalysisRepository,
    fixture_run: CreateRunArguments,
    postgres: asyncpg.Connection,
) -> None:
    """Retrieval must expose the exact evidence-backed decision that was stored."""
    run_id = await repository.create_run(**fixture_run)

    stored = await repository.get_run(run_id)

    assert stored.id == run_id
    assert stored.snapshot == fixture_run["snapshot"]
    assert stored.snapshot.milestone_number == 7
    assert stored.snapshot.fetch_started_at == datetime(
        2026, 8, 7, 14, 29, tzinfo=UTC
    )
    assert stored.snapshot.candidate_ref == "release/2026-08-10"
    assert stored.snapshot.candidate_sha == "4" * 40
    assert stored.findings == fixture_run["findings"]
    assert stored.assessment == fixture_run["assessment"]
    assert stored.policy_version == "2026.08.1"
    assert stored.source_fetched_at == datetime(2026, 8, 7, 14, 30, tzinfo=UTC)


async def test_create_run_records_distinct_clocked_start_and_completion_times(
    database_url: str,
    fixture_run: CreateRunArguments,
    postgres: asyncpg.Connection,
) -> None:
    """Terminal rows record completion after identity creation, not source fetch time."""
    started_at = datetime(2026, 8, 7, 15, 0, tzinfo=UTC)
    completed_at = datetime(2026, 8, 7, 15, 1, tzinfo=UTC)
    clock_values = iter((started_at, completed_at))
    clocked_repository = AnalysisRepository(database_url, clock=lambda: next(clock_values))
    try:
        await clocked_repository.create_run(**fixture_run)
    finally:
        await clocked_repository.close()

    persisted = await postgres.fetchrow(
        "SELECT started_at, completed_at, source_fetched_at FROM analysis_runs"
    )
    assert persisted is not None
    assert persisted["started_at"] == started_at
    assert persisted["completed_at"] == completed_at
    assert persisted["completed_at"] > persisted["started_at"]
    assert persisted["completed_at"] != persisted["source_fetched_at"]


async def test_concurrent_creates_share_one_release_identity(
    database_url: str,
    fixture_run: CreateRunArguments,
    postgres: asyncpg.Connection,
) -> None:
    """Concurrent analysis runs must not duplicate repository, policy, or release rows."""
    await postgres.execute("TRUNCATE TABLE repository_connections CASCADE")
    repositories = [AnalysisRepository(database_url), AnalysisRepository(database_url)]
    try:
        run_ids = await asyncio.gather(
            *(repository.create_run(**fixture_run) for repository in repositories)
        )
    finally:
        await asyncio.gather(*(repository.close() for repository in repositories))

    assert len(set(run_ids)) == 2
    assert await postgres.fetchval("SELECT count(*) FROM repository_connections") == 1
    assert await postgres.fetchval("SELECT count(*) FROM release_policies") == 1
    assert await postgres.fetchval("SELECT count(*) FROM releases") == 1


async def test_human_decision_schema_preserves_fingerprint_and_lineage(
    postgres: asyncpg.Connection,
) -> None:
    """Decision audit rows require fingerprints and retain append-only lineage."""
    columns = await postgres.fetch(
        "SELECT column_name, is_nullable FROM information_schema.columns "
        "WHERE table_name = 'human_decisions'"
    )
    column_nullability = {row["column_name"]: row["is_nullable"] for row in columns}
    assert column_nullability["fingerprint"] == "NO"
    assert column_nullability["supersedes_decision_id"] == "YES"

    constraint = await postgres.fetchrow(
        "SELECT confdeltype FROM pg_constraint "
        "WHERE conrelid = 'human_decisions'::regclass "
        "AND contype = 'f' AND pg_get_constraintdef(oid) "
        "LIKE '%supersedes_decision_id%'"
    )
    assert constraint is not None
    assert constraint["confdeltype"] == b"r"
