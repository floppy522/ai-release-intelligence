from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parents[2]
DATABASE_URL = "postgresql+asyncpg://user:pass@localhost/release_intelligence"


def _offline_sql(*arguments: str) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *arguments],
        cwd=API_ROOT,
        env={**os.environ, "DATABASE_URL": DATABASE_URL},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.fixture(scope="module")
def upgrade_sql() -> str:
    return _offline_sql("upgrade", "head", "--sql")


@pytest.fixture(scope="module")
def downgrade_sql() -> str:
    return _offline_sql(
        "downgrade",
        "0004_ai_explanation_transitions:0003_decision_assessments",
        "--sql",
    )


def test_upgrade_replaces_blanket_immutability_with_one_guarded_transition(
    upgrade_sql: str,
) -> None:
    dropped = upgrade_sql.index(
        "DROP TRIGGER ai_explanations_immutable_update ON ai_explanations"
    )
    function = upgrade_sql.index("CREATE FUNCTION enforce_ai_explanation_transition")
    trigger = upgrade_sql.index("CREATE TRIGGER ai_explanations_guarded_change")

    assert dropped < function < trigger
    assert 'OLD.content::jsonb = \'{"state":"pending"}\'::jsonb' in upgrade_sql
    assert 'NEW.content::jsonb = \'{"state":"unavailable"}\'::jsonb' in upgrade_sql
    assert "NEW.id IS NOT DISTINCT FROM OLD.id" in upgrade_sql
    assert "NEW.analysis_run_id IS NOT DISTINCT FROM OLD.analysis_run_id" in upgrade_sql
    assert "NEW.created_at IS NOT DISTINCT FROM OLD.created_at" in upgrade_sql
    assert "pg_trigger_depth() = 1" in upgrade_sql


def test_upgrade_guards_insert_and_available_payload_shape(
    upgrade_sql: str,
) -> None:
    normalized = " ".join(upgrade_sql.split())

    assert "IF TG_OP = 'INSERT'" in normalized
    assert "invalid initial AI explanation state" in normalized
    assert "jsonb_typeof(NEW.content::jsonb) = 'object'" in normalized
    assert "jsonb_typeof(NEW.content::jsonb -> 'explanation') = 'object'" in normalized
    assert "jsonb_typeof(NEW.content::jsonb -> 'metadata') = 'object'" in normalized
    assert (
        "NEW.content::jsonb - ARRAY['state', 'explanation', 'metadata']" in normalized
    )


def test_downgrade_restores_original_immutable_update_trigger(
    downgrade_sql: str,
) -> None:
    dropped = downgrade_sql.index(
        "DROP TRIGGER ai_explanations_guarded_change ON ai_explanations"
    )
    restored = downgrade_sql.index("CREATE TRIGGER ai_explanations_immutable_update")

    assert dropped < restored
    assert "DROP FUNCTION enforce_ai_explanation_transition()" in downgrade_sql
