from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict

from release_intelligence.api.dependencies import (
    AuthorizedRepository,
    AuthStore,
    CurrentUser,
    CurrentUserDependency,
    OAuthGateway,
    SessionContext,
    SessionRecord,
    get_auth_store,
    get_cipher,
    get_clock,
    get_oauth_gateway,
    require_repository_access,
    validate_csrf,
)
from release_intelligence.security.crypto import (
    CredentialCipher,
    generate_opaque_token,
    token_digest,
)

router = APIRouter(prefix="/api", tags=["authentication"])

SESSION_TTL = timedelta(hours=8)
OAUTH_STATE_TTL = timedelta(minutes=10)


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
) -> RedirectResponse:
    state = generate_opaque_token()
    await store.save_oauth_state(token_digest(state), clock() + OAUTH_STATE_TTL)
    return RedirectResponse(
        oauth.authorization_url(state), status_code=status.HTTP_307_TEMPORARY_REDIRECT
    )


@router.get("/auth/github/callback", response_model=LoginResponse)
async def github_callback(
    code: Annotated[str, Query(min_length=1, max_length=1024)],
    state_value: Annotated[str, Query(alias="state", min_length=1, max_length=1024)],
    store: Annotated[AuthStore, Depends(get_auth_store)],
    oauth: Annotated[OAuthGateway, Depends(get_oauth_gateway)],
    cipher: Annotated[CredentialCipher, Depends(get_cipher)],
    clock: Annotated[Callable[[], datetime], Depends(get_clock)],
) -> JSONResponse:
    now = clock()
    if not await store.consume_oauth_state(token_digest(state_value), now):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "OAuth state is invalid or expired"
        )

    user_token = await oauth.exchange_code(code)
    identity = await oauth.current_user(user_token)
    user = CurrentUser(id=identity.user_id, login=identity.login)
    await store.upsert_user_with_credential(user, cipher.encrypt(user_token))

    session_token = generate_opaque_token()
    csrf_token = generate_opaque_token()
    await store.create_session(
        SessionRecord(
            user_id=user.id,
            token_hash=token_digest(session_token),
            csrf_token_hash=token_digest(csrf_token),
            expires_at=now + SESSION_TTL,
        )
    )
    response = JSONResponse(
        LoginResponse(authenticated=True, csrf_token=csrf_token).model_dump()
    )
    response.set_cookie(
        "session",
        session_token,
        max_age=int(SESSION_TTL.total_seconds()),
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
    await store.delete_session(context.session.token_hash)
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
