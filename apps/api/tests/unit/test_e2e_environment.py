from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from release_intelligence.adapters.fixtures import github_source as fixtures
from release_intelligence.api.dependencies import (
    AuthorizedRepository,
    CurrentUser,
    SessionRecord,
)
from release_intelligence.config import E2ESettings
from release_intelligence.main import create_app
from release_intelligence.ports.github import RepoRef
from release_intelligence.security.crypto import CredentialCipher

NOW = datetime(2026, 8, 16, 20, 0, tzinfo=UTC)


class E2EAuthStore:
    def __init__(self) -> None:
        self.users: dict[str, CurrentUser] = {}
        self.sessions: dict[str, SessionRecord] = {}
        self.repositories: dict[tuple[str, str], AuthorizedRepository] = {}

    async def complete_oauth_login(
        self,
        user: CurrentUser,
        encrypted_credential: str,
        session: SessionRecord,
    ) -> None:
        assert encrypted_credential and "fixture-token" not in encrypted_credential
        self.users[user.id] = user
        self.sessions[session.token_hash] = session

    async def connect_repository(
        self,
        *,
        user_id: str,
        installation_id: int,
        repository_id: str,
        full_name: str,
    ) -> None:
        self.repositories[(user_id, repository_id)] = AuthorizedRepository(
            repository_id=repository_id,
            full_name=full_name,
            installation_id=installation_id,
        )

    async def get_session(
        self, token_hash: str, accessed_at: datetime
    ) -> tuple[CurrentUser, SessionRecord] | None:
        session = self.sessions.get(token_hash)
        if session is None or session.expires_at <= accessed_at:
            return None
        return self.users[session.user_id], session

    async def find_repository_access(
        self, user_id: str, repository_id: str
    ) -> AuthorizedRepository | None:
        direct = self.repositories.get((user_id, repository_id))
        if direct is not None:
            return direct
        return next(
            (
                repository
                for (bound_user, _bound_id), repository in self.repositories.items()
                if bound_user == user_id and repository.full_name == repository_id
            ),
            None,
        )

    async def save_oauth_state(self, *_args: object) -> None:
        raise AssertionError("E2E bootstrap must not use OAuth state")

    async def consume_oauth_state(self, *_args: object) -> bool:
        raise AssertionError("E2E bootstrap must not use OAuth state")

    async def delete_session(self, token_hash: str) -> None:
        self.sessions.pop(token_hash, None)


async def test_e2e_bootstrap_creates_real_session_csrf_and_repository_binding() -> None:
    store = E2EAuthStore()
    app = create_app(
        auth_store=store,
        cipher=CredentialCipher(SecretStr(Fernet.generate_key().decode())),
        clock=lambda: NOW,
        configure_auth=False,
        environment="e2e",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://testserver"
    ) as client:
        bootstrap = await client.post("/api/e2e/bootstrap")
        csrf = await client.get("/api/auth/csrf")
        repository = await client.get(
            "/api/repositories/by-name/floppy522/ai-release-intelligence-demo"
        )

    assert bootstrap.status_code == 201, bootstrap.text
    assert bootstrap.json() == {
        "repository_id": "987654",
        "repository_full_name": "floppy522/ai-release-intelligence-demo",
        "milestone_number": 7,
        "candidate_ref": "release/2026-08-10",
    }
    assert "session=" in bootstrap.headers["set-cookie"]
    assert csrf.status_code == 200
    assert csrf.headers["cache-control"] == "no-store"
    assert repository.status_code == 200
    assert repository.json()["repository_id"] == "987654"
    assert len(store.sessions) == 1
    assert len(store.repositories) == 1


async def test_e2e_bootstrap_is_unavailable_outside_exact_environment() -> None:
    app = create_app(configure_auth=False, environment="production")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://testserver"
    ) as client:
        safe_method = await client.get("/api/e2e/bootstrap")
        unsafe_method = await client.post("/api/e2e/bootstrap")

    assert safe_method.status_code == 404
    assert unsafe_method.status_code == 503
    assert unsafe_method.json() == {"detail": "Authentication unavailable"}


async def test_healthz_is_public_and_reveals_no_configuration() -> None:
    app = create_app(configure_auth=False, environment="production")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://testserver"
    ) as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_e2e_settings_require_only_database_and_encryption_material(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv(
        "ARI_DATABASE_URL",
        "postgresql+asyncpg://release_intelligence:test@postgres/release_intelligence",
    )
    monkeypatch.setenv("ARI_CREDENTIAL_ENCRYPTION_KEY", key)
    monkeypatch.delenv("ARI_GITHUB_PRIVATE_KEY_PEM", raising=False)

    settings = E2ESettings()

    assert settings.database_url.get_secret_value().startswith("postgresql+asyncpg://")
    assert settings.credential_encryption_key.get_secret_value() == key


async def test_fixture_source_exposes_only_the_expected_repository_release() -> None:
    source = fixtures.FixtureGitHubSource(clock=lambda: NOW)
    repository = RepoRef(owner="floppy522", name="ai-release-intelligence-demo")

    milestone = await source.get_milestone(repository, 7)
    candidate_sha = await source.resolve_ref(repository, "release/2026-08-10")
    checks = await source.list_checks_for_ref(repository, candidate_sha)

    assert milestone.number == 7
    assert await source.list_milestone_items(repository, 7) == ()
    assert [(check.name, check.conclusion) for check in checks] == [
        ("blocking-suite", "success"),
        ("advisory-tests", "failure"),
    ]
    assert all(check.head_sha == candidate_sha for check in checks)
    assert all(
        check.url.startswith(
            "https://github.com/floppy522/ai-release-intelligence-demo/runs/"
        )
        for check in checks
    )


async def test_fixture_source_rejects_other_coordinates() -> None:
    source = fixtures.FixtureGitHubSource(clock=lambda: NOW)
    wrong_repository = RepoRef(owner="example", name="private")

    for operation in (
        source.get_milestone(wrong_repository, 7),
        source.resolve_ref(wrong_repository, "release/2026-08-10"),
    ):
        try:
            await operation
        except Exception as error:  # noqa: BLE001 - assert sanitized public failure
            assert "fixture" not in str(error).casefold()
        else:
            raise AssertionError("unexpected fixture coordinate was accepted")
