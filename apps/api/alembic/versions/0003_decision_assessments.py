"""Persist immutable human-decision reassessments.

Revision ID: 0003_decision_assessments
Revises: 0002_policy_config
Create Date: 2026-08-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003_decision_assessments"
down_revision: str | Sequence[str] | None = "0002_policy_config"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "human_decisions",
        sa.Column("decision_sequence", sa.BigInteger(), nullable=True),
    )
    op.execute(
        "UPDATE human_decisions AS target SET decision_sequence = ranked.sequence "
        "FROM (SELECT id, row_number() OVER (PARTITION BY analysis_run_id "
        "ORDER BY decided_at, id)::bigint AS sequence FROM human_decisions) "
        "AS ranked WHERE target.id = ranked.id"
    )
    op.alter_column("human_decisions", "decision_sequence", nullable=False)
    op.create_unique_constraint(
        "uq_human_decision_run_sequence",
        "human_decisions",
        ["analysis_run_id", "decision_sequence"],
    )
    op.add_column(
        "human_decisions",
        sa.Column(
            "assessment_status",
            sa.String(length=32),
            nullable=False,
            server_default="NEEDS_DECISION",
        ),
    )
    op.add_column(
        "human_decisions",
        sa.Column(
            "assessment_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.alter_column("human_decisions", "assessment_status", server_default=None)
    op.alter_column("human_decisions", "assessment_payload", server_default=None)
    op.create_check_constraint(
        "ck_human_decision_sequence",
        "human_decisions",
        "decision_sequence > 0",
    )
    op.create_check_constraint(
        "ck_human_decision_kind",
        "human_decisions",
        "decision IN ('ACCEPTED_RISK', 'RELEASE_BLOCKER')",
    )
    op.create_check_constraint(
        "ck_human_decision_reason",
        "human_decisions",
        "length(btrim(reason)) BETWEEN 1 AND 4000",
    )
    op.create_check_constraint(
        "ck_human_decision_actor",
        "human_decisions",
        "length(btrim(actor_id)) BETWEEN 1 AND 255",
    )
    op.create_check_constraint(
        "ck_human_decision_fingerprint",
        "human_decisions",
        "fingerprint ~ '^sha256:[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "ck_human_decision_assessment_status",
        "human_decisions",
        "assessment_status IN "
        "('READY', 'NOT_READY', 'NEEDS_DECISION', 'INSUFFICIENT_DATA')",
    )
    op.create_check_constraint(
        "ck_human_decision_assessment_payload",
        "human_decisions",
        "jsonb_typeof(assessment_payload) = 'array'",
    )
    op.execute(
        "CREATE TRIGGER human_decisions_immutable_delete "
        "BEFORE DELETE ON human_decisions FOR EACH ROW "
        "EXECUTE FUNCTION prevent_immutable_analysis_update();"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER human_decisions_immutable_delete ON human_decisions")
    for name in (
        "ck_human_decision_assessment_payload",
        "ck_human_decision_assessment_status",
        "ck_human_decision_fingerprint",
        "ck_human_decision_actor",
        "ck_human_decision_reason",
        "ck_human_decision_kind",
        "ck_human_decision_sequence",
    ):
        op.drop_constraint(name, "human_decisions", type_="check")
    op.drop_constraint(
        "uq_human_decision_run_sequence", "human_decisions", type_="unique"
    )
    op.drop_column("human_decisions", "assessment_payload")
    op.drop_column("human_decisions", "assessment_status")
    op.drop_column("human_decisions", "decision_sequence")
