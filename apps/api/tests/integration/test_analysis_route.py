from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy.exc import SQLAlchemyError

from release_intelligence.api.dependencies import (
    AuthorizedRepository,
    CurrentUser,
    SessionRecord,
)
from release_intelligence.application.analyze_release import (
    AnalysisRequest,
    AnalysisService,
    MissingCandidateRef,
    MissingMilestone,
)
from release_intelligence.domain.models import (
    EvidenceRef,
    ReleaseSnapshot,
    SourceError,
)
from release_intelligence.main import create_app
from release_intelligence.ports.github import GitHubUnauthorized
from release_intelligence.ports.repositories import StoredAnalysisRun
from release_intelligence.security.crypto import token_digest

NOW = datetime(2026, 8, 7, 14, 30, tzinfo=UTC)
REPOSITORY_ID = "987654"
SESSION_TOKEN = "test-session"
CSRF_TOKEN = "test-csrf"


def snapshot(
    *, complete: bool = True, source_errors: tuple[SourceError, ...] = ()
) -> ReleaseSnapshot:
    return ReleaseSnapshot(
        release_name="Milestone 7",
        issue_number="",
        milestone_number=7,
        issue_labels=(),
        linked_pr_numbers=(),
        issue_evidence=EvidenceRef(
            evidence_id="milestone-7",
            source_type="github_milestone",
            source_id="7",
            url="https://github.com/example/release-intelligence/milestone/7",
            fingerprint="github:milestone:7:2026-08-07T14:30:00Z",
        ),
        repository_id=REPOSITORY_ID,
        repository_full_name="example/release-intelligence",
        fetch_started_at=NOW,
        fetched_at=NOW,
        complete=complete,
        source_errors=source_errors,
        candidate_ref="release/2026-08-10",
        candidate_sha="candidate-sha" if complete else "",
    )


class FakeAuthStore:
    allow_repository = True

    async def get_session(
        self, token_hash: str, accessed_at: datetime
    ) -> tuple[CurrentUser, SessionRecord] | None:
        del accessed_at
        if token_hash != token_digest(SESSION_TOKEN):
            return None
        user = CurrentUser(id="github:7", login="octocat")
        return user, SessionRecord(
            user_id=user.id,
            token_hash=token_hash,
            csrf_token_hash=token_digest(CSRF_TOKEN),
            expires_at=NOW + timedelta(hours=1),
        )

    async def find_repository_access(
        self, user_id: str, repository_id: str
    ) -> AuthorizedRepository | None:
        if not self.allow_repository or user_id != "github:7":
            return None
        if repository_id != REPOSITORY_ID:
            return None
        return AuthorizedRepository(
            repository_id=REPOSITORY_ID,
            full_name="example/release-intelligence",
            installation_id=123,
        )


class FakeLoader:
    def __init__(self, result: ReleaseSnapshot | Exception) -> None:
        self.result = result
        self.requests: list[AnalysisRequest] = []

    async def load(self, request: AnalysisRequest) -> ReleaseSnapshot:
        self.requests.append(request)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class MemoryAnalysisRepository:
    def __init__(self, *, failure: bool = False) -> None:
        self.runs: dict[UUID, StoredAnalysisRun] = {}
        self.failure = failure
        self.rolled_back = False

    async def create_run(self, **values: Any) -> UUID:
        if self.failure:
            self.rolled_back = True
            raise SQLAlchemyError("database unavailable")
        run_id = uuid4()
        self.runs[run_id] = StoredAnalysisRun(
            id=run_id,
            snapshot=values["snapshot"],
            findings=values["findings"],
            assessment=values["assessment"],
            policy_version=values["policy_version"],
            source_fetched_at=values["source_fetched_at"],
        )
        return run_id

    async def get_run(self, run_id: UUID) -> StoredAnalysisRun:
        try:
            return self.runs[run_id]
        except KeyError:
            raise KeyError(run_id) from None


def service(
    loader: FakeLoader, repository: MemoryAnalysisRepository
) -> AnalysisService:
    async def loader_factory(request: AnalysisRequest) -> FakeLoader:
        del request
        return loader

    return AnalysisService(
        loader_factory=loader_factory,
        repository=repository,
        clock=lambda: NOW,
    )


@pytest.fixture
def store() -> FakeAuthStore:
    return FakeAuthStore()


async def request_client(
    analysis_service: AnalysisService, store: FakeAuthStore
) -> httpx.AsyncClient:
    app = create_app(
        auth_store=store,
        analysis_service=analysis_service,
        clock=lambda: NOW,
        configure_auth=False,
    )
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://testserver",
        cookies={"session": SESSION_TOKEN},
        headers={"X-CSRF-Token": CSRF_TOKEN},
    )


async def test_post_analysis_returns_persisted_run_and_get_retrieves_it(
    store: FakeAuthStore,
) -> None:
    loader = FakeLoader(snapshot())
    repository = MemoryAnalysisRepository()
    async with await request_client(service(loader, repository), store) as client:
        response = await client.post(
            "/api/analyses",
            json={
                "repository_id": REPOSITORY_ID,
                "milestone_number": 7,
                "candidate_ref": "release/2026-08-10",
            },
        )
        retrieved = await client.get(f"/api/analyses/{response.json()['run_id']}")

    assert response.status_code == 202
    assert UUID(response.json()["run_id"])
    assert retrieved.status_code == 200
    assert retrieved.json()["status"] == "READY"
    assert retrieved.json()["snapshot"]["candidate_sha"] == "candidate-sha"
    assert loader.requests[0].repository.owner == "example"
    assert loader.requests[0].installation_id == 123


async def test_partial_and_rate_limited_snapshots_are_persisted_insufficient(
    store: FakeAuthStore,
) -> None:
    reset_at = NOW + timedelta(minutes=30)
    loader = FakeLoader(
        snapshot(
            complete=False,
            source_errors=(
                SourceError(
                    code="github.rate_limited",
                    message="GitHub rate limit prevented a complete snapshot",
                    reset_at=reset_at,
                ),
            ),
        )
    )
    repository = MemoryAnalysisRepository()
    async with await request_client(service(loader, repository), store) as client:
        created = await client.post(
            "/api/analyses",
            json={
                "repository_id": REPOSITORY_ID,
                "milestone_number": 7,
                "candidate_ref": "release/2026-08-10",
            },
        )
        retrieved = await client.get(f"/api/analyses/{created.json()['run_id']}")

    assert created.status_code == 202
    assert retrieved.json()["status"] == "INSUFFICIENT_DATA"
    error = retrieved.json()["snapshot"]["source_errors"][0]
    assert error["code"] == "github.rate_limited"
    assert error["reset_at"] == reset_at.isoformat().replace("+00:00", "Z")


@pytest.mark.parametrize(
    ("failure", "detail"),
    [
        (MissingMilestone(), "Milestone was not found"),
        (MissingCandidateRef(), "Candidate branch was not found"),
    ],
)
async def test_missing_milestone_or_branch_returns_422(
    store: FakeAuthStore, failure: Exception, detail: str
) -> None:
    async with await request_client(
        service(FakeLoader(failure), MemoryAnalysisRepository()), store
    ) as client:
        response = await client.post(
            "/api/analyses",
            json={
                "repository_id": REPOSITORY_ID,
                "milestone_number": 7,
                "candidate_ref": "release/2026-08-10",
            },
        )

    assert response.status_code == 422
    assert response.json() == {"detail": detail}


async def test_repository_and_github_authorization_failures_return_403(
    store: FakeAuthStore,
) -> None:
    store.allow_repository = False
    async with await request_client(
        service(FakeLoader(snapshot()), MemoryAnalysisRepository()), store
    ) as client:
        repository_denied = await client.post(
            "/api/analyses",
            json={
                "repository_id": REPOSITORY_ID,
                "milestone_number": 7,
                "candidate_ref": "release/2026-08-10",
            },
        )
    store.allow_repository = True
    async with await request_client(
        service(FakeLoader(GitHubUnauthorized()), MemoryAnalysisRepository()), store
    ) as client:
        github_denied = await client.post(
            "/api/analyses",
            json={
                "repository_id": REPOSITORY_ID,
                "milestone_number": 7,
                "candidate_ref": "release/2026-08-10",
            },
        )

    assert repository_denied.status_code == 403
    assert github_denied.status_code == 403


async def test_database_failure_returns_503_after_repository_rollback(
    store: FakeAuthStore,
) -> None:
    repository = MemoryAnalysisRepository(failure=True)
    async with await request_client(
        service(FakeLoader(snapshot()), repository), store
    ) as client:
        response = await client.post(
            "/api/analyses",
            json={
                "repository_id": REPOSITORY_ID,
                "milestone_number": 7,
                "candidate_ref": "release/2026-08-10",
            },
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "Analysis persistence unavailable"}
    assert repository.rolled_back is True


async def test_candidate_ref_must_use_release_date_format(
    store: FakeAuthStore,
) -> None:
    loader = FakeLoader(snapshot())
    async with await request_client(
        service(loader, MemoryAnalysisRepository()), store
    ) as client:
        response = await client.post(
            "/api/analyses",
            json={
                "repository_id": REPOSITORY_ID,
                "milestone_number": 7,
                "candidate_ref": "feature/not-a-release",
            },
        )

    assert response.status_code == 422
    assert loader.requests == []
