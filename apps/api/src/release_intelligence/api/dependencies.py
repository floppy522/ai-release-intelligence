from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Protocol, cast

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from pydantic import SecretStr

from release_intelligence.adapters.github.auth import (
    GitHubOAuthIdentity,
)
from release_intelligence.security.crypto import (
    CredentialCipher,
    digest_matches,
    token_digest,
)


@dataclass(frozen=True)
class CurrentUser:
    id: str
    login: str


@dataclass(frozen=True)
class SessionRecord:
    user_id: str
    token_hash: str
    csrf_token_hash: str
    expires_at: datetime


@dataclass(frozen=True)
class SessionContext:
    user: CurrentUser
    session: SessionRecord


@dataclass(frozen=True)
class AuthorizedRepository:
    repository_id: str
    full_name: str
    installation_id: int


class OAuthGateway(Protocol):
    def authorization_url(self, state: str) -> str: ...

    async def exchange_code(self, code: str) -> SecretStr: ...

    async def current_user(self, token: SecretStr) -> GitHubOAuthIdentity: ...


class AuthStore(Protocol):
    async def save_oauth_state(self, state_hash: str, expires_at: datetime) -> None: ...

    async def consume_oauth_state(
        self, state_hash: str, consumed_at: datetime
    ) -> bool: ...

    async def upsert_user_with_credential(
        self, user: CurrentUser, encrypted_credential: str
    ) -> None: ...

    async def create_session(self, session: SessionRecord) -> None: ...

    async def get_session(
        self, token_hash: str, accessed_at: datetime
    ) -> tuple[CurrentUser, SessionRecord] | None: ...

    async def delete_session(self, token_hash: str) -> None: ...

    async def find_repository_access(
        self, user_id: str, repository_id: str
    ) -> AuthorizedRepository | None: ...


def get_auth_store(request: Request) -> AuthStore:
    store = getattr(request.app.state, "auth_store", None)
    if store is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Authentication unavailable"
        )
    return cast(AuthStore, store)


def get_oauth_gateway(request: Request) -> OAuthGateway:
    gateway = getattr(request.app.state, "oauth_gateway", None)
    if gateway is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Authentication unavailable"
        )
    return cast(OAuthGateway, gateway)


def get_cipher(request: Request) -> CredentialCipher:
    cipher = getattr(request.app.state, "credential_cipher", None)
    if cipher is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Authentication unavailable"
        )
    return cast(CredentialCipher, cipher)


def get_clock(request: Request) -> Callable[[], datetime]:
    return cast(Callable[[], datetime], request.app.state.clock)


async def get_session_context(
    request: Request,
    store: Annotated[AuthStore, Depends(get_auth_store)],
    clock: Annotated[Callable[[], datetime], Depends(get_clock)],
    session_token: Annotated[str | None, Cookie(alias="session")] = None,
) -> SessionContext:
    cached = getattr(request.state, "session_context", None)
    if isinstance(cached, SessionContext):
        return cached
    if not session_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
    result = await store.get_session(token_digest(session_token), clock())
    if result is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Session is invalid or expired"
        )
    user, session = result
    return SessionContext(user=user, session=session)


async def get_current_user(
    context: Annotated[SessionContext, Depends(get_session_context)],
) -> CurrentUser:
    return context.user


async def validate_csrf(
    context: Annotated[SessionContext, Depends(get_session_context)],
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> SessionContext:
    if not csrf_token or not digest_matches(
        csrf_token, context.session.csrf_token_hash
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF validation failed")
    return context


async def require_repository_access(
    *,
    user_id: str,
    repository_id: str,
    store: AuthStore,
) -> AuthorizedRepository:
    repository = await store.find_repository_access(user_id, repository_id)
    if repository is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Repository access denied")
    return repository


CurrentUserDependency = Annotated[CurrentUser, Depends(get_current_user)]
