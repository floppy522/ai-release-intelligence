"""Add immutable versioned repository release-policy configuration.

Revision ID: 0002_policy_config
Revises: 0001_initial
Create Date: 2026-08-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002_policy_config"
down_revision: str | Sequence[str] | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "release_policies",
        sa.Column("configuration_version", sa.Integer(), nullable=True),
    )
    op.add_column(
        "release_policies",
        sa.Column(
            "policy_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.create_unique_constraint(
        "uq_repository_policy_configuration_version",
        "release_policies",
        ["repository_id", "configuration_version"],
    )
    op.create_check_constraint(
        "ck_release_policy_configuration_payload",
        "release_policies",
        "(configuration_version IS NULL AND policy_payload IS NULL) OR "
        "(configuration_version IS NOT NULL AND configuration_version > 0 "
        "AND policy_payload IS NOT NULL "
        "AND jsonb_typeof(policy_payload) = 'object')",
    )
    op.execute(
        """
        CREATE FUNCTION prevent_immutable_policy_update()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'immutable policy records cannot be updated';
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER release_policies_immutable_update
        BEFORE UPDATE ON release_policies FOR EACH ROW
        EXECUTE FUNCTION prevent_immutable_policy_update();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER release_policies_immutable_update ON release_policies;"
        "DROP FUNCTION prevent_immutable_policy_update();"
    )
    op.drop_constraint(
        "ck_release_policy_configuration_payload",
        "release_policies",
        type_="check",
    )
    op.drop_constraint(
        "uq_repository_policy_configuration_version",
        "release_policies",
        type_="unique",
    )
    op.drop_column("release_policies", "policy_payload")
    op.drop_column("release_policies", "configuration_version")
