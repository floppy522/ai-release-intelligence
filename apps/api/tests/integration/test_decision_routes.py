from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest

from release_intelligence.api.dependencies import (
    AuthorizedRepository,
    CurrentUser,
    SessionRecord,
)
from release_intelligence.application.decisions import (
    DecisionConflictError,
    DecisionFindingNotFoundError,
    DecisionKind,
    DecisionPersistenceError,
    DecisionResult,
    HumanDecision,
)
from release_intelligence.domain.models import (
    EvidenceRef,
    ReadinessAssessment,
    ReadinessFinding,
    ReleaseSnapshot,
    ReleaseStatus,
    SnapshotVersion,
)
from release_intelligence.main import create_app
from release_intelligence.ports.repositories import StoredAnalysisRun
from release_intelligence.security.crypto import token_digest

NOW = datetime(2026, 8, 7, 14, 30, tzinfo=UTC)
RUN_ID = UUID("10000000-0000-0000-0000-000000000001")
FINDING_ID = UUID("20000000-0000-0000-0000-000000000002")
DECISION_ID = UUID("30000000-0000-0000-0000-000000000003")
PREVIOUS_ID = UUID("40000000-0000-0000-0000-000000000004")
REPOSITORY_ID = "987654"
SESSION_TOKEN = "test-session"
CSRF_TOKEN = "test-csrf"
FINGERPRINT = "sha256:" + "a" * 64


def snapshot() -> ReleaseSnapshot:
    return ReleaseSnapshot(
        release_name="Milestone 7",
        issue_number="7",
        milestone_number=7,
        issue_labels=(),
        linked_pr_numbers=(),
        issue_evidence=EvidenceRef(
            "milestone-7",
            "github_milestone",
            "7",
            "https://github.com/acme/widgets/milestone/7",
            "github:milestone:7",
        ),
        snapshot_version=SnapshotVersion.GITHUB_V1,
        repository_id=REPOSITORY_ID,
        repository_full_name="acme/widgets",
        fetch_started_at=NOW,
        fetched_at=NOW,
        candidate_ref="release/2026-08-10",
        candidate_sha="a" * 40,
    )


FINDING = ReadinessFinding(
    rule_id="checks.advisory_requires_decision",
    severity="DECISION_REQUIRED",
    summary="Advisory check security is not successful",
    required_action="Accept the risk or block the release",
    evidence=(
        EvidenceRef(
            "github-check-201",
            "github_check",
            "201",
            "https://github.com/acme/widgets/runs/201",
            FINGERPRINT,
        ),
    ),
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
            full_name="acme/widgets",
            installation_id=123,
        )


class FakeAnalysisService:
    missing = False

    async def get(self, run_id: UUID) -> StoredAnalysisRun:
        if self.missing or run_id != RUN_ID:
            raise KeyError(run_id)
        return StoredAnalysisRun(
            id=RUN_ID,
            snapshot=snapshot(),
            findings=(FINDING,),
            assessment=ReadinessAssessment(
                status=ReleaseStatus.NEEDS_DECISION, findings=(FINDING,)
            ),
            policy_version="configuration:1",
            source_fetched_at=NOW,
        )


class FakeDecisionService:
    failure: Exception | None = None

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def record_for_run(self, **values: object) -> DecisionResult:
        self.calls.append(values)
        if self.failure is not None:
            raise self.failure
        decision = HumanDecision(
            id=DECISION_ID,
            analysis_run_id=RUN_ID,
            finding_id=FINDING_ID,
            fingerprint=FINGERPRINT,
            kind=DecisionKind.ACCEPTED_RISK,
            reason="Reviewed by the release lead",
            actor_id="github:7",
            decided_at=NOW,
            supersedes_decision_id=PREVIOUS_ID,
        )
        return DecisionResult(
            decision=decision,
            assessment=ReadinessAssessment(status=ReleaseStatus.READY, findings=()),
        )


@pytest.fixture
def auth_store() -> FakeAuthStore:
    return FakeAuthStore()


@pytest.fixture
def decision_service() -> FakeDecisionService:
    return FakeDecisionService()


async def client(
    auth_store: FakeAuthStore,
    decision_service: FakeDecisionService,
) -> httpx.AsyncClient:
    app = create_app(
        auth_store=auth_store,
        analysis_service=FakeAnalysisService(),  # type: ignore[arg-type]
        decision_service=decision_service,  # type: ignore[arg-type]
        clock=lambda: NOW,
        configure_auth=False,
    )
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://testserver",
        cookies={"session": SESSION_TOKEN},
        headers={"X-CSRF-Token": CSRF_TOKEN},
    )


async def test_records_actor_owned_decision_and_returns_reassessment(
    auth_store: FakeAuthStore, decision_service: FakeDecisionService
) -> None:
    async with await client(auth_store, decision_service) as request:
        response = await request.post(
            f"/api/analyses/{RUN_ID}/decisions",
            json={
                "finding_id": str(FINDING_ID),
                "fingerprint": FINGERPRINT,
                "decision": "ACCEPTED_RISK",
                "reason": "Reviewed by the release lead",
            },
        )

    assert response.status_code == 201
    assert response.json()["decision"] == "ACCEPTED_RISK"
    assert response.json()["actor_id"] == "github:7"
    assert response.json()["supersedes_decision_id"] == str(PREVIOUS_ID)
    assert response.json()["assessment"]["status"] == "READY"
    assert decision_service.calls == [
        {
            "run_id": RUN_ID,
            "finding_id": FINDING_ID,
            "fingerprint": FINGERPRINT,
            "kind": DecisionKind.ACCEPTED_RISK,
            "reason": "Reviewed by the release lead",
            "actor": "github:7",
            "authorized_repository_id": REPOSITORY_ID,
        }
    ]


async def test_repository_access_is_checked_before_decision_write(
    auth_store: FakeAuthStore, decision_service: FakeDecisionService
) -> None:
    auth_store.allow_repository = False
    async with await client(auth_store, decision_service) as request:
        response = await request.post(
            f"/api/analyses/{RUN_ID}/decisions",
            json={
                "finding_id": str(FINDING_ID),
                "fingerprint": FINGERPRINT,
                "decision": "RELEASE_BLOCKER",
                "reason": "Security review is incomplete",
            },
        )

    assert response.status_code == 404
    assert response.json() == {"detail": "Analysis was not found"}
    assert decision_service.calls == []


async def test_csrf_is_required_for_decision_write(
    auth_store: FakeAuthStore, decision_service: FakeDecisionService
) -> None:
    async with await client(auth_store, decision_service) as request:
        response = await request.post(
            f"/api/analyses/{RUN_ID}/decisions",
            headers={"X-CSRF-Token": "wrong"},
            json={
                "finding_id": str(FINDING_ID),
                "fingerprint": FINGERPRINT,
                "decision": "RELEASE_BLOCKER",
                "reason": "Security review is incomplete",
            },
        )

    assert response.status_code == 403
    assert decision_service.calls == []


@pytest.mark.parametrize(
    ("failure", "status_code", "detail"),
    [
        (
            DecisionFindingNotFoundError(),
            422,
            "Finding is not eligible for a decision",
        ),
        (
            DecisionConflictError(),
            409,
            "Analysis changed; refresh before deciding",
        ),
        (
            DecisionPersistenceError("postgresql://secret@database"),
            503,
            "Decision persistence unavailable",
        ),
    ],
)
async def test_decision_failures_are_sanitized_and_consistently_mapped(
    auth_store: FakeAuthStore,
    decision_service: FakeDecisionService,
    failure: Exception,
    status_code: int,
    detail: str,
) -> None:
    decision_service.failure = failure
    async with await client(auth_store, decision_service) as request:
        response = await request.post(
            f"/api/analyses/{RUN_ID}/decisions",
            json={
                "finding_id": str(FINDING_ID),
                "fingerprint": FINGERPRINT,
                "decision": "ACCEPTED_RISK",
                "reason": "Reviewed",
            },
        )

    assert response.status_code == status_code
    assert response.json() == {"detail": detail}
    assert "postgresql" not in response.text


async def test_blank_reason_is_rejected_before_decision_write(
    auth_store: FakeAuthStore, decision_service: FakeDecisionService
) -> None:
    async with await client(auth_store, decision_service) as request:
        response = await request.post(
            f"/api/analyses/{RUN_ID}/decisions",
            json={
                "finding_id": str(FINDING_ID),
                "fingerprint": FINGERPRINT,
                "decision": "ACCEPTED_RISK",
                "reason": "   ",
            },
        )

    assert response.status_code == 422
    assert decision_service.calls == []


async def test_missing_run_is_404_and_does_not_write(
    auth_store: FakeAuthStore, decision_service: FakeDecisionService
) -> None:
    async with await client(auth_store, decision_service) as request:
        response = await request.post(
            f"/api/analyses/{uuid4()}/decisions",
            json={
                "finding_id": str(FINDING_ID),
                "fingerprint": FINGERPRINT,
                "decision": "ACCEPTED_RISK",
                "reason": "Reviewed",
            },
        )

    assert response.status_code == 404
    assert decision_service.calls == []
