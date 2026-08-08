from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def upgrade_sql() -> str:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head", "--sql"],
        cwd=API_ROOT,
        env={
            **os.environ,
            "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost/release_intelligence",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_legacy_backfill_temporarily_disables_the_immutable_update_trigger(
    upgrade_sql: str,
) -> None:
    disabled = upgrade_sql.index(
        "ALTER TABLE human_decisions DISABLE TRIGGER human_decisions_immutable_update"
    )
    backfilled = upgrade_sql.index("UPDATE human_decisions AS target")
    enabled = upgrade_sql.index(
        "ALTER TABLE human_decisions ENABLE TRIGGER human_decisions_immutable_update"
    )

    assert disabled < backfilled < enabled


def test_legacy_backfill_uses_persisted_assessment_and_finding_evidence(
    upgrade_sql: str,
) -> None:
    assert "DEFAULT 'NEEDS_DECISION'" not in upgrade_sql
    assert "DEFAULT '[]'::jsonb" not in upgrade_sql
    assert "analysis_runs.assessment_status" in upgrade_sql
    assert "readiness_findings" in upgrade_sql
    assert "finding_evidence" in upgrade_sql


def test_legacy_backfill_fails_closed_before_aggregating_incomplete_audit_data(
    upgrade_sql: str,
) -> None:
    preflight = upgrade_sql.index("legacy human decision audit data is incomplete")
    backfill = upgrade_sql.index("UPDATE human_decisions AS target")

    assert preflight < backfill
    assert "target.finding_id IS NULL" in upgrade_sql
    assert "evidence.id IS NULL" in upgrade_sql


def test_decision_retention_constraints_allow_only_cascaded_deletion(
    upgrade_sql: str,
) -> None:
    assert "CREATE FUNCTION prevent_direct_decision_change" in upgrade_sql
    assert "pg_trigger_depth() = 1" in upgrade_sql
    assert (
        "FOREIGN KEY(finding_id) REFERENCES readiness_findings (id) ON DELETE CASCADE"
        in upgrade_sql
    )
    assert (
        "FOREIGN KEY(supersedes_decision_id) REFERENCES human_decisions (id) "
        "ON DELETE CASCADE"
        in upgrade_sql
    )
