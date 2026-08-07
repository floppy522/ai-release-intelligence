"""Create immutable PostgreSQL storage for release analysis runs.

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_initial"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    timestamp = sa.DateTime(timezone=True)

    op.create_table(
        "repository_connections",
        sa.Column("id", uuid, primary_key=True, nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("external_repository_id", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.UniqueConstraint("provider", "external_repository_id", name="uq_repository_identity"),
    )
    op.create_table(
        "release_policies",
        sa.Column("id", uuid, primary_key=True, nullable=False),
        sa.Column("repository_id", uuid, nullable=False),
        sa.Column("version", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"], ["repository_connections.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("repository_id", "version", name="uq_release_policy_version"),
    )
    op.create_table(
        "releases",
        sa.Column("id", uuid, primary_key=True, nullable=False),
        sa.Column("repository_id", uuid, nullable=False),
        sa.Column("github_milestone_number", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"], ["repository_connections.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "repository_id", "github_milestone_number", name="uq_release_repository_milestone"
        ),
    )
    op.create_table(
        "analysis_runs",
        sa.Column("id", uuid, primary_key=True, nullable=False),
        sa.Column("release_id", uuid, nullable=False),
        sa.Column("policy_version", sa.String(length=128), nullable=False),
        sa.Column("source_fetched_at", timestamp, nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("assessment_status", sa.String(length=32), nullable=True),
        sa.Column(
            "started_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("completed_at", timestamp, nullable=False),
        sa.ForeignKeyConstraint(["release_id"], ["releases.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "release_snapshots",
        sa.Column("id", uuid, primary_key=True, nullable=False),
        sa.Column("analysis_run_id", uuid, nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.ForeignKeyConstraint(["analysis_run_id"], ["analysis_runs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("analysis_run_id"),
    )
    op.create_table(
        "readiness_findings",
        sa.Column("id", uuid, primary_key=True, nullable=False),
        sa.Column("analysis_run_id", uuid, nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.String(length=255), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("required_action", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["analysis_run_id"], ["analysis_runs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("analysis_run_id", "position", name="uq_finding_run_position"),
    )
    op.create_table(
        "finding_evidence",
        sa.Column("id", uuid, primary_key=True, nullable=False),
        sa.Column("finding_id", uuid, nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("evidence_id", sa.String(length=255), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=255), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(
            ["finding_id"], ["readiness_findings.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("finding_id", "position", name="uq_finding_evidence_position"),
    )
    op.create_table(
        "human_decisions",
        sa.Column("id", uuid, primary_key=True, nullable=False),
        sa.Column("analysis_run_id", uuid, nullable=False),
        sa.Column("finding_id", uuid, nullable=True),
        sa.Column("fingerprint", sa.String(length=255), nullable=False),
        sa.Column("supersedes_decision_id", uuid, nullable=True),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column(
            "decided_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.ForeignKeyConstraint(["analysis_run_id"], ["analysis_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["finding_id"], ["readiness_findings.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["supersedes_decision_id"], ["human_decisions.id"], ondelete="RESTRICT"
        ),
    )
    op.create_table(
        "ai_explanations",
        sa.Column("id", uuid, primary_key=True, nullable=False),
        sa.Column("analysis_run_id", uuid, nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.ForeignKeyConstraint(["analysis_run_id"], ["analysis_runs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("analysis_run_id"),
    )
    op.create_table(
        "web_sessions",
        sa.Column("id", uuid, primary_key=True, nullable=False),
        sa.Column("repository_id", uuid, nullable=True),
        sa.Column("token_hash", sa.String(length=255), nullable=False),
        sa.Column("expires_at", timestamp, nullable=False),
        sa.Column(
            "created_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"], ["repository_connections.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("token_hash"),
    )

    op.execute(
        """
        CREATE FUNCTION prevent_immutable_analysis_update()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'immutable analysis records cannot be updated';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table_name in (
        "analysis_runs",
        "release_snapshots",
        "readiness_findings",
        "finding_evidence",
        "human_decisions",
        "ai_explanations",
    ):
        op.execute(
            f"CREATE TRIGGER {table_name}_immutable_update "
            f"BEFORE UPDATE ON {table_name} FOR EACH ROW "
            "EXECUTE FUNCTION prevent_immutable_analysis_update();"
        )


def downgrade() -> None:
    op.drop_table("web_sessions")
    op.drop_table("ai_explanations")
    op.drop_table("human_decisions")
    op.drop_table("finding_evidence")
    op.drop_table("readiness_findings")
    op.drop_table("release_snapshots")
    op.drop_table("analysis_runs")
    op.drop_table("releases")
    op.drop_table("release_policies")
    op.drop_table("repository_connections")
    op.execute("DROP FUNCTION prevent_immutable_analysis_update();")
