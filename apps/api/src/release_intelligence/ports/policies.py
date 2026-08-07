from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from release_intelligence.domain.policy import ReleasePolicy


class PolicyPersistenceError(Exception):
    """Sanitized policy persistence failure safe for API handling."""

    def __init__(self) -> None:
        super().__init__("Policy persistence unavailable")


class PolicyVersionConflictError(Exception):
    """The caller attempted to update a policy version that is no longer latest."""

    def __init__(self) -> None:
        super().__init__("Policy version conflict")


@dataclass(frozen=True)
class PolicyRecord:
    repository_id: str
    version: int
    policy: ReleasePolicy
    created_at: datetime


class PolicyRepositoryPort(Protocol):
    async def get_latest(self, repository_id: str) -> PolicyRecord | None: ...

    async def create_version(
        self,
        *,
        repository_id: str,
        policy: ReleasePolicy,
        expected_version: int | None,
    ) -> PolicyRecord: ...
