from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import httpx
import pytest

from release_intelligence.api.dependencies import (
    AuthorizedRepository,
    CurrentUser,
    SessionRecord,
)
from release_intelligence.application.explanations import (
    ExplanationService,
    ExplanationValidator,
    build_explanation_input,
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
from release_intelligence.ports.ai import (
    AIExplanation,
    AIExplanationUnavailable,
    ExplanationAction,
    ExplanationGroup,
    ExplanationInput,
    ExplanationMetadata,
)
from release_intelligence.ports.repositories import (
    StoredAnalysisRun,
    StoredFindingMetadata,
)
from release_intelligence.security.crypto import token_digest

NOW = datetime(2026, 8, 7, 14, 30, tzinfo=UTC)
RUN_ID = UUID("20000000-0000-0000-0000-000000000001")
FINDING_ID = UUID("10000000-0000-0000-0000-000000000001")
REPOSITORY_ID = "987654"
SESSION_TOKEN = "test-session"
CSRF_TOKEN = "test-csrf"


def stored_run() -> StoredAnalysisRun:
    evidence = EvidenceRef(
        evidence_id="evidence-1",
        source_type="github_check",
        source_id="1",
        url="https://github.com/acme/widgets/actions/runs/1",
        fingerprint="sha256:" + "1" * 64,
    )
    finding = ReadinessFinding(
        rule_id="checks.blocking",
        severity="BLOCKING",
        summary="Blocking check failed",
        required_action="Resolve blocking check",
        evidence=(evidence,),
    )
    assessment = ReadinessAssessment(
        status=ReleaseStatus.NOT_READY,
        findings=(finding,),
    )
    snapshot = ReleaseSnapshot(
        release_name="Milestone 7",
        issue_number="",
        milestone_number=7,
        issue_labels=(),
        linked_pr_numbers=(),
        issue_evidence=evidence,
        snapshot_version=SnapshotVersion.GITHUB_V1,
        repository_id=REPOSITORY_ID,
        repository_full_name="acme/widgets",
        fetch_started_at=NOW,
        fetched_at=NOW,
        candidate_ref="release/2026-08-10",
        candidate_sha="a" * 40,
    )
    return StoredAnalysisRun(
        id=RUN_ID,
        snapshot=snapshot,
        findings=(finding,),
        assessment=assessment,
        policy_version="configuration:1",
        source_fetched_at=NOW,
        finding_metadata=(
            StoredFindingMetadata(finding_id=FINDING_ID, finding=finding),
        ),
    )


def explanation() -> AIExplanation:
    value = AIExplanation(
        summary="The blocking check must be resolved.",
        groups=(
            ExplanationGroup(
                title="Blocking checks",
                explanation="The deterministic report marks the check as blocking.",
                severity="BLOCKING",
                finding_ids=(str(FINDING_ID),),
                evidence_ids=("evidence-1",),
            ),
        ),
        actions=(
            ExplanationAction(
                action="Resolve blocking check",
                finding_ids=(str(FINDING_ID),),
                evidence_ids=("evidence-1",),
            ),
        ),
        limitations=("Only supplied deterministic facts were used.",),
        confidence="HIGH",
        finding_ids=(str(FINDING_ID),),
        evidence_ids=("evidence-1",),
    )
    value.attach_metadata(
        ExplanationMetadata(
            model="gpt-5.6-2026-08-01",
            latency_seconds=Decimal("0.250000"),
            input_tokens=1_000,
            output_tokens=500,
            cost=Decimal("0.007500"),
        )
    )
    return value


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
        if (
            not self.allow_repository
            or user_id != "github:7"
            or repository_id != REPOSITORY_ID
        ):
            return None
        return AuthorizedRepository(
            repository_id=REPOSITORY_ID,
            full_name="acme/widgets",
            installation_id=123,
        )


class FakeAnalysisService:
    def __init__(self, run: StoredAnalysisRun) -> None:
        self.run = run

    async def get(self, run_id: UUID) -> StoredAnalysisRun:
        if run_id != self.run.id:
            raise KeyError(run_id)
        return self.run


class FakeProvider:
    def __init__(self, result: AIExplanation | Exception) -> None:
        self.result = result
        self.inputs: list[ExplanationInput] = []

    async def explain(self, input: ExplanationInput) -> AIExplanation:
        self.inputs.append(input)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class MemoryExplanationStore:
    def __init__(self) -> None:
        self.content: dict[UUID, str] = {}

    async def load_explanation(self, run_id: UUID) -> str | None:
        return self.content.get(run_id)

    async def reserve_explanation(self, run_id: UUID) -> bool:
        if run_id in self.content:
            return False
        self.content[run_id] = '{"state":"pending"}'
        return True

    async def complete_explanation(self, run_id: UUID, content: str) -> None:
        assert self.content[run_id] == '{"state":"pending"}'
        self.content[run_id] = content

    async def fail_explanation(self, run_id: UUID) -> None:
        self.content[run_id] = '{"state":"unavailable"}'


async def client(
    *,
    auth_store: FakeAuthStore | None = None,
    provider: FakeProvider | None = None,
    explanation_store: MemoryExplanationStore | None = None,
) -> httpx.AsyncClient:
    app = create_app(
        auth_store=auth_store or FakeAuthStore(),
        analysis_service=FakeAnalysisService(stored_run()),  # type: ignore[arg-type]
        explanation_service=(
            ExplanationService(provider, store=explanation_store)
            if provider is not None
            else None
        ),
        clock=lambda: NOW,
        configure_auth=False,
    )
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://testserver",
        cookies={"session": SESSION_TOKEN},
        headers={"X-CSRF-Token": CSRF_TOKEN},
    )


async def test_explanation_route_returns_validated_content_and_metadata() -> None:
    provider = FakeProvider(explanation())
    before = deepcopy(stored_run().assessment)
    expected = ExplanationValidator(build_explanation_input(stored_run())).validate(
        explanation()
    )
    async with await client(provider=provider) as request:
        response = await request.post(f"/api/analyses/{RUN_ID}/explanation")
        repeated = await request.post(f"/api/analyses/{RUN_ID}/explanation")

    assert response.status_code == 200
    assert response.json() == {
        "state": "available",
        "explanation": expected.model_dump(mode="json"),
        "metadata": expected.metadata.model_dump(mode="json"),
    }
    assert provider.inputs[0].deterministic_status == "NOT_READY"
    assert repeated.json() == response.json()
    assert len(provider.inputs) == 1
    assert stored_run().assessment == before


@pytest.mark.parametrize(
    "failure",
    [
        AIExplanationUnavailable(),
        TimeoutError("postgresql://user:secret@db/openai-api-key"),
        ValueError("raw provider refusal with secret-api-key"),
    ],
)
async def test_provider_failures_return_safe_unavailable(failure: Exception) -> None:
    provider = FakeProvider(failure)
    async with await client(provider=provider) as request:
        response = await request.post(f"/api/analyses/{RUN_ID}/explanation")
        repeated = await request.post(f"/api/analyses/{RUN_ID}/explanation")

    assert response.status_code == 200
    assert response.json() == {"state": "unavailable"}
    assert "secret" not in response.text
    assert "provider" not in response.text
    assert repeated.json() == {"state": "unavailable"}
    assert len(provider.inputs) == 1


async def test_disabled_ai_returns_unavailable_without_breaking_startup() -> None:
    async with await client(provider=None) as request:
        response = await request.post(f"/api/analyses/{RUN_ID}/explanation")

    assert response.status_code == 200
    assert response.json() == {"state": "unavailable"}


async def test_explanation_route_requires_csrf() -> None:
    async with await client(provider=FakeProvider(explanation())) as request:
        response = await request.post(
            f"/api/analyses/{RUN_ID}/explanation", headers={"X-CSRF-Token": "wrong"}
        )

    assert response.status_code == 403


async def test_explanation_route_hides_unauthorized_run_without_provider_call() -> None:
    store = FakeAuthStore()
    store.allow_repository = False
    provider = FakeProvider(explanation())
    async with await client(auth_store=store, provider=provider) as request:
        response = await request.post(f"/api/analyses/{RUN_ID}/explanation")

    assert response.status_code == 404
    assert provider.inputs == []


async def test_shared_store_prevents_a_second_service_instance_provider_call() -> None:
    store = MemoryExplanationStore()
    first_provider = FakeProvider(explanation())
    async with await client(
        provider=first_provider, explanation_store=store
    ) as request:
        first = await request.post(f"/api/analyses/{RUN_ID}/explanation")
    second_provider = FakeProvider(explanation())
    async with await client(
        provider=second_provider, explanation_store=store
    ) as request:
        second = await request.post(f"/api/analyses/{RUN_ID}/explanation")

    assert first.json() == second.json()
    assert len(first_provider.inputs) == 1
    assert second_provider.inputs == []
