import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Protocol, cast

import httpx
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from openai import AsyncOpenAI
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from release_intelligence.adapters.ai.openai_provider import (
    DEFAULT_TIMEOUT_SECONDS,
    OpenAIClient,
    OpenAIExplanationProvider,
)
from release_intelligence.adapters.fixtures.github_source import FixtureGitHubSource
from release_intelligence.adapters.github.auth import (
    GitHubAppTokenProvider,
    GitHubOAuthGateway,
)
from release_intelligence.adapters.github.client import GitHubRestClient
from release_intelligence.adapters.persistence.auth import AuthRepository
from release_intelligence.adapters.persistence.policies import PolicyRepository
from release_intelligence.adapters.persistence.repositories import AnalysisRepository
from release_intelligence.api.dependencies import (
    AuthStore,
    OAuthGateway,
    SessionContext,
)
from release_intelligence.api.routes.auth import router as auth_router
from release_intelligence.api.routes.decisions import router as decisions_router
from release_intelligence.api.routes.e2e import router as e2e_router
from release_intelligence.api.routes.explanations import router as explanations_router
from release_intelligence.api.routes.releases import router as releases_router
from release_intelligence.api.routes.repositories import router as repositories_router
from release_intelligence.api.schemas import AssessmentResponse
from release_intelligence.application.analyze_release import (
    AnalysisRequest,
    AnalysisService,
    GitHubReleaseLoader,
    ReleaseLoader,
    assess_fixture_release,
)
from release_intelligence.application.decisions import DecisionService, DecisionStore
from release_intelligence.application.explanations import ExplanationService
from release_intelligence.config import AppSettings, E2ESettings
from release_intelligence.domain.models import ReadinessAssessment
from release_intelligence.ports.ai import AIExplanationStore
from release_intelligence.ports.auth import AuthPersistenceError
from release_intelligence.ports.github import GitHubHttpClient
from release_intelligence.ports.policies import PolicyRepositoryPort
from release_intelligence.ports.repositories import AnalysisRepositoryPort
from release_intelligence.security.crypto import (
    CredentialCipher,
    digest_matches,
    token_digest,
)
from release_intelligence.security.logging import (
    install_access_log_redaction,
    install_application_log_redaction,
)

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
DEFAULT_SESSION_TTL_SECONDS = 8 * 60 * 60
DEFAULT_OAUTH_STATE_TTL_SECONDS = 10 * 60
ALLOWED_ENVIRONMENTS = frozenset({"production", "development", "test", "e2e"})


class ManagedAuthStore(AuthStore, Protocol):
    async def close(self) -> None: ...


class ManagedGitHubHttpClient(GitHubHttpClient, Protocol):
    async def aclose(self) -> None: ...


class ManagedAnalysisRepository(AnalysisRepositoryPort, AIExplanationStore, Protocol):
    pass


class ManagedPolicyRepository(PolicyRepositoryPort, Protocol):
    async def close(self) -> None: ...


class ManagedOpenAIClient(OpenAIClient, Protocol):
    async def close(self) -> None: ...


AuthRepositoryFactory = Callable[[str], ManagedAuthStore]
HttpClientFactory = Callable[[], ManagedGitHubHttpClient]
AnalysisRepositoryFactory = Callable[
    [str, Callable[[], datetime]], ManagedAnalysisRepository
]
PolicyRepositoryFactory = Callable[[str], ManagedPolicyRepository]
OpenAIClientFactory = Callable[[str], ManagedOpenAIClient]


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


def _analysis_repository(
    database_url: str, clock: Callable[[], datetime]
) -> ManagedAnalysisRepository:
    return AnalysisRepository(database_url, clock=clock)


def _policy_repository(database_url: str) -> ManagedPolicyRepository:
    return PolicyRepository(database_url)


def _openai_client(api_key: str) -> ManagedOpenAIClient:
    return cast(
        ManagedOpenAIClient,
        AsyncOpenAI(
            api_key=api_key,
            max_retries=0,
            timeout=DEFAULT_TIMEOUT_SECONDS,
        ),
    )


def _deployment_environment(value: str | None) -> str:
    candidate = (
        value if value is not None else os.environ.get("ENVIRONMENT", "production")
    )
    if candidate not in ALLOWED_ENVIRONMENTS:
        raise ValueError("ENVIRONMENT is invalid")
    return candidate


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
    decision_service: DecisionService | None = None,
    analysis_repository_factory: AnalysisRepositoryFactory = _analysis_repository,
    policy_store: PolicyRepositoryPort | None = None,
    policy_repository_factory: PolicyRepositoryFactory = _policy_repository,
    explanation_service: ExplanationService | None = None,
    openai_client_factory: OpenAIClientFactory = _openai_client,
    environment: str | None = None,
) -> FastAPI:
    install_application_log_redaction()
    effective_clock = clock or (lambda: datetime.now(UTC))
    deployment_environment = _deployment_environment(environment)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        store = auth_store
        gateway = oauth_gateway
        credential_cipher = cipher
        owned_store: ManagedAuthStore | None = None
        owned_client: ManagedGitHubHttpClient | None = None
        owned_analysis_repository: ManagedAnalysisRepository | None = None
        owned_policy_repository: ManagedPolicyRepository | None = None
        owned_openai_client: ManagedOpenAIClient | None = None
        configuration = settings if deployment_environment != "e2e" else None
        configured_analysis_service = analysis_service
        configured_decision_service = decision_service
        configured_policy_store = policy_store
        configured_explanation_service = explanation_service
        try:
            install_access_log_redaction()
            production_auth = configure_auth
            if configure_auth and deployment_environment == "e2e":
                e2e_settings_loader = cast(Callable[[], E2ESettings], E2ESettings)
                e2e_configuration = e2e_settings_loader()
                application.state.session_ttl_seconds = (
                    e2e_configuration.session_ttl_seconds
                )
                if store is None:
                    owned_store = auth_repository_factory(
                        e2e_configuration.database_url.get_secret_value()
                    )
                    store = owned_store
                if credential_cipher is None:
                    credential_cipher = CredentialCipher(
                        e2e_configuration.credential_encryption_key
                    )
                if configured_policy_store is None:
                    owned_policy_repository = policy_repository_factory(
                        e2e_configuration.database_url.get_secret_value()
                    )
                    configured_policy_store = owned_policy_repository
                if configured_analysis_service is None:
                    owned_analysis_repository = analysis_repository_factory(
                        e2e_configuration.database_url.get_secret_value(),
                        effective_clock,
                    )

                    async def fixture_loader_factory(
                        analysis_request: AnalysisRequest,
                    ) -> ReleaseLoader:
                        del analysis_request
                        return GitHubReleaseLoader(
                            FixtureGitHubSource(clock=effective_clock),
                            clock=effective_clock,
                        )

                    configured_analysis_service = AnalysisService(
                        loader_factory=fixture_loader_factory,
                        repository=owned_analysis_repository,
                        policy_repository=configured_policy_store,
                        clock=effective_clock,
                    )
                if configured_decision_service is None and owned_analysis_repository:
                    configured_decision_service = DecisionService(
                        clock=effective_clock,
                        store=cast(DecisionStore, owned_analysis_repository),
                    )
                production_auth = False
            if production_auth and (
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
                if configured_policy_store is None:
                    owned_policy_repository = policy_repository_factory(
                        configuration.database_url.get_secret_value()
                    )
                    configured_policy_store = owned_policy_repository
                if configured_analysis_service is None:
                    owned_analysis_repository = analysis_repository_factory(
                        configuration.database_url.get_secret_value(),
                        effective_clock,
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
                        policy_repository=configured_policy_store,
                        clock=effective_clock,
                    )
                if configured_decision_service is None and owned_analysis_repository:
                    configured_decision_service = DecisionService(
                        clock=effective_clock,
                        store=cast(DecisionStore, owned_analysis_repository),
                    )
                if (
                    configured_explanation_service is None
                    and configuration.openai_api_key is not None
                ):
                    input_price = configuration.openai_input_cost_per_million
                    output_price = configuration.openai_output_cost_per_million
                    if input_price is None or output_price is None:
                        raise ValueError("AI token prices are required")
                    owned_openai_client = openai_client_factory(
                        configuration.openai_api_key.get_secret_value()
                    )
                    configured_explanation_service = ExplanationService(
                        OpenAIExplanationProvider(
                            client=owned_openai_client,
                            model=configuration.openai_model,
                            input_cost_per_million=input_price,
                            output_cost_per_million=output_price,
                        ),
                        store=(
                            owned_analysis_repository
                            if owned_analysis_repository is not None
                            else None
                        ),
                    )
            application.state.auth_store = store
            application.state.oauth_gateway = gateway
            application.state.credential_cipher = credential_cipher
            application.state.analysis_service = configured_analysis_service
            application.state.decision_service = configured_decision_service
            application.state.policy_store = configured_policy_store
            application.state.explanation_service = configured_explanation_service
            yield
        finally:
            try:
                try:
                    try:
                        if owned_openai_client is not None:
                            await owned_openai_client.close()
                    finally:
                        if owned_policy_repository is not None:
                            await owned_policy_repository.close()
                finally:
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
    application.state.decision_service = decision_service
    application.state.policy_store = policy_store
    application.state.explanation_service = explanation_service
    application.state.environment = deployment_environment
    application.include_router(repositories_router)
    application.include_router(auth_router)
    application.include_router(releases_router)
    application.include_router(decisions_router)
    application.include_router(explanations_router)
    if deployment_environment == "e2e":
        application.include_router(e2e_router)

    @application.middleware("http")
    async def enforce_csrf(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.method not in UNSAFE_METHODS:
            return await call_next(request)
        if deployment_environment == "e2e" and request.url.path == "/api/e2e/bootstrap":
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

    @application.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return application


app = create_app()
