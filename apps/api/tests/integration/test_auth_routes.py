from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Protocol

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from release_intelligence.adapters.github.auth import GitHubOAuthIdentity
from release_intelligence.api.dependencies import (
    AuthorizedRepository,
    CurrentUser,
    SessionRecord,
)
from release_intelligence.main import create_app
from release_intelligence.security.crypto import CredentialCipher, token_digest


class Clock(Protocol):
    def __call__(self) -> datetime: ...


class FakeOAuthGateway:
    def __init__(self) -> None:
        self.exchanged_codes: list[str] = []

    def authorization_url(self, state: str) -> str:
        return f"https://github.com/login/oauth/authorize?client_id=test&state={state}"

    async def exchange_code(self, code: str) -> SecretStr:
        self.exchanged_codes.append(code)
        return SecretStr("gho_long-lived-user-token")

    async def current_user(self, token: SecretStr) -> GitHubOAuthIdentity:
        assert token.get_secret_value() == "gho_long-lived-user-token"
        return GitHubOAuthIdentity(user_id="github:7", login="octocat")


class FakeAuthStore:
    def __init__(self) -> None:
        self.oauth_states: dict[str, datetime] = {}
        self.credentials: dict[str, str] = {}
        self.users: dict[str, CurrentUser] = {}
        self.sessions: dict[str, SessionRecord] = {}
        self.repository_access: dict[tuple[str, str], AuthorizedRepository] = {}

    async def save_oauth_state(self, state_hash: str, expires_at: datetime) -> None:
        self.oauth_states[state_hash] = expires_at

    async def consume_oauth_state(self, state_hash: str, consumed_at: datetime) -> bool:
        expires_at = self.oauth_states.pop(state_hash, None)
        return expires_at is not None and expires_at > consumed_at

    async def upsert_user_with_credential(
        self, user: CurrentUser, encrypted_credential: str
    ) -> None:
        self.users[user.id] = user
        self.credentials[user.id] = encrypted_credential

    async def create_session(self, session: SessionRecord) -> None:
        self.sessions[session.token_hash] = session

    async def get_session(
        self, token_hash: str, accessed_at: datetime
    ) -> tuple[CurrentUser, SessionRecord] | None:
        session = self.sessions.get(token_hash)
        if session is None or session.expires_at <= accessed_at:
            return None
        return self.users[session.user_id], session

    async def delete_session(self, token_hash: str) -> None:
        self.sessions.pop(token_hash, None)

    async def find_repository_access(
        self, user_id: str, repository_id: str
    ) -> AuthorizedRepository | None:
        return self.repository_access.get((user_id, repository_id))


@pytest.fixture
def clock() -> Clock:
    return lambda: datetime(2026, 8, 7, 16, 0, tzinfo=UTC)


@pytest.fixture
def store() -> FakeAuthStore:
    return FakeAuthStore()


@pytest.fixture
def oauth() -> FakeOAuthGateway:
    return FakeOAuthGateway()


@pytest.fixture
def cipher() -> CredentialCipher:
    return CredentialCipher(SecretStr(Fernet.generate_key().decode()))


@pytest.fixture
async def client(
    store: FakeAuthStore,
    oauth: FakeOAuthGateway,
    cipher: CredentialCipher,
    clock: Clock,
) -> httpx.AsyncClient:
    app = create_app(auth_store=store, oauth_gateway=oauth, cipher=cipher, clock=clock)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://testserver"
    ) as test_client:
        yield test_client


async def _login(client: httpx.AsyncClient) -> tuple[str, str]:
    start = await client.get("/api/auth/github/login", follow_redirects=False)
    assert start.status_code == 307
    state = httpx.URL(start.headers["location"]).params["state"]
    callback = await client.get(
        "/api/auth/github/callback", params={"code": "oauth-code", "state": state}
    )
    assert callback.status_code == 200
    return callback.json()["csrf_token"], callback.headers["set-cookie"]


async def test_oauth_callback_rejects_missing_mismatched_and_replayed_state(
    client: httpx.AsyncClient,
) -> None:
    missing = await client.get(
        "/api/auth/github/callback", params={"code": "oauth-code", "state": "unknown"}
    )
    assert missing.status_code == 400

    start = await client.get("/api/auth/github/login", follow_redirects=False)
    state = httpx.URL(start.headers["location"]).params["state"]
    accepted = await client.get(
        "/api/auth/github/callback", params={"code": "oauth-code", "state": state}
    )
    replayed = await client.get(
        "/api/auth/github/callback", params={"code": "oauth-code", "state": state}
    )

    assert accepted.status_code == 200
    assert replayed.status_code == 400


async def test_callback_encrypts_long_lived_credential_and_sets_opaque_secure_session(
    client: httpx.AsyncClient,
    store: FakeAuthStore,
    cipher: CredentialCipher,
) -> None:
    csrf_token, set_cookie = await _login(client)

    encrypted = store.credentials["github:7"]
    assert "gho_long-lived-user-token" not in encrypted
    assert cipher.decrypt(encrypted).get_secret_value() == "gho_long-lived-user-token"
    assert csrf_token
    assert "session=" in set_cookie
    assert "gho_long-lived-user-token" not in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "SameSite=lax" in set_cookie
    assert "Path=/" in set_cookie
    assert len(store.sessions) == 1


async def test_current_user_comes_from_server_side_session(
    client: httpx.AsyncClient,
) -> None:
    await _login(client)

    response = await client.get("/api/auth/me")

    assert response.status_code == 200
    assert response.json() == {"id": "github:7", "login": "octocat"}


async def test_unsafe_method_requires_csrf_token_bound_to_session(
    client: httpx.AsyncClient,
) -> None:
    csrf_token, _ = await _login(client)

    missing = await client.post("/api/auth/logout")
    mismatched = await client.post(
        "/api/auth/logout", headers={"X-CSRF-Token": "wrong"}
    )
    accepted = await client.post(
        "/api/auth/logout", headers={"X-CSRF-Token": csrf_token}
    )

    assert missing.status_code == 403
    assert mismatched.status_code == 403
    assert accepted.status_code == 204


async def test_csrf_guard_covers_every_unsafe_http_method(
    client: httpx.AsyncClient,
) -> None:
    csrf_token, _ = await _login(client)

    for method in ("POST", "PUT", "PATCH", "DELETE"):
        rejected = await client.request(method, "/api/not-a-route")
        passed_guard = await client.request(
            method,
            "/api/not-a-route",
            headers={"X-CSRF-Token": csrf_token},
        )

        assert rejected.status_code == 403
        assert passed_guard.status_code == 404


async def test_repository_access_binds_user_installation_and_repository(
    client: httpx.AsyncClient,
    store: FakeAuthStore,
) -> None:
    await _login(client)
    store.repository_access[("github:7", "example/allowed")] = AuthorizedRepository(
        repository_id="example/allowed",
        full_name="example/allowed",
        installation_id=123,
    )
    store.repository_access[("github:99", "other-owner/private-repo")] = (
        AuthorizedRepository(
            repository_id="other-owner/private-repo",
            full_name="other-owner/private-repo",
            installation_id=999,
        )
    )

    allowed = await client.get("/api/repositories/example/allowed")
    other_installation = await client.get("/api/repositories/other-owner/private-repo")

    assert allowed.status_code == 200
    assert allowed.json()["installation_id"] == 123
    assert other_installation.status_code == 403


def test_deployment_key_hashing_contract_does_not_store_session_or_csrf_tokens() -> (
    None
):
    session_token = "session-opaque-token"
    csrf_token = "csrf-random-token"

    assert (
        token_digest(session_token)
        == hashlib.sha256(session_token.encode()).hexdigest()
    )
    assert token_digest(csrf_token) == hashlib.sha256(csrf_token.encode()).hexdigest()
    assert session_token not in token_digest(session_token)
    assert csrf_token not in token_digest(csrf_token)
