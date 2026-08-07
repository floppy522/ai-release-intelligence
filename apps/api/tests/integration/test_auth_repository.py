from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import pytest

from release_intelligence.adapters.persistence.auth import AuthRepository
from release_intelligence.api.dependencies import CurrentUser, SessionRecord

API_ROOT = Path(__file__).resolve().parents[2]


def _database_url() -> str:
    value = os.environ.get("DATABASE_URL")
    if value is None:
        raise RuntimeError(
            "DATABASE_URL is required for PostgreSQL auth integration tests"
        )
    return value


def _asyncpg_url(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


@pytest.fixture(scope="session", autouse=True)
def migrated_auth_database() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=API_ROOT,
        env={**os.environ, "DATABASE_URL": _database_url()},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture
async def auth_repository() -> AsyncIterator[AuthRepository]:
    repository = AuthRepository(_database_url())
    try:
        yield repository
    finally:
        await repository.close()


@pytest.fixture
async def postgres() -> AsyncIterator[asyncpg.Connection]:
    connection = await asyncpg.connect(_asyncpg_url(_database_url()))
    await connection.execute("TRUNCATE TABLE users RESTART IDENTITY CASCADE")
    await connection.execute(
        "TRUNCATE TABLE repository_connections RESTART IDENTITY CASCADE"
    )
    try:
        yield connection
    finally:
        await connection.close()


async def test_oauth_state_is_single_use_and_expiry_is_enforced(
    auth_repository: AuthRepository,
    postgres: asyncpg.Connection,
) -> None:
    del postgres
    now = datetime(2026, 8, 7, 16, 0, tzinfo=UTC)
    await auth_repository.save_oauth_state("state-hash", now + timedelta(minutes=10))

    assert await auth_repository.consume_oauth_state("state-hash", now) is True
    assert await auth_repository.consume_oauth_state("state-hash", now) is False

    await auth_repository.save_oauth_state("expired-state", now)
    assert await auth_repository.consume_oauth_state("expired-state", now) is False


async def test_credentials_and_sessions_are_server_side_and_repository_access_is_bound(
    auth_repository: AuthRepository,
    postgres: asyncpg.Connection,
) -> None:
    now = datetime(2026, 8, 7, 16, 0, tzinfo=UTC)
    user = CurrentUser(id="github:7", login="octocat")
    await auth_repository.upsert_user_with_credential(user, "fernet-ciphertext")
    await auth_repository.create_session(
        SessionRecord(
            user_id=user.id,
            token_hash="session-hash",
            csrf_token_hash="csrf-hash",
            expires_at=now + timedelta(hours=8),
        )
    )
    await auth_repository.connect_repository(
        user_id=user.id,
        installation_id=123,
        repository_id="9001",
        full_name="example/allowed",
    )

    loaded = await auth_repository.get_session("session-hash", now)
    allowed_by_id = await auth_repository.find_repository_access(user.id, "9001")
    allowed_by_name = await auth_repository.find_repository_access(
        user.id, "example/allowed"
    )
    denied = await auth_repository.find_repository_access("github:99", "9001")

    assert loaded is not None
    assert loaded[0] == user
    assert loaded[1].csrf_token_hash == "csrf-hash"
    assert allowed_by_id == allowed_by_name
    assert allowed_by_id is not None
    assert allowed_by_id.installation_id == 123
    assert denied is None
    assert (
        await postgres.fetchval(
            "SELECT encrypted_token FROM encrypted_user_credentials"
        )
        == "fernet-ciphertext"
    )
    assert (
        await postgres.fetchval("SELECT token_hash FROM web_sessions") == "session-hash"
    )
    assert (
        await postgres.fetchval(
            "SELECT count(*) FROM web_sessions WHERE token_hash = 'raw'"
        )
        == 0
    )
