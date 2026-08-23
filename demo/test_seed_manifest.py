"""Offline content contracts for the fictional public-release demo assets."""

from __future__ import annotations

import re
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "demo" / "seed_manifest.yaml"
DEMO_REPOSITORY = ROOT / "demo" / "repository"


@pytest.fixture
def manifest() -> dict[str, object]:
    """Load the bounded, versioned fictional-demo manifest."""

    with MANIFEST_PATH.open(encoding="utf-8") as stream:
        loaded = yaml.safe_load(stream)
    assert isinstance(loaded, dict)
    return loaded


def test_demo_manifest_has_required_release_evidence(
    manifest: dict[str, object],
) -> None:
    """A malformed seed cannot yield the documented decision-required demo."""

    assert manifest["schema_version"] == 1
    assert manifest["repository"] == {
        "owner": "floppy522",
        "name": "ai-release-intelligence-demo",
        "visibility": "public",
    }
    assert manifest["milestone"] == "Release 2026.08.10"
    assert manifest["milestone_number"] == 7
    assert manifest["previous_milestone_number"] == 6
    assert manifest["candidate_branch"] == "release/2026-08-10"
    assert manifest["previous_release_branch"] == "release/2026-08-03"
    assert {
        "code-change",
        "release-ops",
        "release-blocker",
        "migration-required",
    } <= set(manifest["labels"])  # type: ignore[arg-type]
    states = manifest["demo_states"]
    assert isinstance(states, list)
    assert {state["expected_status"] for state in states} == {
        "NEEDS_DECISION",
        "READY",
    }


def test_manifest_carries_current_previous_and_decision_evidence(
    manifest: dict[str, object],
) -> None:
    """Removing a relationship would make the release path misleading."""

    issues = manifest["issues"]
    pulls = manifest["pull_requests"]
    checks = manifest["checks"]
    assert isinstance(issues, list)
    assert isinstance(pulls, list)
    assert isinstance(checks, list)
    issue_keys = {issue["key"] for issue in issues}
    assert {"previous-code", "current-code", "release-operations"} <= issue_keys
    assert all(issue["marker"].startswith("ari-demo:v1:") for issue in issues)
    assert any("release-ops" in issue["labels"] for issue in issues)
    assert any("migration-required" in issue["labels"] for issue in issues)
    assert {(pull["issue_key"], pull["base"]) for pull in pulls if pull["merged"]} >= {
        ("previous-code", "release/2026-08-03"),
        ("previous-code", "main"),
        ("current-code", "main"),
    }
    assert {
        (check["name"], check["category"], check["expected_conclusion"])
        for check in checks
    } == {
        ("blocking-suite", "BLOCKING", "success"),
        ("advisory-synthetic", "ADVISORY", "failure"),
    }


def test_demo_repository_is_runnable_safe_and_action_pinned() -> None:
    """The repository fixture keeps migration behavior and workflow safety."""

    migration = DEMO_REPOSITORY / "scripts" / "migrate.py"
    migration_test = DEMO_REPOSITORY / "tests" / "test_migration.py"
    workflow = DEMO_REPOSITORY / ".github" / "workflows" / "release-ci.yml"
    assert migration.is_file()
    assert migration_test.is_file()
    assert workflow.is_file()

    completed = subprocess.run(
        [sys.executable, str(migration), "--database", ":memory:"],
        cwd=DEMO_REPOSITORY,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    workflow_text = workflow.read_text(encoding="utf-8")
    parsed = yaml.safe_load(workflow_text)
    assert isinstance(parsed, dict)
    assert parsed["permissions"] == {"contents": "read"}
    assert "secrets." not in workflow_text
    assert "pull_request_target" not in workflow_text
    for line in workflow_text.splitlines():
        if "uses:" in line and not line.lstrip().startswith("#"):
            reference = line.split("uses:", 1)[1].strip()
            assert re.fullmatch(r"[^@\s]+@[0-9a-f]{40}", reference)


def test_migration_is_idempotent(tmp_path: Path) -> None:
    """A repeated safe migration leaves one marker and succeeds."""

    database = tmp_path / "fictional.sqlite3"
    command = [sys.executable, "scripts/migrate.py", "--database", str(database)]
    for _ in range(2):
        completed = subprocess.run(
            command,
            cwd=DEMO_REPOSITORY,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
    with closing(sqlite3.connect(database)) as connection:
        rows = connection.execute(
            "SELECT name FROM release_migration_markers"
        ).fetchall()
    assert rows == [("fictional-payment-schema-v1",)]
