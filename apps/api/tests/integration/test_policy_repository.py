"""Real PostgreSQL contracts for append-only release-policy versions."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest

from release_intelligence.adapters.persistence.policies import PolicyRepository
from release_intelligence.domain.policy import CheckCategory, ReleasePolicy
from release_intelligence.ports.policies import PolicyVersionConflictError

API_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_ID = "987654"


def _require_database_url() -> str:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        raise RuntimeError(
            "DATABASE_URL is required for PostgreSQL policy integration tests"
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
async def postgres(database_url: str) -> AsyncIterator[asyncpg.Connection]:
    connection = await asyncpg.connect(_asyncpg_url(database_url))
    await connection.execute("TRUNCATE TABLE repository_connections CASCADE")
    await connection.execute(
        "INSERT INTO repository_connections "
        "(id, provider, external_repository_id, full_name) "
        "VALUES ('00000000-0000-0000-0000-000000000001', "
        "'github', $1, 'example/release-intelligence')",
        REPOSITORY_ID,
    )
    try:
        yield connection
    finally:
        await connection.close()


@pytest.fixture
async def repository(database_url: str) -> AsyncIterator[PolicyRepository]:
    store = PolicyRepository(database_url)
    try:
        yield store
    finally:
        await store.close()


@pytest.fixture
def policy() -> ReleasePolicy:
    return ReleasePolicy(
        main_branch="main",
        candidate_branch="release/2026-08-10",
        milestone_number=7,
        code_change_label="code-change",
        release_ops_label="release-ops",
        blocker_label="release-blocker",
        check_categories={
            "api": CheckCategory.BLOCKING,
            "security": CheckCategory.ADVISORY,
            "legacy": CheckCategory.IGNORED,
        },
        previous_milestone_number=6,
        previous_release_branch="release/2026-08-03",
    )


async def test_policy_versions_round_trip_without_overwriting_history(
    repository: PolicyRepository,
    postgres: asyncpg.Connection,
    policy: ReleasePolicy,
) -> None:
    first = await repository.create_version(
        repository_id=REPOSITORY_ID,
        policy=policy,
        expected_version=None,
    )
    updated = policy.model_copy(update={"main_branch": "trunk"})
    second = await repository.create_version(
        repository_id=REPOSITORY_ID,
        policy=updated,
        expected_version=1,
    )

    latest = await repository.get_latest(REPOSITORY_ID)
    assert first.version == 1
    assert second.version == 2
    assert latest is not None
    assert latest.policy == updated
    assert await postgres.fetchval(
        "SELECT count(*) FROM release_policies WHERE policy_payload IS NOT NULL"
    ) == 2
    assert await postgres.fetchval(
        "SELECT policy_payload->>'main_branch' FROM release_policies "
        "WHERE configuration_version = 1"
    ) == "main"


async def test_policy_rows_are_immutable_in_database(
    repository: PolicyRepository,
    postgres: asyncpg.Connection,
    policy: ReleasePolicy,
) -> None:
    await repository.create_version(
        repository_id=REPOSITORY_ID,
        policy=policy,
        expected_version=None,
    )

    with pytest.raises(asyncpg.PostgresError, match="immutable policy records"):
        await postgres.execute(
            "UPDATE release_policies SET policy_payload = '{}'::jsonb "
            "WHERE configuration_version = 1"
        )


async def test_concurrent_stale_writers_cannot_lose_policy_update(
    database_url: str,
    postgres: asyncpg.Connection,
    policy: ReleasePolicy,
) -> None:
    stores = [PolicyRepository(database_url), PolicyRepository(database_url)]
    try:
        results = await asyncio.gather(
            *(
                store.create_version(
                    repository_id=REPOSITORY_ID,
                    policy=policy,
                    expected_version=None,
                )
                for store in stores
            ),
            return_exceptions=True,
        )
    finally:
        await asyncio.gather(*(store.close() for store in stores))

    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, PolicyVersionConflictError) for result in results) == 1
    assert await postgres.fetchval(
        "SELECT count(*) FROM release_policies WHERE policy_payload IS NOT NULL"
    ) == 1
