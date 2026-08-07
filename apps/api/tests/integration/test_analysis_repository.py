"""PostgreSQL contracts for persisted, immutable analysis runs.

These tests deliberately use ``DATABASE_URL`` and a real PostgreSQL schema.  A
missing URL is an error: SQLite, fake connections, and skipped integration
coverage would not exercise the persistence guarantees this suite protects.
"""

from __future__ import annotations

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
        issue_labels=("code-change", "release-blocker"),
        linked_pr_numbers=(),
        issue_evidence=evidence,
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


async def test_create_run_rolls_back_every_row_when_a_finding_is_invalid(
    repository: AnalysisRepository,
    fixture_run: CreateRunArguments,
    postgres: asyncpg.Connection,
) -> None:
    """Dropping a finding's evidence must leave no partial analysis report."""
    finding = fixture_run["findings"][0]
    invalid_finding = replace(finding, evidence=())
    invalid_assessment = ReadinessAssessment(
        status=ReleaseStatus.NOT_READY,
        findings=(invalid_finding,),
    )
    invalid_run: CreateRunArguments = {
        **fixture_run,
        "findings": (invalid_finding,),
        "assessment": invalid_assessment,
    }

    with pytest.raises(ValueError):
        await repository.create_run(**invalid_run)

    failed_run_states = [
        row["state"] for row in await postgres.fetch("SELECT state FROM analysis_runs")
    ]
    assert failed_run_states in ([], ["FAILED"])
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
    assert stored.findings == fixture_run["findings"]
    assert stored.assessment == fixture_run["assessment"]
    assert stored.policy_version == "2026.08.1"
    assert stored.source_fetched_at == datetime(2026, 8, 7, 14, 30, tzinfo=UTC)
