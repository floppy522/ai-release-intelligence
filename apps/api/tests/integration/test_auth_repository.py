from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import httpx
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from release_intelligence.adapters.persistence.auth import AuthRepository
from release_intelligence.api.dependencies import CurrentUser, SessionRecord
from release_intelligence.config import AppSettings
from release_intelligence.main import create_app
from release_intelligence.ports.auth import AuthPersistenceError
from release_intelligence.security.crypto import token_digest

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
    await connection.execute("TRUNCATE TABLE oauth_states RESTART IDENTITY CASCADE")
    await connection.execute("TRUNCATE TABLE users RESTART IDENTITY CASCADE")
    await connection.execute(
        "TRUNCATE TABLE repository_connections RESTART IDENTITY CASCADE"
    )
    await connection.execute(
        "TRUNCATE TABLE github_installations RESTART IDENTITY CASCADE"
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
    await auth_repository.save_oauth_state(
        "state-hash", "binding-hash", now + timedelta(minutes=10)
    )

    assert (
        await auth_repository.consume_oauth_state(
            "state-hash", "wrong-binding", now
        )
        is False
    )
    assert (
        await auth_repository.consume_oauth_state(
            "state-hash", "binding-hash", now
        )
        is True
    )
    assert (
        await auth_repository.consume_oauth_state(
            "state-hash", "binding-hash", now
        )
        is False
    )

    await auth_repository.save_oauth_state("expired-state", "binding-hash", now)
    assert (
        await auth_repository.consume_oauth_state(
            "expired-state", "binding-hash", now
        )
        is False
    )


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


async def test_deleting_installation_cascades_repository_connection(
    auth_repository: AuthRepository,
    postgres: asyncpg.Connection,
) -> None:
    user = CurrentUser(id="github:7", login="octocat")
    await auth_repository.upsert_user_with_credential(user, "fernet-ciphertext")
    await auth_repository.connect_repository(
        user_id=user.id,
        installation_id=123,
        repository_id="9001",
        full_name="example/allowed",
    )

    disconnected = await auth_repository.disconnect_installation(
        user_id=user.id, installation_id=123
    )

    assert disconnected is True
    assert await postgres.fetchval("SELECT count(*) FROM github_installations") == 0
    assert await postgres.fetchval("SELECT count(*) FROM repository_connections") == 0
    assert (
        await postgres.fetchval(
            "SELECT count(*) FROM repository_connections WHERE installation_id IS NULL"
        )
        == 0
    )


async def test_oauth_completion_rolls_back_user_credential_and_session_atomically(
    auth_repository: AuthRepository,
    postgres: asyncpg.Connection,
) -> None:
    now = datetime(2026, 8, 7, 16, 0, tzinfo=UTC)
    existing_user = CurrentUser(id="github:7", login="octocat")
    await auth_repository.upsert_user_with_credential(
        existing_user, "existing-ciphertext"
    )
    await postgres.execute(
        """
        CREATE FUNCTION reject_test_web_session_insert() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'test session insert failure';
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER reject_test_web_session_insert
        BEFORE INSERT ON web_sessions
        FOR EACH ROW EXECUTE FUNCTION reject_test_web_session_insert();
        """
    )
    try:
        with pytest.raises(AuthPersistenceError):
            await auth_repository.complete_oauth_login(
                existing_user,
                "replacement-ciphertext",
                SessionRecord(
                    user_id=existing_user.id,
                    token_hash="existing-user-session",
                    csrf_token_hash="csrf-hash",
                    expires_at=now + timedelta(hours=1),
                ),
            )
        with pytest.raises(AuthPersistenceError):
            await auth_repository.complete_oauth_login(
                CurrentUser(id="github:99", login="new-user"),
                "new-user-ciphertext",
                SessionRecord(
                    user_id="github:99",
                    token_hash="new-user-session",
                    csrf_token_hash="csrf-hash",
                    expires_at=now + timedelta(hours=1),
                ),
            )
    finally:
        await postgres.execute(
            "DROP TRIGGER IF EXISTS reject_test_web_session_insert ON web_sessions;"
            "DROP FUNCTION IF EXISTS reject_test_web_session_insert();"
        )

    assert await postgres.fetchval("SELECT count(*) FROM users") == 1
    assert await postgres.fetchval(
        "SELECT encrypted_token FROM encrypted_user_credentials"
    ) == "existing-ciphertext"
    assert await postgres.fetchval("SELECT count(*) FROM web_sessions") == 0


async def test_production_lifespan_wires_real_postgresql_auth_repository(
    postgres: asyncpg.Connection,
) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    settings = AppSettings(
        database_url=_database_url(),
        credential_encryption_key=Fernet.generate_key().decode(),
        github_app_id="4242",
        github_private_key_pem=private_key_pem,
        github_client_id="client-id",
        github_client_secret="client-secret",
        session_ttl_seconds=3600,
        oauth_state_ttl_seconds=120,
    )
    app = create_app(
        settings=settings,
        clock=lambda: datetime(2026, 8, 7, 16, 0, tzinfo=UTC),
    )

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://testserver"
        ) as client:
            response = await client.get(
                "/api/auth/github/login", follow_redirects=False
            )

        assert response.status_code == 307
        state = httpx.URL(response.headers["location"]).params["state"]
        binding = response.cookies["oauth_binding"]
        stored = await postgres.fetchrow(
            "SELECT state_hash, binding_hash, expires_at FROM oauth_states"
        )
        assert stored is not None
        assert len(stored["state_hash"]) == 64
        assert len(stored["binding_hash"]) == 64
        assert stored["state_hash"] == token_digest(state)
        assert stored["binding_hash"] == token_digest(binding)
        assert state not in (stored["state_hash"], stored["binding_hash"])
        assert binding not in (stored["state_hash"], stored["binding_hash"])
        assert stored["expires_at"] == datetime(
            2026, 8, 7, 16, 2, tzinfo=UTC
        )
