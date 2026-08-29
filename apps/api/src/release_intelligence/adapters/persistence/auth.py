from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from sqlalchemy import delete, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from release_intelligence.adapters.persistence.models import (
    EncryptedUserCredentialRow,
    GitHubInstallationRow,
    OAuthStateRow,
    RepositoryConnectionRow,
    UserInstallationAccessRow,
    UserRow,
    WebSessionRow,
)
from release_intelligence.api.dependencies import (
    AuthorizedRepository,
    CurrentUser,
    SessionRecord,
)
from release_intelligence.ports.auth import AuthPersistenceError


class AuthRepository:
    """PostgreSQL boundary for encrypted credentials and server-side sessions."""

    def __init__(self, database_url: str) -> None:
        self._engine: AsyncEngine = create_async_engine(
            database_url, pool_pre_ping=True
        )
        self._sessions = async_sessionmaker(self._engine, expire_on_commit=False)

    async def close(self) -> None:
        await self._engine.dispose()

    async def save_oauth_state(
        self, state_hash: str, binding_hash: str, expires_at: datetime
    ) -> None:
        self._require_aware(expires_at)
        async with self._session(transaction=True) as session:
            session.add(
                OAuthStateRow(
                    state_hash=state_hash,
                    binding_hash=binding_hash,
                    expires_at=expires_at,
                )
            )

    async def consume_oauth_state(
        self, state_hash: str, binding_hash: str, consumed_at: datetime
    ) -> bool:
        self._require_aware(consumed_at)
        statement = (
            delete(OAuthStateRow)
            .where(
                OAuthStateRow.state_hash == state_hash,
                OAuthStateRow.binding_hash == binding_hash,
                OAuthStateRow.expires_at > consumed_at,
            )
            .returning(OAuthStateRow.id)
        )
        async with self._session(transaction=True) as session:
            return (await session.scalar(statement)) is not None

    async def upsert_user_with_credential(
        self, user: CurrentUser, encrypted_credential: str
    ) -> None:
        if not encrypted_credential:
            raise ValueError("encrypted credential is required")
        async with self._session(transaction=True) as session:
            await self._upsert_user_credential(
                session, user, encrypted_credential
            )

    async def create_session(self, session_record: SessionRecord) -> None:
        self._require_aware(session_record.expires_at)
        async with self._session(transaction=True) as session:
            await self._insert_session(session, session_record)

    async def complete_oauth_login(
        self,
        user: CurrentUser,
        encrypted_credential: str,
        session_record: SessionRecord,
    ) -> None:
        """Commit identity, encrypted credential, and web session atomically."""
        if not encrypted_credential:
            raise ValueError("encrypted credential is required")
        if session_record.user_id != user.id:
            raise ValueError("session user must match authenticated user")
        self._require_aware(session_record.expires_at)
        async with self._session(transaction=True) as session:
            await self._upsert_user_credential(
                session, user, encrypted_credential
            )
            await self._insert_session(session, session_record)

    async def get_session(
        self, token_hash: str, accessed_at: datetime
    ) -> tuple[CurrentUser, SessionRecord] | None:
        self._require_aware(accessed_at)
        statement = (
            select(WebSessionRow, UserRow)
            .join(UserRow, UserRow.id == WebSessionRow.user_id)
            .where(
                WebSessionRow.token_hash == token_hash,
                WebSessionRow.expires_at > accessed_at,
            )
        )
        async with self._session() as session:
            row = (await session.execute(statement)).one_or_none()
        if row is None:
            return None
        web_session, user = row
        current_user = CurrentUser(id=user.external_user_id, login=user.login)
        return current_user, SessionRecord(
            user_id=current_user.id,
            token_hash=web_session.token_hash,
            csrf_token_hash=web_session.csrf_token_hash,
            expires_at=web_session.expires_at,
        )

    async def delete_session(self, token_hash: str) -> None:
        async with self._session(transaction=True) as session:
            await session.execute(
                delete(WebSessionRow).where(WebSessionRow.token_hash == token_hash)
            )

    async def connect_repository(
        self,
        *,
        user_id: str,
        installation_id: int,
        repository_id: str,
        full_name: str,
    ) -> None:
        """Bind one selected repository to the user's GitHub App installation."""
        async with self._session(transaction=True) as session:
            user_row = await self._user_row(session, user_id)
            await session.execute(
                insert(GitHubInstallationRow)
                .values(external_installation_id=installation_id)
                .on_conflict_do_nothing(
                    index_elements=[GitHubInstallationRow.external_installation_id]
                )
            )
            installation = await session.scalar(
                select(GitHubInstallationRow).where(
                    GitHubInstallationRow.external_installation_id == installation_id,
                )
            )
            if installation is None:
                raise AuthPersistenceError() from None
            await session.execute(
                insert(UserInstallationAccessRow)
                .values(user_id=user_row.id, installation_id=installation.id)
                .on_conflict_do_nothing()
            )
            await session.execute(
                insert(RepositoryConnectionRow)
                .values(
                    installation_id=installation.id,
                    provider="github",
                    external_repository_id=repository_id,
                    full_name=full_name,
                )
                .on_conflict_do_update(
                    constraint="uq_repository_identity",
                    set_={
                        "installation_id": installation.id,
                        "full_name": full_name,
                    },
                )
            )

    async def find_repository_access(
        self, user_id: str, repository_id: str
    ) -> AuthorizedRepository | None:
        statement = (
            select(RepositoryConnectionRow, GitHubInstallationRow)
            .join(
                GitHubInstallationRow,
                GitHubInstallationRow.id == RepositoryConnectionRow.installation_id,
            )
            .join(
                UserInstallationAccessRow,
                UserInstallationAccessRow.installation_id == GitHubInstallationRow.id,
            )
            .join(UserRow, UserRow.id == UserInstallationAccessRow.user_id)
            .where(
                UserRow.provider == "github",
                UserRow.external_user_id == user_id,
                or_(
                    RepositoryConnectionRow.external_repository_id == repository_id,
                    RepositoryConnectionRow.full_name == repository_id,
                ),
            )
        )
        async with self._session() as session:
            row = (await session.execute(statement)).one_or_none()
        if row is None:
            return None
        repository, installation = row
        return AuthorizedRepository(
            repository_id=repository.external_repository_id,
            full_name=repository.full_name,
            installation_id=installation.external_installation_id,
        )

    async def disconnect_installation(
        self, *, user_id: str, installation_id: int
    ) -> bool:
        """Delete an authorized installation and cascade its repository connection."""
        async with self._session(transaction=True) as session:
            installation = await session.scalar(
                select(GitHubInstallationRow)
                .join(
                    UserInstallationAccessRow,
                    UserInstallationAccessRow.installation_id
                    == GitHubInstallationRow.id,
                )
                .join(UserRow, UserRow.id == UserInstallationAccessRow.user_id)
                .where(
                    UserRow.provider == "github",
                    UserRow.external_user_id == user_id,
                    GitHubInstallationRow.external_installation_id
                    == installation_id,
                )
            )
            if installation is None:
                return False
            await session.delete(installation)
        return True

    @staticmethod
    async def _upsert_user_credential(
        session: AsyncSession,
        user: CurrentUser,
        encrypted_credential: str,
    ) -> None:
        await session.execute(
            insert(UserRow)
            .values(
                provider="github",
                external_user_id=user.id,
                login=user.login,
            )
            .on_conflict_do_update(
                constraint="uq_user_identity",
                set_={"login": user.login},
            )
        )
        user_row = await session.scalar(
            select(UserRow).where(
                UserRow.provider == "github",
                UserRow.external_user_id == user.id,
            )
        )
        if user_row is None:
            raise AuthPersistenceError() from None
        await session.execute(
            insert(EncryptedUserCredentialRow)
            .values(
                user_id=user_row.id,
                encrypted_token=encrypted_credential,
            )
            .on_conflict_do_update(
                index_elements=[EncryptedUserCredentialRow.user_id],
                set_={
                    "encrypted_token": encrypted_credential,
                    "updated_at": datetime.now(UTC),
                },
            )
        )

    @classmethod
    async def _insert_session(
        cls, session: AsyncSession, session_record: SessionRecord
    ) -> None:
        user_row = await cls._user_row(session, session_record.user_id)
        session.add(
            WebSessionRow(
                user_id=user_row.id,
                token_hash=session_record.token_hash,
                csrf_token_hash=session_record.csrf_token_hash,
                expires_at=session_record.expires_at,
            )
        )

    @staticmethod
    async def _user_row(session: AsyncSession, user_id: str) -> UserRow:
        user = await session.scalar(
            select(UserRow).where(
                UserRow.provider == "github",
                UserRow.external_user_id == user_id,
            )
        )
        if user is None:
            raise AuthPersistenceError() from None
        return user

    @asynccontextmanager
    async def _session(
        self, *, transaction: bool = False
    ) -> AsyncIterator[AsyncSession]:
        try:
            async with self._sessions() as session:
                if transaction:
                    async with session.begin():
                        yield session
                else:
                    yield session
        except SQLAlchemyError:
            raise AuthPersistenceError() from None

    @staticmethod
    def _require_aware(timestamp: datetime) -> None:
        if timestamp.tzinfo is None:
            raise ValueError("authentication timestamps must be timezone-aware")
