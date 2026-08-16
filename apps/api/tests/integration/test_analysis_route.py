from __future__ import annotations

from dataclasses import replace
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
    SnapshotVersion,
    SourceError,
)
from release_intelligence.domain.policy import CheckCategory, ReleasePolicy
from release_intelligence.main import create_app
from release_intelligence.ports.github import (
    GitHubCheck,
    GitHubPartialData,
    GitHubRateLimited,
    GitHubUnauthorized,
    RepoRef,
)
from release_intelligence.ports.policies import PolicyPersistenceError, PolicyRecord
from release_intelligence.ports.repositories import (
    IncompatibleSnapshotError,
    StoredAnalysisRun,
)
from release_intelligence.security.crypto import token_digest

NOW = datetime(2026, 8, 7, 14, 30, tzinfo=UTC)
REPOSITORY_ID = "987654"
SESSION_TOKEN = "test-session"
CSRF_TOKEN = "test-csrf"


def snapshot(
    *,
    complete: bool = True,
    source_errors: tuple[SourceError, ...] = (),
    candidate_sha: str | None = None,
    checks: tuple[GitHubCheck, ...] = (),
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
        snapshot_version=SnapshotVersion.GITHUB_V1,
        repository_id=REPOSITORY_ID,
        repository_full_name="example/release-intelligence",
        fetch_started_at=NOW,
        fetched_at=NOW,
        complete=complete,
        source_errors=source_errors,
        candidate_ref="release/2026-08-10",
        candidate_sha=(candidate_sha or "candidate-sha") if complete else "",
        checks=checks,
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
        self.write_failed = False
        self.incompatible_runs: dict[UUID, str] = {}

    async def create_run(self, **values: Any) -> UUID:
        if self.failure:
            self.write_failed = True
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
        if run_id in self.incompatible_runs:
            raise IncompatibleSnapshotError(self.incompatible_runs[run_id])
        try:
            return self.runs[run_id]
        except KeyError:
            raise KeyError(run_id) from None


class FakePolicyStore:
    def __init__(self, record: PolicyRecord | None, *, failure: bool = False) -> None:
        self.record = record
        self.failure = failure
        self.requested_repository_ids: list[str] = []

    async def get_latest(self, repository_id: str) -> PolicyRecord | None:
        self.requested_repository_ids.append(repository_id)
        if self.failure:
            raise PolicyPersistenceError() from RuntimeError(
                "postgresql://user:secret-password@database"
            )
        return self.record

    async def create_version(
        self,
        *,
        repository_id: str,
        policy: ReleasePolicy,
        expected_version: int | None,
    ) -> PolicyRecord:
        del repository_id, policy, expected_version
        raise AssertionError("analysis must not create policy versions")


def service(
    loader: FakeLoader,
    repository: MemoryAnalysisRepository,
    policy_repository: FakePolicyStore | None = None,
) -> AnalysisService:
    async def loader_factory(request: AnalysisRequest) -> FakeLoader:
        del request
        return loader

    return AnalysisService(
        loader_factory=loader_factory,
        repository=repository,
        policy_repository=policy_repository,
        clock=lambda: NOW,
    )


@pytest.fixture
def store() -> FakeAuthStore:
    return FakeAuthStore()


async def request_client(
    analysis_service: AnalysisService,
    store: FakeAuthStore,
    *,
    clock=lambda: NOW,
) -> httpx.AsyncClient:
    app = create_app(
        auth_store=store,
        analysis_service=analysis_service,
        clock=clock,
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


async def test_analysis_uses_current_configured_policy_for_decision_eligible_run() -> (
    None
):
    candidate_sha = "a" * 40
    check = GitHubCheck(
        source_id="201",
        run_id=201,
        name="security",
        url="https://github.com/example/release-intelligence/runs/201",
        head_sha=candidate_sha,
        status="completed",
        conclusion="failure",
        started_at=NOW,
        completed_at=NOW,
    )
    policy = ReleasePolicy(
        main_branch="main",
        candidate_branch="release/2026-08-10",
        milestone_number=7,
        code_change_label="code-change",
        release_ops_label="release-ops",
        blocker_label="release-blocker",
        check_categories={"security": CheckCategory.ADVISORY},
    )
    policy_store = FakePolicyStore(
        PolicyRecord(
            repository_id=REPOSITORY_ID,
            version=4,
            policy=policy,
            created_at=NOW,
        )
    )
    loader = FakeLoader(snapshot(candidate_sha=candidate_sha, checks=(check,)))
    repository = MemoryAnalysisRepository()

    analysis_service = service(loader, repository, policy_store)
    run_id = await analysis_service.run(
        AnalysisRequest(
            repository_id=REPOSITORY_ID,
            repository=RepoRef(owner="example", name="release-intelligence"),
            installation_id=123,
            milestone_number=7,
            candidate_ref="release/2026-08-10",
        ),
        actor="github:7",
    )

    stored = repository.runs[run_id]
    assert policy_store.requested_repository_ids == [REPOSITORY_ID]
    assert stored.policy_version == "configuration:4"
    assert stored.assessment.status.value == "NEEDS_DECISION"
    assert [finding.rule_id for finding in stored.findings] == [
        "checks.advisory_requires_decision"
    ]


async def test_analysis_loads_policy_selected_previous_release_before_snapshot() -> (
    None
):
    previous_policy = ReleasePolicy(
        main_branch="main",
        candidate_branch="release/2026-08-10",
        milestone_number=7,
        code_change_label="code-change",
        release_ops_label="release-ops",
        blocker_label="release-blocker",
        check_categories={},
        previous_milestone_number=6,
        previous_release_branch="release/2026-08-03",
    )
    policy_store = FakePolicyStore(
        PolicyRecord(
            repository_id=REPOSITORY_ID,
            version=5,
            policy=previous_policy,
            created_at=NOW,
        )
    )
    loader = FakeLoader(
        replace(
            snapshot(candidate_sha="a" * 40),
            previous_milestone_number=6,
            previous_release_branch="release/2026-08-03",
        )
    )
    repository = MemoryAnalysisRepository()

    run_id = await service(loader, repository, policy_store).run(
        AnalysisRequest(
            repository_id=REPOSITORY_ID,
            repository=RepoRef(owner="example", name="release-intelligence"),
            installation_id=123,
            milestone_number=7,
            candidate_ref="release/2026-08-10",
        ),
        actor="github:7",
    )

    assert loader.requests == [
        AnalysisRequest(
            repository_id=REPOSITORY_ID,
            repository=RepoRef(owner="example", name="release-intelligence"),
            installation_id=123,
            milestone_number=7,
            candidate_ref="release/2026-08-10",
            previous_milestone_number=6,
            previous_release_branch="release/2026-08-03",
        )
    ]
    assert repository.runs[run_id].assessment.status.value == "READY"


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


async def test_database_error_http_mapping_returns_503(
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
    assert repository.write_failed is True


async def test_policy_database_error_http_mapping_is_sanitized_503(
    store: FakeAuthStore,
) -> None:
    policy_repository = FakePolicyStore(None, failure=True)
    async with await request_client(
        service(FakeLoader(snapshot()), MemoryAnalysisRepository(), policy_repository),
        store,
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
    assert "secret-password" not in response.text


@pytest.mark.parametrize(
    "bootstrap_failure",
    [GitHubPartialData(), GitHubRateLimited(NOW + timedelta(minutes=30))],
)
async def test_installation_token_availability_is_persisted_insufficient(
    store: FakeAuthStore, bootstrap_failure: Exception
) -> None:
    repository = MemoryAnalysisRepository()

    async def failing_factory(request: AnalysisRequest) -> FakeLoader:
        del request
        raise bootstrap_failure

    analysis_service = AnalysisService(
        loader_factory=failing_factory,
        repository=repository,
        clock=lambda: NOW,
    )
    async with await request_client(analysis_service, store) as client:
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
    if isinstance(bootstrap_failure, GitHubRateLimited):
        assert error["code"] == "github.rate_limited"
        assert error["reset_at"] is not None
    else:
        assert error["code"] == "github.partial_data"


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


async def test_candidate_ref_rejects_impossible_calendar_date(
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
                "candidate_ref": "release/2026-02-31",
            },
        )

    assert response.status_code == 422
    assert loader.requests == []


async def test_get_returns_effective_insufficient_when_stored_ready_is_stale(
    store: FakeAuthStore,
) -> None:
    repository = MemoryAnalysisRepository()
    analysis_service = service(FakeLoader(snapshot()), repository)
    async with await request_client(analysis_service, store) as client:
        created = await client.post(
            "/api/analyses",
            json={
                "repository_id": REPOSITORY_ID,
                "milestone_number": 7,
                "candidate_ref": "release/2026-08-10",
            },
        )
    stored = repository.runs[UUID(created.json()["run_id"])]
    assert stored.assessment.status.value == "READY"

    async with await request_client(
        analysis_service,
        store,
        clock=lambda: NOW + timedelta(minutes=11),
    ) as client:
        retrieved = await client.get(f"/api/analyses/{stored.id}")

    assert retrieved.status_code == 200
    assert retrieved.json()["status"] == "INSUFFICIENT_DATA"
    assert repository.runs[stored.id].assessment.status.value == "READY"


async def test_missing_and_unauthorized_run_ids_are_indistinguishable(
    store: FakeAuthStore,
) -> None:
    repository = MemoryAnalysisRepository()
    analysis_service = service(FakeLoader(snapshot()), repository)
    async with await request_client(analysis_service, store) as client:
        created = await client.post(
            "/api/analyses",
            json={
                "repository_id": REPOSITORY_ID,
                "milestone_number": 7,
                "candidate_ref": "release/2026-08-10",
            },
        )
        missing = await client.get(f"/api/analyses/{uuid4()}")
        store.allow_repository = False
        unauthorized = await client.get(f"/api/analyses/{created.json()['run_id']}")

    assert missing.status_code == unauthorized.status_code == 404
    assert missing.json() == unauthorized.json()


async def test_incompatible_snapshot_authorizes_from_relational_identity_before_409(
    store: FakeAuthStore,
) -> None:
    repository = MemoryAnalysisRepository()
    run_id = uuid4()
    repository.incompatible_runs[run_id] = REPOSITORY_ID
    analysis_service = service(FakeLoader(snapshot()), repository)

    async with await request_client(analysis_service, store) as client:
        authorized = await client.get(f"/api/analyses/{run_id}")
        store.allow_repository = False
        unauthorized = await client.get(f"/api/analyses/{run_id}")
        missing = await client.get(f"/api/analyses/{uuid4()}")

    assert authorized.status_code == 409
    assert authorized.json() == {
        "detail": "Analysis snapshot version is unsupported; refresh or upgrade",
        "status": "INSUFFICIENT_DATA",
    }
    assert unauthorized.status_code == missing.status_code == 404
    assert unauthorized.json() == missing.json()
