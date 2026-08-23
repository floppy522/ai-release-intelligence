from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class UserRow(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("provider", "external_user_id", name="uq_user_identity"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    external_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    login: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    credential: Mapped[EncryptedUserCredentialRow | None] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    installation_access: Mapped[list[UserInstallationAccessRow]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    web_sessions: Mapped[list[WebSessionRow]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class EncryptedUserCredentialRow(Base):
    __tablename__ = "encrypted_user_credentials"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    encrypted_token: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user: Mapped[UserRow] = relationship(back_populates="credential")


class GitHubInstallationRow(Base):
    __tablename__ = "github_installations"

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    external_installation_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, unique=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user_access: Mapped[list[UserInstallationAccessRow]] = relationship(
        back_populates="installation",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    repositories: Mapped[list[RepositoryConnectionRow]] = relationship(
        back_populates="installation",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class UserInstallationAccessRow(Base):
    __tablename__ = "user_installation_access"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    installation_id: Mapped[UUID] = mapped_column(
        ForeignKey("github_installations.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user: Mapped[UserRow] = relationship(back_populates="installation_access")
    installation: Mapped[GitHubInstallationRow] = relationship(
        back_populates="user_access"
    )


class OAuthStateRow(Base):
    __tablename__ = "oauth_states"

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    binding_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RepositoryConnectionRow(Base):
    __tablename__ = "repository_connections"
    __table_args__ = (
        UniqueConstraint(
            "provider", "external_repository_id", name="uq_repository_identity"
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    installation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("github_installations.id", ondelete="CASCADE"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    external_repository_id: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    policies: Mapped[list[ReleasePolicyRow]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )
    releases: Mapped[list[ReleaseRow]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )
    installation: Mapped[GitHubInstallationRow | None] = relationship(
        back_populates="repositories"
    )


class ReleasePolicyRow(Base):
    __tablename__ = "release_policies"
    __table_args__ = (
        UniqueConstraint("repository_id", "version", name="uq_release_policy_version"),
        UniqueConstraint(
            "repository_id",
            "configuration_version",
            name="uq_repository_policy_configuration_version",
        ),
        CheckConstraint(
            "(configuration_version IS NULL AND policy_payload IS NULL) OR "
            "(configuration_version IS NOT NULL AND configuration_version > 0 "
            "AND policy_payload IS NOT NULL "
            "AND jsonb_typeof(policy_payload) = 'object')",
            name="ck_release_policy_configuration_payload",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    repository_id: Mapped[UUID] = mapped_column(
        ForeignKey("repository_connections.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[str] = mapped_column(String(128), nullable=False)
    configuration_version: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    policy_payload: Mapped[dict[str, object] | None] = mapped_column(
        JSONB, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    repository: Mapped[RepositoryConnectionRow] = relationship(
        back_populates="policies"
    )


class ReleaseRow(Base):
    __tablename__ = "releases"
    __table_args__ = (
        UniqueConstraint(
            "repository_id",
            "github_milestone_number",
            name="uq_release_repository_milestone",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    repository_id: Mapped[UUID] = mapped_column(
        ForeignKey("repository_connections.id", ondelete="CASCADE"), nullable=False
    )
    github_milestone_number: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    repository: Mapped[RepositoryConnectionRow] = relationship(
        back_populates="releases"
    )
    analysis_runs: Mapped[list[AnalysisRunRow]] = relationship(
        back_populates="release", cascade="all, delete-orphan"
    )


class AnalysisRunRow(Base):
    __tablename__ = "analysis_runs"

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    release_id: Mapped[UUID] = mapped_column(
        ForeignKey("releases.id", ondelete="CASCADE"), nullable=False
    )
    policy_version: Mapped[str] = mapped_column(String(128), nullable=False)
    source_fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    assessment_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    release: Mapped[ReleaseRow] = relationship(back_populates="analysis_runs")
    snapshot: Mapped[ReleaseSnapshotRow] = relationship(
        back_populates="analysis_run", uselist=False, cascade="all, delete-orphan"
    )
    findings: Mapped[list[ReadinessFindingRow]] = relationship(
        back_populates="analysis_run",
        cascade="all, delete-orphan",
        order_by="ReadinessFindingRow.position",
    )
    decisions: Mapped[list[HumanDecisionRow]] = relationship(
        back_populates="analysis_run", cascade="all, delete-orphan"
    )
    ai_explanation: Mapped[AIExplanationRow | None] = relationship(
        back_populates="analysis_run", uselist=False, cascade="all, delete-orphan"
    )


class ReleaseSnapshotRow(Base):
    __tablename__ = "release_snapshots"

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    analysis_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    analysis_run: Mapped[AnalysisRunRow] = relationship(back_populates="snapshot")


class ReadinessFindingRow(Base):
    __tablename__ = "readiness_findings"
    __table_args__ = (
        UniqueConstraint("analysis_run_id", "position", name="uq_finding_run_position"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    analysis_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    rule_id: Mapped[str] = mapped_column(String(255), nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    required_action: Mapped[str] = mapped_column(Text, nullable=False)

    analysis_run: Mapped[AnalysisRunRow] = relationship(back_populates="findings")
    evidence: Mapped[list[FindingEvidenceRow]] = relationship(
        back_populates="finding",
        cascade="all, delete-orphan",
        order_by="FindingEvidenceRow.position",
    )


class FindingEvidenceRow(Base):
    __tablename__ = "finding_evidence"
    __table_args__ = (
        UniqueConstraint("finding_id", "position", name="uq_finding_evidence_position"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    finding_id: Mapped[UUID] = mapped_column(
        ForeignKey("readiness_findings.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(255), nullable=False)

    finding: Mapped[ReadinessFindingRow] = relationship(back_populates="evidence")


class HumanDecisionRow(Base):
    __tablename__ = "human_decisions"

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    analysis_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False
    )
    finding_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("readiness_findings.id", ondelete="CASCADE"), nullable=True
    )
    fingerprint: Mapped[str] = mapped_column(String(255), nullable=False)
    supersedes_decision_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "human_decisions.id",
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=True,
    )
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    decision_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    assessment_status: Mapped[str] = mapped_column(String(32), nullable=False)
    assessment_payload: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False
    )
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    analysis_run: Mapped[AnalysisRunRow] = relationship(back_populates="decisions")
    supersedes: Mapped[HumanDecisionRow | None] = relationship(
        foreign_keys=[supersedes_decision_id], remote_side="HumanDecisionRow.id"
    )


class AIExplanationRow(Base):
    __tablename__ = "ai_explanations"

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    analysis_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    analysis_run: Mapped[AnalysisRunRow] = relationship(back_populates="ai_explanation")


class WebSessionRow(Base):
    __tablename__ = "web_sessions"

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    csrf_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user: Mapped[UserRow] = relationship(back_populates="web_sessions")
