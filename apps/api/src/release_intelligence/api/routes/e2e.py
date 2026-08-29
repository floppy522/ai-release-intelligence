from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Annotated, Protocol, cast

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, SecretStr

from release_intelligence.adapters.fixtures.github_source import (
    E2E_CANDIDATE_REF,
    E2E_INSTALLATION_ID,
    E2E_MILESTONE_NUMBER,
    E2E_REPOSITORY,
    E2E_REPOSITORY_ID,
)
from release_intelligence.api.dependencies import (
    AuthLifetimes,
    CurrentUser,
    SessionRecord,
    get_auth_lifetimes,
    get_cipher,
    get_clock,
)
from release_intelligence.ports.auth import AuthPersistenceError
from release_intelligence.security.crypto import (
    CredentialCipher,
    csrf_token_for_session,
    generate_opaque_token,
    token_digest,
)

router = APIRouter(prefix="/api/e2e", tags=["e2e"])
E2E_USER_ID = "e2e:release-lead"


class E2EBootstrapStore(Protocol):
    async def complete_oauth_login(
        self,
        user: CurrentUser,
        encrypted_credential: str,
        session: SessionRecord,
    ) -> None: ...

    async def connect_repository(
        self,
        *,
        user_id: str,
        installation_id: int,
        repository_id: str,
        full_name: str,
    ) -> None: ...


class E2EBootstrapResponse(BaseModel):
    repository_id: str
    repository_full_name: str
    milestone_number: int
    candidate_ref: str


def get_e2e_store(request: Request) -> E2EBootstrapStore:
    store = getattr(request.app.state, "auth_store", None)
    if store is None or not callable(getattr(store, "connect_repository", None)):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "E2E bootstrap unavailable",
        )
    return cast(E2EBootstrapStore, store)


@router.post("/bootstrap", status_code=status.HTTP_201_CREATED)
async def bootstrap(
    store: Annotated[E2EBootstrapStore, Depends(get_e2e_store)],
    cipher: Annotated[CredentialCipher, Depends(get_cipher)],
    clock: Annotated[Callable[[], datetime], Depends(get_clock)],
    lifetimes: Annotated[AuthLifetimes, Depends(get_auth_lifetimes)],
) -> JSONResponse:
    user = CurrentUser(id=E2E_USER_ID, login="release-lead")
    session_token = generate_opaque_token()
    csrf_token = csrf_token_for_session(session_token)
    try:
        await store.complete_oauth_login(
            user,
            cipher.encrypt(SecretStr("e2e-fixture-token")),
            SessionRecord(
                user_id=user.id,
                token_hash=token_digest(session_token),
                csrf_token_hash=token_digest(csrf_token),
                expires_at=clock() + timedelta(seconds=lifetimes.session_seconds),
            ),
        )
        await store.connect_repository(
            user_id=user.id,
            installation_id=E2E_INSTALLATION_ID,
            repository_id=E2E_REPOSITORY_ID,
            full_name=f"{E2E_REPOSITORY.owner}/{E2E_REPOSITORY.name}",
        )
    except AuthPersistenceError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "E2E bootstrap unavailable",
        ) from None

    response = JSONResponse(
        E2EBootstrapResponse(
            repository_id=E2E_REPOSITORY_ID,
            repository_full_name=(f"{E2E_REPOSITORY.owner}/{E2E_REPOSITORY.name}"),
            milestone_number=E2E_MILESTONE_NUMBER,
            candidate_ref=E2E_CANDIDATE_REF,
        ).model_dump(),
        status_code=status.HTTP_201_CREATED,
        headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "Referrer-Policy": "no-referrer",
        },
    )
    response.set_cookie(
        "session",
        session_token,
        max_age=lifetimes.session_seconds,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
    )
    return response
