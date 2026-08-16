"""Permit one guarded AI explanation state transition.

Revision ID: 0004_ai_explanation_transitions
Revises: 0003_decision_assessments
Create Date: 2026-08-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004_ai_explanation_transitions"
down_revision: str | Sequence[str] | None = "0003_decision_assessments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP TRIGGER ai_explanations_immutable_update ON ai_explanations")
    op.execute(
        """
        CREATE FUNCTION enforce_ai_explanation_transition()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.content::jsonb = '{"state":"pending"}'::jsonb THEN
                    RETURN NEW;
                END IF;
                RAISE EXCEPTION 'invalid initial AI explanation state';
            END IF;

            IF TG_OP = 'UPDATE' THEN
                IF NEW.id IS NOT DISTINCT FROM OLD.id
                    AND NEW.analysis_run_id IS NOT DISTINCT FROM OLD.analysis_run_id
                    AND NEW.created_at IS NOT DISTINCT FROM OLD.created_at
                    AND OLD.content::jsonb = '{"state":"pending"}'::jsonb
                    AND (
                        NEW.content::jsonb = '{"state":"unavailable"}'::jsonb
                        OR (
                            jsonb_typeof(NEW.content::jsonb) = 'object'
                            AND NEW.content::jsonb ->> 'state' = 'available'
                            AND NEW.content::jsonb ?&
                                ARRAY['state', 'explanation', 'metadata']
                            AND NEW.content::jsonb -
                                ARRAY['state', 'explanation', 'metadata'] =
                                '{}'::jsonb
                            AND jsonb_typeof(NEW.content::jsonb -> 'explanation') =
                                'object'
                            AND jsonb_typeof(NEW.content::jsonb -> 'metadata') =
                                'object'
                        )
                    )
                THEN
                    RETURN NEW;
                END IF;
                RAISE EXCEPTION 'invalid AI explanation state transition';
            END IF;

            IF pg_trigger_depth() = 1 THEN
                RAISE EXCEPTION 'AI explanation records cannot be deleted directly';
            END IF;
            RETURN OLD;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER ai_explanations_guarded_change
        BEFORE INSERT OR UPDATE OR DELETE ON ai_explanations FOR EACH ROW
        EXECUTE FUNCTION enforce_ai_explanation_transition()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER ai_explanations_guarded_change ON ai_explanations")
    op.execute("DROP FUNCTION enforce_ai_explanation_transition()")
    op.execute(
        "CREATE TRIGGER ai_explanations_immutable_update "
        "BEFORE UPDATE ON ai_explanations FOR EACH ROW "
        "EXECUTE FUNCTION prevent_immutable_analysis_update();"
    )
