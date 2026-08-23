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
    op.add_column(
        "human_decisions",
        sa.Column("assessment_status", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "human_decisions",
        sa.Column(
            "assessment_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM human_decisions AS target
                LEFT JOIN analysis_runs AS run
                    ON run.id = target.analysis_run_id
                LEFT JOIN readiness_findings AS selected_finding
                    ON selected_finding.id = target.finding_id
                    AND selected_finding.analysis_run_id = target.analysis_run_id
                LEFT JOIN finding_evidence AS evidence
                    ON evidence.finding_id = selected_finding.id
                WHERE run.id IS NULL
                    OR run.assessment_status IS NULL
                    OR target.finding_id IS NULL
                    OR selected_finding.id IS NULL
                    OR evidence.id IS NULL
                    OR EXISTS (
                        SELECT 1
                        FROM readiness_findings AS finding
                        WHERE finding.analysis_run_id = target.analysis_run_id
                            AND NOT EXISTS (
                                SELECT 1
                                FROM finding_evidence AS finding_item_evidence
                                WHERE finding_item_evidence.finding_id = finding.id
                            )
                    )
            ) THEN
                RAISE EXCEPTION
                    'legacy human decision audit data is incomplete';
            END IF;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "ALTER TABLE human_decisions DISABLE TRIGGER "
        "human_decisions_immutable_update"
    )
    op.execute(
        "UPDATE human_decisions AS target SET decision_sequence = ranked.sequence "
        "FROM (SELECT id, row_number() OVER (PARTITION BY analysis_run_id "
        "ORDER BY decided_at, id)::bigint AS sequence FROM human_decisions) "
        "AS ranked WHERE target.id = ranked.id"
    )
    op.execute(
        """
        UPDATE human_decisions AS target
        SET assessment_status = analysis_runs.assessment_status,
            assessment_payload = COALESCE(
                (
                    SELECT jsonb_agg(
                        jsonb_build_object(
                            'rule_id', readiness_findings.rule_id,
                            'severity', readiness_findings.severity,
                            'summary', readiness_findings.summary,
                            'required_action', readiness_findings.required_action,
                            'evidence', COALESCE(
                                (
                                    SELECT jsonb_agg(
                                        jsonb_build_object(
                                            'evidence_id', finding_evidence.evidence_id,
                                            'source_type', finding_evidence.source_type,
                                            'source_id', finding_evidence.source_id,
                                            'url', finding_evidence.url,
                                            'fingerprint', finding_evidence.fingerprint
                                        ) ORDER BY finding_evidence.position
                                    )
                                    FROM finding_evidence
                                    WHERE finding_evidence.finding_id =
                                        readiness_findings.id
                                ),
                                '[]'::jsonb
                            )
                        ) ORDER BY readiness_findings.position
                    )
                    FROM readiness_findings
                    WHERE readiness_findings.analysis_run_id = analysis_runs.id
                ),
                '[]'::jsonb
            )
        FROM analysis_runs
        WHERE target.analysis_run_id = analysis_runs.id
        """
    )
    op.execute(
        "ALTER TABLE human_decisions ENABLE TRIGGER "
        "human_decisions_immutable_update"
    )
    op.alter_column("human_decisions", "decision_sequence", nullable=False)
    op.alter_column("human_decisions", "assessment_status", nullable=False)
    op.alter_column("human_decisions", "assessment_payload", nullable=False)
    op.create_unique_constraint(
        "uq_human_decision_run_sequence",
        "human_decisions",
        ["analysis_run_id", "decision_sequence"],
    )
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
    op.drop_constraint(
        "human_decisions_finding_id_fkey", "human_decisions", type_="foreignkey"
    )
    op.create_foreign_key(
        "human_decisions_finding_id_fkey",
        "human_decisions",
        "readiness_findings",
        ["finding_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint(
        "human_decisions_supersedes_decision_id_fkey",
        "human_decisions",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "human_decisions_supersedes_decision_id_fkey",
        "human_decisions",
        "human_decisions",
        ["supersedes_decision_id"],
        ["id"],
        ondelete="NO ACTION",
        deferrable=True,
        initially="DEFERRED",
    )
    op.execute("DROP TRIGGER human_decisions_immutable_update ON human_decisions")
    op.execute(
        """
        CREATE FUNCTION prevent_direct_decision_change()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'UPDATE' OR pg_trigger_depth() = 1 THEN
                RAISE EXCEPTION 'immutable analysis records cannot be updated';
            END IF;
            RETURN OLD;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER human_decisions_immutable_change
        BEFORE UPDATE OR DELETE ON human_decisions FOR EACH ROW
        EXECUTE FUNCTION prevent_direct_decision_change()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER human_decisions_immutable_change ON human_decisions")
    op.execute("DROP FUNCTION prevent_direct_decision_change()")
    op.execute(
        "CREATE TRIGGER human_decisions_immutable_update "
        "BEFORE UPDATE ON human_decisions FOR EACH ROW "
        "EXECUTE FUNCTION prevent_immutable_analysis_update();"
    )
    op.drop_constraint(
        "human_decisions_supersedes_decision_id_fkey",
        "human_decisions",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "human_decisions_supersedes_decision_id_fkey",
        "human_decisions",
        "human_decisions",
        ["supersedes_decision_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        "human_decisions_finding_id_fkey", "human_decisions", type_="foreignkey"
    )
    op.create_foreign_key(
        "human_decisions_finding_id_fkey",
        "human_decisions",
        "readiness_findings",
        ["finding_id"],
        ["id"],
        ondelete="SET NULL",
    )
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
