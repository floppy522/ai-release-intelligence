from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from release_intelligence.api.dependencies import (
    AuthStore,
    OAuthGateway,
    SessionContext,
)
from release_intelligence.api.routes.auth import router as auth_router
from release_intelligence.api.schemas import AssessmentResponse
from release_intelligence.application.analyze_release import assess_fixture_release
from release_intelligence.domain.models import ReadinessAssessment
from release_intelligence.security.crypto import (
    CredentialCipher,
    digest_matches,
    token_digest,
)

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def create_app(
    *,
    auth_store: AuthStore | None = None,
    oauth_gateway: OAuthGateway | None = None,
    cipher: CredentialCipher | None = None,
    clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    application = FastAPI(title="AI Release Intelligence")
    application.state.auth_store = auth_store
    application.state.oauth_gateway = oauth_gateway
    application.state.credential_cipher = cipher
    application.state.clock = clock or (lambda: datetime.now(UTC))
    application.include_router(auth_router)

    @application.middleware("http")
    async def enforce_csrf(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.method not in UNSAFE_METHODS:
            return await call_next(request)
        store = cast(AuthStore | None, request.app.state.auth_store)
        if store is None:
            return JSONResponse(
                {"detail": "Authentication unavailable"},
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        session_token = request.cookies.get("session")
        if not session_token:
            return JSONResponse(
                {"detail": "Authentication required"},
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        session_result = await store.get_session(
            token_digest(session_token), request.app.state.clock()
        )
        if session_result is None:
            return JSONResponse(
                {"detail": "Session is invalid or expired"},
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        user, session = session_result
        csrf_token = request.headers.get("X-CSRF-Token")
        if not csrf_token or not digest_matches(csrf_token, session.csrf_token_hash):
            return JSONResponse(
                {"detail": "CSRF validation failed"},
                status_code=status.HTTP_403_FORBIDDEN,
            )
        request.state.session_context = SessionContext(user=user, session=session)
        return await call_next(request)

    @application.get("/api/demo/analysis", response_model=AssessmentResponse)
    def get_demo_analysis() -> ReadinessAssessment:
        return assess_fixture_release()

    return application


app = create_app()
