"""PostgreSQL upgrade contract for historical Task 2 decision rows."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import asyncpg
import pytest

API_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_ID = UUID("10000000-0000-0000-0000-000000000001")
RELEASE_ID = UUID("20000000-0000-0000-0000-000000000002")
RUN_ID = UUID("30000000-0000-0000-0000-000000000003")
SNAPSHOT_ID = UUID("40000000-0000-0000-0000-000000000004")
FINDING_ID = UUID("50000000-0000-0000-0000-000000000005")
EVIDENCE_ID = UUID("60000000-0000-0000-0000-000000000006")
DECISION_ID = UUID("70000000-0000-0000-0000-000000000007")
POLICY_ID = UUID("80000000-0000-0000-0000-000000000008")
EXPLANATION_ID = UUID("90000000-0000-0000-0000-000000000009")
PENDING_RUN_ID = UUID("30000000-0000-0000-0000-000000000013")
PENDING_EXPLANATION_ID = UUID("90000000-0000-0000-0000-000000000019")
NOW = datetime(2026, 8, 7, 14, 30, tzinfo=UTC)


def _database_url() -> str:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        raise RuntimeError(
            "DATABASE_URL is required for PostgreSQL decision migration tests"
        )
    if not database_url.startswith(("postgresql://", "postgresql+asyncpg://")):
        raise RuntimeError("DATABASE_URL must use a PostgreSQL URL")
    return database_url


def _asyncpg_url(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _run_alembic(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *arguments],
        cwd=API_ROOT,
        env={**os.environ, "DATABASE_URL": _database_url()},
        capture_output=True,
        text=True,
        check=False,
    )


def _alembic(*arguments: str) -> None:
    result = _run_alembic(*arguments)
    assert result.returncode == 0, result.stdout + result.stderr


async def test_upgrade_from_0002_preserves_legacy_run_assessment() -> None:
    database_url = _database_url()
    _alembic("downgrade", "0002_policy_config")
    postgres = await asyncpg.connect(_asyncpg_url(database_url))
    try:
        await postgres.execute("TRUNCATE TABLE repository_connections CASCADE")
        await postgres.execute(
            "INSERT INTO repository_connections "
            "(id, provider, external_repository_id, full_name) "
            "VALUES ($1, 'github', '987654', 'acme/widgets')",
            REPOSITORY_ID,
        )
        await postgres.execute(
            "INSERT INTO release_policies (id, repository_id, version) "
            "VALUES ($1, $2, 'configuration:1')",
            POLICY_ID,
            REPOSITORY_ID,
        )
        await postgres.execute(
            "INSERT INTO releases "
            "(id, repository_id, github_milestone_number, name) "
            "VALUES ($1, $2, 7, 'Milestone 7')",
            RELEASE_ID,
            REPOSITORY_ID,
        )
        await postgres.execute(
            "INSERT INTO analysis_runs "
            "(id, release_id, policy_version, source_fetched_at, state, "
            "assessment_status, started_at, completed_at) "
            "VALUES ($1, $2, 'configuration:1', $3, 'COMPLETED', "
            "'NOT_READY', $3, $3)",
            RUN_ID,
            RELEASE_ID,
            NOW,
        )
        await postgres.execute(
            "INSERT INTO release_snapshots (id, analysis_run_id, payload) "
            "VALUES ($1, $2, '{}'::jsonb)",
            SNAPSHOT_ID,
            RUN_ID,
        )
        await postgres.execute(
            "INSERT INTO readiness_findings "
            "(id, analysis_run_id, position, rule_id, severity, summary, "
            "required_action) VALUES ($1, $2, 0, "
            "'checks.blocking_not_successful', 'BLOCKING', "
            "'API check failed', 'Repair the API check')",
            FINDING_ID,
            RUN_ID,
        )
        await postgres.execute(
            "INSERT INTO finding_evidence "
            "(id, finding_id, position, evidence_id, source_type, source_id, "
            "url, fingerprint) VALUES ($1, $2, 0, 'github-check-101', "
            "'github_check', '101', 'https://github.com/acme/widgets/runs/101', $3)",
            EVIDENCE_ID,
            FINDING_ID,
            "sha256:" + "a" * 64,
        )
        await postgres.execute(
            "INSERT INTO human_decisions "
            "(id, analysis_run_id, finding_id, fingerprint, decision, reason, "
            "actor_id, decided_at) VALUES ($1, $2, $3, $4, "
            "'RELEASE_BLOCKER', 'Historical review', 'github:7', $5)",
            DECISION_ID,
            RUN_ID,
            FINDING_ID,
            "sha256:" + "a" * 64,
            NOW,
        )
    finally:
        await postgres.close()

    _alembic("upgrade", "head")
    postgres = await asyncpg.connect(_asyncpg_url(database_url))
    try:
        row = await postgres.fetchrow(
            "SELECT decision_sequence, assessment_status, assessment_payload "
            "FROM human_decisions WHERE id = $1",
            DECISION_ID,
        )
    finally:
        await postgres.close()

    assert row is not None
    assert row["decision_sequence"] == 1
    assert row["assessment_status"] == "NOT_READY"
    assert row["assessment_payload"] == [
        {
            "rule_id": "checks.blocking_not_successful",
            "severity": "BLOCKING",
            "summary": "API check failed",
            "required_action": "Repair the API check",
            "evidence": [
                {
                    "evidence_id": "github-check-101",
                    "source_type": "github_check",
                    "source_id": "101",
                    "url": "https://github.com/acme/widgets/runs/101",
                    "fingerprint": "sha256:" + "a" * 64,
                }
            ],
        }
    ]


async def test_upgrade_from_0002_rejects_incomplete_legacy_audit_data() -> None:
    database_url = _database_url()
    _alembic("downgrade", "0002_policy_config")
    postgres = await asyncpg.connect(_asyncpg_url(database_url))
    try:
        await postgres.execute("TRUNCATE TABLE repository_connections CASCADE")
        await postgres.execute(
            "INSERT INTO repository_connections "
            "(id, provider, external_repository_id, full_name) "
            "VALUES ($1, 'github', '987654', 'acme/widgets')",
            REPOSITORY_ID,
        )
        await postgres.execute(
            "INSERT INTO releases "
            "(id, repository_id, github_milestone_number, name) "
            "VALUES ($1, $2, 7, 'Milestone 7')",
            RELEASE_ID,
            REPOSITORY_ID,
        )
        await postgres.execute(
            "INSERT INTO analysis_runs "
            "(id, release_id, policy_version, source_fetched_at, state, "
            "assessment_status, started_at, completed_at) "
            "VALUES ($1, $2, 'default-v1', $3, 'COMPLETED', 'READY', $3, $3)",
            RUN_ID,
            RELEASE_ID,
            NOW,
        )
        await postgres.execute(
            "INSERT INTO human_decisions "
            "(id, analysis_run_id, finding_id, fingerprint, decision, reason, "
            "actor_id, decided_at) VALUES ($1, $2, NULL, $3, "
            "'ACCEPTED_RISK', 'Legacy invalid row', 'github:7', $4)",
            DECISION_ID,
            RUN_ID,
            "sha256:" + "b" * 64,
            NOW,
        )
    finally:
        await postgres.close()

    result = _run_alembic("upgrade", "head")
    try:
        assert result.returncode != 0
        assert "legacy human decision audit data is incomplete" in (
            result.stdout + result.stderr
        )
        postgres = await asyncpg.connect(_asyncpg_url(database_url))
        try:
            assert await postgres.fetchval(
                "SELECT version_num FROM alembic_version"
            ) == ("0002_policy_config")
        finally:
            await postgres.close()
    finally:
        postgres = await asyncpg.connect(_asyncpg_url(database_url))
        try:
            await postgres.execute("TRUNCATE TABLE repository_connections CASCADE")
        finally:
            await postgres.close()
        _alembic("upgrade", "head")


async def test_upgrade_from_0003_preserves_terminal_rows_and_unlocks_pending() -> None:
    database_url = _database_url()
    _alembic("downgrade", "0003_decision_assessments")
    postgres = await asyncpg.connect(_asyncpg_url(database_url))
    try:
        await postgres.execute("TRUNCATE TABLE repository_connections CASCADE")
        await postgres.execute(
            "INSERT INTO repository_connections "
            "(id, provider, external_repository_id, full_name) "
            "VALUES ($1, 'github', '987654', 'acme/widgets')",
            REPOSITORY_ID,
        )
        await postgres.execute(
            "INSERT INTO releases "
            "(id, repository_id, github_milestone_number, name) "
            "VALUES ($1, $2, 7, 'Milestone 7')",
            RELEASE_ID,
            REPOSITORY_ID,
        )
        await postgres.executemany(
            "INSERT INTO analysis_runs "
            "(id, release_id, policy_version, source_fetched_at, state, "
            "assessment_status, started_at, completed_at) "
            "VALUES ($1, $2, 'configuration:1', $3, 'COMPLETED', "
            "'NOT_READY', $3, $3)",
            ((RUN_ID, RELEASE_ID, NOW), (PENDING_RUN_ID, RELEASE_ID, NOW)),
        )
        await postgres.executemany(
            "INSERT INTO ai_explanations (id, analysis_run_id, content) "
            "VALUES ($1, $2, $3)",
            (
                (
                    EXPLANATION_ID,
                    RUN_ID,
                    '{"state":"available","explanation":{},"metadata":{}}',
                ),
                (
                    PENDING_EXPLANATION_ID,
                    PENDING_RUN_ID,
                    '{"state":"pending"}',
                ),
            ),
        )
    finally:
        await postgres.close()

    _alembic("upgrade", "head")
    postgres = await asyncpg.connect(_asyncpg_url(database_url))
    try:
        await postgres.execute(
            'UPDATE ai_explanations SET content = \'{"state":"unavailable"}\' '
            "WHERE id = $1",
            PENDING_EXPLANATION_ID,
        )
        with pytest.raises(asyncpg.PostgresError, match="state transition"):
            await postgres.execute(
                "UPDATE ai_explanations SET content = "
                '\'{"state":"unavailable"}\' WHERE id = $1',
                EXPLANATION_ID,
            )
        with pytest.raises(asyncpg.PostgresError, match="deleted directly"):
            await postgres.execute(
                "DELETE FROM ai_explanations WHERE id = $1", EXPLANATION_ID
            )

        await postgres.execute(
            "DELETE FROM repository_connections WHERE id = $1", REPOSITORY_ID
        )
        assert await postgres.fetchval("SELECT count(*) FROM ai_explanations") == 0
    finally:
        await postgres.close()
