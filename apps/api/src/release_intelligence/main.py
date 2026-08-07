from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Protocol, cast

import httpx
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from release_intelligence.adapters.github.auth import (
    GitHubAppTokenProvider,
    GitHubOAuthGateway,
)
from release_intelligence.adapters.github.client import GitHubRestClient
from release_intelligence.adapters.persistence.auth import AuthRepository
from release_intelligence.adapters.persistence.repositories import AnalysisRepository
from release_intelligence.api.dependencies import (
    AuthStore,
    OAuthGateway,
    SessionContext,
)
from release_intelligence.api.routes.auth import router as auth_router
from release_intelligence.api.routes.releases import router as releases_router
from release_intelligence.api.schemas import AssessmentResponse
from release_intelligence.application.analyze_release import (
    AnalysisRequest,
    AnalysisService,
    GitHubReleaseLoader,
    ReleaseLoader,
    assess_fixture_release,
)
from release_intelligence.config import AppSettings
from release_intelligence.domain.models import ReadinessAssessment
from release_intelligence.ports.auth import AuthPersistenceError
from release_intelligence.ports.github import GitHubHttpClient
from release_intelligence.security.crypto import (
    CredentialCipher,
    digest_matches,
    token_digest,
)
from release_intelligence.security.logging import install_access_log_redaction

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
DEFAULT_SESSION_TTL_SECONDS = 8 * 60 * 60
DEFAULT_OAUTH_STATE_TTL_SECONDS = 10 * 60


class ManagedAuthStore(AuthStore, Protocol):
    async def close(self) -> None: ...


class ManagedGitHubHttpClient(GitHubHttpClient, Protocol):
    async def aclose(self) -> None: ...


AuthRepositoryFactory = Callable[[str], ManagedAuthStore]
HttpClientFactory = Callable[[], ManagedGitHubHttpClient]


def _auth_repository(database_url: str) -> ManagedAuthStore:
    return AuthRepository(database_url)


def _http_client() -> ManagedGitHubHttpClient:
    return cast(
        ManagedGitHubHttpClient,
        httpx.AsyncClient(
            base_url="https://api.github.com",
            timeout=httpx.Timeout(10.0),
        ),
    )


def create_app(
    *,
    auth_store: AuthStore | None = None,
    oauth_gateway: OAuthGateway | None = None,
    cipher: CredentialCipher | None = None,
    clock: Callable[[], datetime] | None = None,
    settings: AppSettings | None = None,
    auth_repository_factory: AuthRepositoryFactory = _auth_repository,
    http_client_factory: HttpClientFactory = _http_client,
    session_ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS,
    oauth_state_ttl_seconds: int = DEFAULT_OAUTH_STATE_TTL_SECONDS,
    configure_auth: bool = True,
    analysis_service: AnalysisService | None = None,
) -> FastAPI:
    effective_clock = clock or (lambda: datetime.now(UTC))

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        store = auth_store
        gateway = oauth_gateway
        credential_cipher = cipher
        owned_store: ManagedAuthStore | None = None
        owned_client: ManagedGitHubHttpClient | None = None
        owned_analysis_repository: AnalysisRepository | None = None
        configuration = settings
        configured_analysis_service = analysis_service
        try:
            install_access_log_redaction()
            if configure_auth and (
                store is None or gateway is None or credential_cipher is None
            ):
                settings_loader = cast(Callable[[], AppSettings], AppSettings)
                configuration = configuration or settings_loader()
            if configuration is not None:
                application.state.session_ttl_seconds = (
                    configuration.session_ttl_seconds
                )
                application.state.oauth_state_ttl_seconds = (
                    configuration.oauth_state_ttl_seconds
                )
                owned_client = http_client_factory()
                if store is None:
                    owned_store = auth_repository_factory(
                        configuration.database_url.get_secret_value()
                    )
                    store = owned_store
                if gateway is None:
                    gateway = GitHubOAuthGateway(
                        client_id=configuration.github_client_id,
                        client_secret=configuration.github_client_secret,
                        client=owned_client,
                    )
                if credential_cipher is None:
                    credential_cipher = CredentialCipher(
                        configuration.credential_encryption_key
                    )
                token_provider = GitHubAppTokenProvider(
                    app_id=configuration.github_app_id,
                    private_key=configuration.github_private_key_pem,
                    client=owned_client,
                    clock=effective_clock,
                )
                application.state.github_app_token_provider = token_provider
                if configured_analysis_service is None:
                    owned_analysis_repository = AnalysisRepository(
                        configuration.database_url.get_secret_value(),
                        clock=effective_clock,
                    )

                    async def loader_factory(
                        analysis_request: AnalysisRequest,
                    ) -> ReleaseLoader:
                        token = await token_provider.installation_token(
                            analysis_request.installation_id
                        )
                        source = GitHubRestClient(
                            token=token,
                            client=cast(httpx.AsyncClient, owned_client),
                        )
                        return GitHubReleaseLoader(source, clock=effective_clock)

                    configured_analysis_service = AnalysisService(
                        loader_factory=loader_factory,
                        repository=owned_analysis_repository,
                        clock=effective_clock,
                    )
            application.state.auth_store = store
            application.state.oauth_gateway = gateway
            application.state.credential_cipher = credential_cipher
            application.state.analysis_service = configured_analysis_service
            yield
        finally:
            try:
                if owned_analysis_repository is not None:
                    await owned_analysis_repository.close()
            finally:
                try:
                    if owned_client is not None:
                        await owned_client.aclose()
                finally:
                    if owned_store is not None:
                        await owned_store.close()

    application = FastAPI(title="AI Release Intelligence", lifespan=lifespan)
    application.state.auth_store = auth_store
    application.state.oauth_gateway = oauth_gateway
    application.state.credential_cipher = cipher
    application.state.github_app_token_provider = None
    application.state.clock = effective_clock
    application.state.session_ttl_seconds = session_ttl_seconds
    application.state.oauth_state_ttl_seconds = oauth_state_ttl_seconds
    application.state.analysis_service = analysis_service
    application.include_router(auth_router)
    application.include_router(releases_router)

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
        try:
            session_result = await store.get_session(
                token_digest(session_token), request.app.state.clock()
            )
        except AuthPersistenceError:
            return JSONResponse(
                {"detail": "Authentication temporarily unavailable"},
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
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
