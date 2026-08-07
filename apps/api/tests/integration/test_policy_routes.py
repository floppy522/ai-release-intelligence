from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from release_intelligence.api.dependencies import (
    AuthorizedRepository,
    CurrentUser,
    SessionRecord,
)
from release_intelligence.domain.policy import ReleasePolicy
from release_intelligence.main import create_app
from release_intelligence.ports.policies import (
    PolicyPersistenceError,
    PolicyRecord,
    PolicyVersionConflictError,
)
from release_intelligence.security.crypto import token_digest

NOW = datetime(2026, 8, 7, 14, 30, tzinfo=UTC)
REPOSITORY_ID = "987654"
SESSION_TOKEN = "test-session"
CSRF_TOKEN = "test-csrf"

POLICY_PAYLOAD = {
    "main_branch": "main",
    "candidate_branch": "release/2026-08-10",
    "milestone_number": 7,
    "code_change_label": "code-change",
    "release_ops_label": "release-ops",
    "blocker_label": "release-blocker",
    "discovered_checks": ["api", "security"],
    "check_categories": {"api": "BLOCKING", "security": "ADVISORY"},
    "previous_milestone_number": 6,
    "previous_release_branch": "release/2026-08-03",
}


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
            full_name="example/release-intelligence",
            installation_id=123,
        )


class MemoryPolicyStore:
    def __init__(self) -> None:
        self.records: list[PolicyRecord] = []
        self.failure = False

    async def get_latest(self, repository_id: str) -> PolicyRecord | None:
        if self.failure:
            raise PolicyPersistenceError()
        matches = [r for r in self.records if r.repository_id == repository_id]
        return matches[-1] if matches else None

    async def create_version(
        self,
        *,
        repository_id: str,
        policy: ReleasePolicy,
        expected_version: int | None,
    ) -> PolicyRecord:
        if self.failure:
            raise PolicyPersistenceError()
        latest = await self.get_latest(repository_id)
        actual = latest.version if latest else None
        if actual != expected_version:
            raise PolicyVersionConflictError()
        record = PolicyRecord(
            repository_id=repository_id,
            version=(actual or 0) + 1,
            policy=policy,
            created_at=NOW,
        )
        self.records.append(record)
        return record


async def request_client(
    store: FakeAuthStore,
    policy_store: MemoryPolicyStore,
    *,
    csrf: bool = True,
) -> httpx.AsyncClient:
    headers = {"X-CSRF-Token": CSRF_TOKEN} if csrf else {}
    app = create_app(
        auth_store=store,
        policy_store=policy_store,
        clock=lambda: NOW,
        configure_auth=False,
    )
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://testserver",
        cookies={"session": SESSION_TOKEN},
        headers=headers,
    )


@pytest.fixture
def auth_store() -> FakeAuthStore:
    return FakeAuthStore()


@pytest.fixture
def policy_store() -> MemoryPolicyStore:
    return MemoryPolicyStore()


async def test_put_versions_policy_and_get_returns_latest_without_overwrite(
    auth_store: FakeAuthStore, policy_store: MemoryPolicyStore
) -> None:
    async with await request_client(auth_store, policy_store) as client:
        first = await client.put(
            f"/api/repositories/{REPOSITORY_ID}/policy",
            json={**POLICY_PAYLOAD, "expected_version": None},
        )
        second = await client.put(
            f"/api/repositories/{REPOSITORY_ID}/policy",
            json={
                **POLICY_PAYLOAD,
                "main_branch": "trunk",
                "expected_version": 1,
            },
        )
        latest = await client.get(f"/api/repositories/{REPOSITORY_ID}/policy")

    assert first.status_code == 200
    assert first.json()["version"] == 1
    assert second.status_code == 200
    assert latest.status_code == 200, latest.text
    assert latest.json()["version"] == 2
    assert latest.json()["policy"]["main_branch"] == "trunk"
    assert [record.policy.main_branch for record in policy_store.records] == [
        "main",
        "trunk",
    ]


async def test_put_rejects_stale_expected_version_without_losing_update(
    auth_store: FakeAuthStore, policy_store: MemoryPolicyStore
) -> None:
    async with await request_client(auth_store, policy_store) as client:
        created = await client.put(
            f"/api/repositories/{REPOSITORY_ID}/policy",
            json={**POLICY_PAYLOAD, "expected_version": None},
        )
        stale = await client.put(
            f"/api/repositories/{REPOSITORY_ID}/policy",
            json={**POLICY_PAYLOAD, "expected_version": None},
        )

    assert created.status_code == 200
    assert stale.status_code == 409
    assert stale.json() == {"detail": "Policy changed; reload before saving"}
    assert len(policy_store.records) == 1


async def test_policy_routes_require_repository_access_and_csrf(
    auth_store: FakeAuthStore, policy_store: MemoryPolicyStore
) -> None:
    auth_store.allow_repository = False
    async with await request_client(auth_store, policy_store) as client:
        denied = await client.get(f"/api/repositories/{REPOSITORY_ID}/policy")
    auth_store.allow_repository = True
    async with await request_client(auth_store, policy_store, csrf=False) as client:
        csrf_denied = await client.put(
            f"/api/repositories/{REPOSITORY_ID}/policy",
            json={**POLICY_PAYLOAD, "expected_version": None},
        )

    assert denied.status_code == 403
    assert csrf_denied.status_code == 403
    assert csrf_denied.json() == {"detail": "CSRF validation failed"}


async def test_put_rejects_unclassified_discovered_check_with_422(
    auth_store: FakeAuthStore, policy_store: MemoryPolicyStore
) -> None:
    async with await request_client(auth_store, policy_store) as client:
        response = await client.put(
            f"/api/repositories/{REPOSITORY_ID}/policy",
            json={
                **POLICY_PAYLOAD,
                "discovered_checks": ["api", "new-security-scan"],
                "check_categories": {"api": "BLOCKING"},
                "expected_version": None,
            },
        )

    assert response.status_code == 422
    assert response.json() == {"detail": "Every discovered check needs a category"}
    assert policy_store.records == []


async def test_get_missing_policy_is_404_and_database_failures_are_503(
    auth_store: FakeAuthStore, policy_store: MemoryPolicyStore
) -> None:
    async with await request_client(auth_store, policy_store) as client:
        missing = await client.get(f"/api/repositories/{REPOSITORY_ID}/policy")
        policy_store.failure = True
        unavailable = await client.get(f"/api/repositories/{REPOSITORY_ID}/policy")

    assert missing.status_code == 404, missing.text
    assert missing.json() == {"detail": "Release policy was not found"}
    assert unavailable.status_code == 503
    assert unavailable.json() == {"detail": "Policy persistence unavailable"}
