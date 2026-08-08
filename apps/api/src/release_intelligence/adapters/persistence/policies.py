from __future__ import annotations

from pydantic import ValidationError
from sqlalchemy import Select, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from release_intelligence.adapters.persistence.models import (
    ReleasePolicyRow,
    RepositoryConnectionRow,
)
from release_intelligence.domain.policy import PolicyValidationError, ReleasePolicy
from release_intelligence.ports.policies import (
    PolicyPersistenceError,
    PolicyRecord,
    PolicyVersionConflictError,
)


class PolicyRepository:
    """PostgreSQL-backed append-only repository policy history."""

    def __init__(self, database_url: str) -> None:
        self._engine: AsyncEngine = create_async_engine(
            database_url, pool_pre_ping=True
        )
        self._sessions = async_sessionmaker(self._engine, expire_on_commit=False)

    async def close(self) -> None:
        await self._engine.dispose()

    async def get_latest(self, repository_id: str) -> PolicyRecord | None:
        try:
            async with self._sessions() as session:
                row = await session.scalar(self._latest_statement(repository_id))
            return self._record(row, repository_id) if row is not None else None
        except (SQLAlchemyError, ValidationError, PolicyValidationError):
            raise PolicyPersistenceError() from None

    async def create_version(
        self,
        *,
        repository_id: str,
        policy: ReleasePolicy,
        expected_version: int | None,
    ) -> PolicyRecord:
        try:
            canonical_policy = ReleasePolicy.model_validate(
                policy.model_dump(mode="json")
            )
            canonical_payload = canonical_policy.model_dump(mode="json")
        except (ValidationError, PolicyValidationError, TypeError, ValueError):
            raise PolicyPersistenceError() from None
        try:
            async with self._sessions() as session, session.begin():
                repository = await session.scalar(
                    select(RepositoryConnectionRow)
                    .where(
                        RepositoryConnectionRow.provider == "github",
                        RepositoryConnectionRow.external_repository_id
                        == repository_id
                    )
                    .with_for_update()
                )
                if repository is None:
                    raise PolicyPersistenceError()
                latest = await session.scalar(self._latest_statement(repository_id))
                actual_version = (
                    latest.configuration_version if latest is not None else None
                )
                if actual_version != expected_version:
                    raise PolicyVersionConflictError()
                next_version = (actual_version or 0) + 1
                row = ReleasePolicyRow(
                    repository_id=repository.id,
                    version=f"configuration:{next_version}",
                    configuration_version=next_version,
                    policy_payload=canonical_payload,
                )
                session.add(row)
                await session.flush()
                await session.refresh(row)
            try:
                return self._record(row, repository_id)
            except (ValidationError, PolicyValidationError):
                raise PolicyPersistenceError() from None
        except (PolicyPersistenceError, PolicyVersionConflictError):
            raise
        except SQLAlchemyError:
            raise PolicyPersistenceError() from None

    @staticmethod
    def _latest_statement(repository_id: str) -> Select[tuple[ReleasePolicyRow]]:
        return (
            select(ReleasePolicyRow)
            .join(
                RepositoryConnectionRow,
                RepositoryConnectionRow.id == ReleasePolicyRow.repository_id,
            )
            .where(
                RepositoryConnectionRow.provider == "github",
                RepositoryConnectionRow.external_repository_id == repository_id,
                ReleasePolicyRow.configuration_version.is_not(None),
            )
            .order_by(ReleasePolicyRow.configuration_version.desc())
            .limit(1)
        )

    @staticmethod
    def _record(row: ReleasePolicyRow, repository_id: str) -> PolicyRecord:
        if row.configuration_version is None or row.policy_payload is None:
            raise PolicyPersistenceError()
        return PolicyRecord(
            repository_id=repository_id,
            version=row.configuration_version,
            policy=ReleasePolicy.model_validate(row.policy_payload),
            created_at=row.created_at,
        )
