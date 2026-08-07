from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict

from release_intelligence.adapters.github.auth import (
    GitHubAuthorizationError,
    GitHubUpstreamError,
)
from release_intelligence.api.dependencies import (
    AuthLifetimes,
    AuthorizedRepository,
    AuthStore,
    CurrentUser,
    CurrentUserDependency,
    OAuthGateway,
    SessionContext,
    SessionRecord,
    get_auth_lifetimes,
    get_auth_store,
    get_cipher,
    get_clock,
    get_oauth_gateway,
    require_repository_access,
    validate_csrf,
)
from release_intelligence.ports.auth import AuthPersistenceError
from release_intelligence.security.crypto import (
    CredentialCipher,
    generate_opaque_token,
    token_digest,
)

router = APIRouter(prefix="/api", tags=["authentication"])

OAUTH_BINDING_COOKIE = "oauth_binding"
OAUTH_CALLBACK_PATH = "/api/auth/github/callback"


class LoginResponse(BaseModel):
    authenticated: bool
    csrf_token: str


class CurrentUserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    login: str


class RepositoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    repository_id: str
    full_name: str
    installation_id: int


@router.get("/auth/github/login")
async def github_login(
    store: Annotated[AuthStore, Depends(get_auth_store)],
    oauth: Annotated[OAuthGateway, Depends(get_oauth_gateway)],
    clock: Annotated[Callable[[], datetime], Depends(get_clock)],
    lifetimes: Annotated[AuthLifetimes, Depends(get_auth_lifetimes)],
) -> RedirectResponse:
    state = generate_opaque_token()
    binding = generate_opaque_token()
    try:
        await store.save_oauth_state(
            token_digest(state),
            token_digest(binding),
            clock() + timedelta(seconds=lifetimes.oauth_state_seconds),
        )
    except AuthPersistenceError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Authentication temporarily unavailable",
        ) from None
    response = RedirectResponse(
        oauth.authorization_url(state), status_code=status.HTTP_307_TEMPORARY_REDIRECT
    )
    response.set_cookie(
        OAUTH_BINDING_COOKIE,
        binding,
        max_age=lifetimes.oauth_state_seconds,
        httponly=True,
        secure=True,
        samesite="lax",
        path=OAUTH_CALLBACK_PATH,
    )
    return response


@router.get("/auth/github/callback", response_model=LoginResponse)
async def github_callback(
    store: Annotated[AuthStore, Depends(get_auth_store)],
    oauth: Annotated[OAuthGateway, Depends(get_oauth_gateway)],
    cipher: Annotated[CredentialCipher, Depends(get_cipher)],
    clock: Annotated[Callable[[], datetime], Depends(get_clock)],
    lifetimes: Annotated[AuthLifetimes, Depends(get_auth_lifetimes)],
    code: Annotated[str | None, Query()] = None,
    state_value: Annotated[str | None, Query(alias="state")] = None,
    oauth_binding: Annotated[
        str | None, Cookie(alias=OAUTH_BINDING_COOKIE)
    ] = None,
) -> JSONResponse:
    if (
        code is None
        or not code
        or len(code) > 1024
        or state_value is None
        or not state_value
        or len(state_value) > 1024
    ):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "GitHub authorization request was invalid"
        )
    now = clock()
    if not oauth_binding:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "OAuth state is invalid or expired"
        )
    try:
        state_consumed = await store.consume_oauth_state(
            token_digest(state_value), token_digest(oauth_binding), now
        )
    except AuthPersistenceError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Authentication temporarily unavailable",
        ) from None
    if not state_consumed:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "OAuth state is invalid or expired"
        )

    try:
        user_token = await oauth.exchange_code(code)
        identity = await oauth.current_user(user_token)
    except GitHubAuthorizationError:
        return _callback_error(
            status.HTTP_400_BAD_REQUEST, "GitHub authorization was invalid"
        )
    except GitHubUpstreamError:
        return _callback_error(
            status.HTTP_502_BAD_GATEWAY, "GitHub authentication unavailable"
        )
    user = CurrentUser(id=identity.user_id, login=identity.login)

    session_token = generate_opaque_token()
    csrf_token = generate_opaque_token()
    try:
        await store.upsert_user_with_credential(user, cipher.encrypt(user_token))
        await store.create_session(
            SessionRecord(
                user_id=user.id,
                token_hash=token_digest(session_token),
                csrf_token_hash=token_digest(csrf_token),
                expires_at=now + timedelta(seconds=lifetimes.session_seconds),
            )
        )
    except AuthPersistenceError:
        return _callback_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Authentication temporarily unavailable",
        )
    response = JSONResponse(
        LoginResponse(authenticated=True, csrf_token=csrf_token).model_dump()
    )
    _clear_oauth_binding(response)
    response.set_cookie(
        "session",
        session_token,
        max_age=lifetimes.session_seconds,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    return response


@router.get("/auth/me", response_model=CurrentUserResponse)
async def current_user(user: CurrentUserDependency) -> CurrentUser:
    return user


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    context: Annotated[SessionContext, Depends(validate_csrf)],
    store: Annotated[AuthStore, Depends(get_auth_store)],
) -> Response:
    try:
        await store.delete_session(context.session.token_hash)
    except AuthPersistenceError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Authentication temporarily unavailable",
        ) from None
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(
        "session", path="/", secure=True, httponly=True, samesite="lax"
    )
    return response


@router.get("/repositories/{owner}/{name}", response_model=RepositoryResponse)
async def repository_details(
    owner: str,
    name: str,
    user: CurrentUserDependency,
    store: Annotated[AuthStore, Depends(get_auth_store)],
) -> AuthorizedRepository:
    return await require_repository_access(
        user_id=user.id,
        repository_id=f"{owner}/{name}",
        store=store,
    )


def _clear_oauth_binding(response: Response) -> None:
    response.delete_cookie(
        OAUTH_BINDING_COOKIE,
        path=OAUTH_CALLBACK_PATH,
        secure=True,
        httponly=True,
        samesite="lax",
    )


def _callback_error(status_code: int, detail: str) -> JSONResponse:
    response = JSONResponse({"detail": detail}, status_code=status_code)
    _clear_oauth_binding(response)
    return response
