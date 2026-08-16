from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime

import pytest
from fastapi import HTTPException

from release_intelligence.api.dependencies import (
    AuthorizedRepository,
    AuthStore,
    CurrentUser,
    SessionRecord,
    require_repository_access,
)
from release_intelligence.api.routes.explanations import (
    _require_run_access as require_explanation_run_access,
)
from release_intelligence.api.routes.releases import (
    _require_run_access as require_analysis_run_access,
)


class RepositoryStore:
    def __init__(self) -> None:
        self.allowed = AuthorizedRepository(
            repository_id="77",
            full_name="acme/widgets",
            installation_id=9001,
        )
        self.lookups: list[tuple[str, str]] = []

    async def find_repository_access(
        self, user_id: str, repository_id: str
    ) -> AuthorizedRepository | None:
        self.lookups.append((user_id, repository_id))
        if (user_id, repository_id) in {
            ("user-1", "77"),
            ("user-1", "acme/widgets"),
        }:
            return self.allowed
        return None

    async def save_oauth_state(
        self, state_hash: str, binding_hash: str, expires_at: datetime
    ) -> None:
        raise AssertionError("unexpected auth write")

    async def consume_oauth_state(
        self, state_hash: str, binding_hash: str, consumed_at: datetime
    ) -> bool:
        raise AssertionError("unexpected auth write")

    async def complete_oauth_login(
        self,
        user: CurrentUser,
        encrypted_credential: str,
        session: SessionRecord,
    ) -> None:
        raise AssertionError("unexpected auth write")

    async def get_session(
        self, token_hash: str, accessed_at: datetime
    ) -> tuple[CurrentUser, SessionRecord] | None:
        raise AssertionError("unexpected session lookup")

    async def delete_session(self, token_hash: str) -> None:
        raise AssertionError("unexpected auth write")


async def test_repository_authorization_returns_persisted_installation_binding() -> (
    None
):
    store = RepositoryStore()

    repository = await require_repository_access(
        user_id="user-1",
        repository_id="77",
        store=store,
    )

    assert repository == store.allowed
    assert repository.installation_id == 9001
    assert store.lookups == [("user-1", "77")]


@pytest.mark.parametrize(
    "guard", [require_analysis_run_access, require_explanation_run_access]
)
async def test_cross_repository_run_access_is_hidden_as_not_found(
    guard: Callable[[str, str, AuthStore], Awaitable[None]],
) -> None:
    store = RepositoryStore()

    with pytest.raises(HTTPException) as raised:
        await guard("user-2", "77", store)

    assert raised.value.status_code == 404
    assert raised.value.detail == "Analysis was not found"
    assert store.lookups == [("user-2", "77")]
