"""Offline contracts for the fictional public-release demo seed assets.

These tests execute the seed script only against a fake ``gh`` binary.  They
exercise the script boundary (arguments, exit status, and emitted commands)
without contacting GitHub or relying on a local credential.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "demo" / "seed_manifest.yaml"
SEED_SCRIPT = ROOT / "demo" / "seed_demo_repo.sh"
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
    repository = manifest["repository"]
    assert repository == {
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
    } <= set(manifest["labels"])
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
    """A modified workflow must keep migration behavior and PR safety intact."""

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
    assert "permissions:\n  contents: read" in workflow_text
    assert "secrets." not in workflow_text
    assert "pull_request_target" not in workflow_text
    for line in workflow_text.splitlines():
        if "uses:" in line and not line.lstrip().startswith("#"):
            reference = line.split("uses:", 1)[1].strip()
            assert "@" in reference
            assert len(reference.rsplit("@", 1)[1]) == 40


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
    import sqlite3

    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT name FROM release_migration_markers"
        ).fetchall()
    assert rows == [("fictional-payment-schema-v1",)]


def _write_fake_gh(directory: Path) -> Path:
    executable = directory / "gh"
    executable.write_text(
        """#!/usr/bin/env bash
set -eu
: \"${FAKE_GH_LOG:?}\"
printf '%s\\n' \"$*\" >> \"$FAKE_GH_LOG\"
if [ \"${1:-}\" = auth ]; then
  [ \"${FAKE_GH_AUTH:-ok}\" = ok ] || exit 1
  exit 0
fi
if [ \"${1:-}\" = repo ] && [ \"${2:-}\" = view ]; then
  [ \"${FAKE_GH_REPO:-present}\" = present ] || exit 1
  printf '%s\\n' '{\"nameWithOwner\":\"floppy522/ai-release-intelligence-demo\",\"visibility\":\"PUBLIC\"}'
  exit 0
fi
if [ \"${1:-}\" = issue ] && [ \"${2:-}\" = list ]; then
  if [ -z \"${FAKE_GH_ISSUES:-}\" ]; then
    case \"$*\" in
      *previous-code*) printf '%s\\n' '[{\"number\": 11, \"title\": \"[ari-demo:v1:previous-code] fictional\"}]' ;;
      *current-code*) printf '%s\\n' '[{\"number\": 12, \"title\": \"[ari-demo:v1:current-code] fictional\"}]' ;;
      *release-operations*) printf '%s\\n' '[{\"number\": 13, \"title\": \"[ari-demo:v1:release-operations] fictional\"}]' ;;
      *resolved-blocker*) printf '%s\\n' '[{\"number\": 14, \"title\": \"[ari-demo:v1:resolved-blocker] fictional\"}]' ;;
      *) printf '%s\\n' '[]' ;;
    esac
    exit 0
  fi
  printf '%s\\n' \"${FAKE_GH_ISSUES:-[]}\"
  exit 0
fi
if [ \"${1:-}\" = pr ] && [ \"${2:-}\" = list ]; then
  if [ -z \"${FAKE_GH_PRS:-}\" ]; then
    case \"$*\" in
      *previous-release*) printf '%s\\n' '[{\"number\": 21, \"title\": \"[ari-demo:v1:previous-release] fictional\", \"headRefName\": \"fixture/previous-release-demo\", \"baseRefName\": \"release/2026-08-03\"}]' ;;
      *previous-main*) printf '%s\\n' '[{\"number\": 22, \"title\": \"[ari-demo:v1:previous-main] fictional\", \"headRefName\": \"fixture/previous-main-demo\", \"baseRefName\": \"main\"}]' ;;
      *current-main*) printf '%s\\n' '[{\"number\": 23, \"title\": \"[ari-demo:v1:current-main] fictional\", \"headRefName\": \"fixture/current-main-demo\", \"baseRefName\": \"main\"}]' ;;
      *) printf '%s\\n' '[]' ;;
    esac
    exit 0
  fi
  printf '%s\\n' \"${FAKE_GH_PRS:-[]}\"
  exit 0
fi
if [ \"${1:-}\" = api ]; then
  case \"$*\" in *git/ref/heads/fixture/*) exit 1 ;; esac
  case \"$*\" in
    *collaborators/*) printf '%s\\n' 'write' ;;
    *search/issues*)
      if [ -n \"${FAKE_GH_SEARCH:-}\" ]; then
        printf '%s\\n' \"$FAKE_GH_SEARCH\"
      else
      case \"$*\" in
        *previous-code*) printf '%s\\n' '{\"total_count\":1,\"items\":[{\"number\":11,\"title\":\"[ari-demo:v1:previous-code] fictional\"}]}' ;;
        *current-code*) printf '%s\\n' '{\"total_count\":1,\"items\":[{\"number\":12,\"title\":\"[ari-demo:v1:current-code] fictional\"}]}' ;;
        *release-operations*) printf '%s\\n' '{\"total_count\":1,\"items\":[{\"number\":13,\"title\":\"[ari-demo:v1:release-operations] fictional\"}]}' ;;
        *resolved-blocker*) printf '%s\\n' '{\"total_count\":1,\"items\":[{\"number\":14,\"title\":\"[ari-demo:v1:resolved-blocker] fictional\"}]}' ;;
        *) printf '%s\\n' '{\"total_count\":0,\"items\":[]}' ;;
      esac
      fi ;;
    *git/ref/heads*) printf '%s\\n' '{\"object\":{\"sha\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\"}}' ;;
    *milestones*) printf '%s\\n' '[{\"number\":1,\"title\":\"Fictional archive 1\"},{\"number\":2,\"title\":\"Fictional archive 2\"},{\"number\":3,\"title\":\"Fictional archive 3\"},{\"number\":4,\"title\":\"Fictional archive 4\"},{\"number\":5,\"title\":\"Fictional archive 5\"},{\"number\":6,\"title\":\"Release 2026.08.03\"},{\"number\":7,\"title\":\"Release 2026.08.10\"}]' ;;
    *check-runs*) printf '%s\\n' '{\"check_runs\":[{\"name\":\"blocking-suite\",\"status\":\"completed\",\"conclusion\":\"success\",\"html_url\":\"https://github.com/floppy522/ai-release-intelligence-demo/runs/7001\"}]}' ;;
    *pulls/*) printf '%s\\n' '{\"merged\":true,\"head\":{\"sha\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\"}}' ;;
    *) printf '%s\\n' '{}' ;;
  esac
fi
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def _run_seed(tmp_path: Path, **environment: str) -> subprocess.CompletedProcess[str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(parents=True)
    _write_fake_gh(fake_bin)
    log = tmp_path / "fake-gh.log"
    env = os.environ | {
        "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"],
        "FAKE_GH_LOG": str(log),
        **environment,
    }
    completed = subprocess.run(
        ["bash", str(SEED_SCRIPT), "floppy522/ai-release-intelligence-demo"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    completed.fake_log = log.read_text(encoding="utf-8") if log.exists() else ""  # type: ignore[attr-defined]
    return completed


def test_seed_is_idempotent_for_first_repeat_and_partial_state(tmp_path: Path) -> None:
    """The script converges with only create/update operations, never deletion."""

    first = _run_seed(tmp_path / "first")
    repeat = _run_seed(tmp_path / "repeat")
    partial = _run_seed(tmp_path / "partial")
    for result in (first, repeat, partial):
        assert result.returncode == 0, result.stderr
        assert "auth status --hostname github.com" in result.fake_log  # type: ignore[attr-defined]
        assert "issue edit" in result.fake_log  # type: ignore[attr-defined]
        assert "pr edit" in result.fake_log  # type: ignore[attr-defined]
        assert "delete" not in result.fake_log.lower()  # type: ignore[attr-defined]


def test_seed_refuses_wrong_owner_auth_failure_and_conflicting_duplicates(
    tmp_path: Path,
) -> None:
    """Unsafe targets or ambiguous stable markers fail before mutation."""

    wrong_owner = subprocess.run(
        ["bash", str(SEED_SCRIPT), "someone-else/ai-release-intelligence-demo"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert wrong_owner.returncode != 0
    auth_failure = _run_seed(tmp_path / "auth", FAKE_GH_AUTH="denied")
    assert auth_failure.returncode != 0
    assert "issue create" not in auth_failure.fake_log  # type: ignore[attr-defined]
    duplicate = _run_seed(
        tmp_path / "duplicate",
        FAKE_GH_SEARCH=json.dumps(
            {
                "total_count": 2,
                "items": [
                    {"number": 1, "title": "[ari-demo:v1:previous-code] fictional"},
                    {"number": 2, "title": "[ari-demo:v1:previous-code] fictional"},
                ],
            }
        ),
    )
    assert duplicate.returncode != 0
    assert "conflicting" in duplicate.stderr.lower()


def test_seed_rejects_manifest_injection_without_executing_it(tmp_path: Path) -> None:
    """Manifest scalars are validated data, never shell syntax."""

    unsafe_manifest = tmp_path / "unsafe.yaml"
    unsafe_manifest.write_text(
        MANIFEST_PATH.read_text(encoding="utf-8").replace(
            "release/2026-08-10", "release/2026-08-10; touch should-not-exist", 1
        ),
        encoding="utf-8",
    )
    result = _run_seed(tmp_path / "unsafe", ARI_SEED_MANIFEST=str(unsafe_manifest))
    assert result.returncode != 0
    assert not (tmp_path / "should-not-exist").exists()
    assert "issue create" not in result.fake_log  # type: ignore[attr-defined]


def test_seed_script_is_bash_safe_and_does_not_leak_credentials() -> None:
    """The public helper is strict, bounded, and does not echo auth material."""

    text = SEED_SCRIPT.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env bash\n")
    assert "set -euo pipefail" in text
    assert "eval " not in text
    assert "gh auth status --hostname github.com" in text
    assert "gh repo create" in text
    assert "gh issue create" in text
    assert "gh pr create" in text
    assert "GITHUB_TOKEN" not in text
    assert "Authorization:" not in text
    assert shutil.which("bash") is not None


def test_seed_uses_locked_python_and_validates_repository_before_mutation() -> None:
    """A system Python or private/mismatched repo must not reach mutation calls."""

    text = SEED_SCRIPT.read_text(encoding="utf-8")
    assert "uv run --project" in text
    assert "nameWithOwner,visibility" in text
    assert "repository visibility" in text
    assert "collaborators/${assignee}/permission" in text


def test_seed_converges_managed_issue_state_and_safe_cross_references() -> None:
    """A rerun must repair only managed state without auto-closing code Issues."""

    text = SEED_SCRIPT.read_text(encoding="utf-8")
    assert "gh issue reopen" in text
    assert "--remove-label" in text
    assert "Related to #%s" in text
    assert "Fixes #%s" not in text


def test_seed_ref_and_search_guards_are_complete_and_fail_closed() -> None:
    """Existing managed refs and page-two duplicates cannot silently win."""

    text = SEED_SCRIPT.read_text(encoding="utf-8")
    assert "/search/issues" in text
    assert "total_count" in text
    assert "expected managed ref" in text
    assert "commit.gpgSign=false" in text


def test_seed_waits_for_exact_successful_migration_check_before_ops_edit() -> None:
    """The release-ops migration URL must be observed, not invented."""

    text = SEED_SCRIPT.read_text(encoding="utf-8")
    assert "check-runs" in text
    assert "Migration evidence" in text
    assert "migration check" in text
