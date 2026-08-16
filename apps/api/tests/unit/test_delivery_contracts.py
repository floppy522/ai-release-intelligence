from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[4]
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


def _yaml(path: str) -> dict[str, object]:
    value = yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_compose_isolated_stack_uses_healthy_non_writable_services() -> None:
    document = _yaml("compose.test.yaml")
    services = document["services"]
    assert isinstance(services, dict)
    assert {"postgres", "migrate", "api", "web"} <= services.keys()

    postgres = services["postgres"]
    assert isinstance(postgres, dict)
    assert str(postgres["image"]).startswith("postgres:18-alpine")
    assert postgres["networks"] == ["database"]
    assert postgres["volumes"] == ["postgres_test_data:/var/lib/postgresql/data"]
    assert "healthcheck" in postgres

    api = services["api"]
    web = services["web"]
    migrate = services["migrate"]
    assert all(isinstance(service, dict) for service in (api, web, migrate))
    assert api["read_only"] is True
    assert web["read_only"] is True
    assert migrate["read_only"] is True
    assert api["environment"]["ENVIRONMENT"] == "e2e"
    assert api["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert api["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    assert web["depends_on"]["api"]["condition"] == "service_healthy"
    assert set(api["networks"]) == {"database", "edge"}
    assert web["networks"] == ["edge"]

    networks = document["networks"]
    assert networks["database"]["internal"] is True
    assert set(document["volumes"]) == {"postgres_test_data"}


def test_dockerfiles_are_multistage_non_root_and_exec_form() -> None:
    for relative in ("apps/api/Dockerfile", "apps/web/Dockerfile"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert len(re.findall(r"(?m)^FROM ", text)) >= 2
        assert re.search(r"(?m)^USER [1-9][0-9]*:[1-9][0-9]*$", text)
        assert re.search(r"(?m)^HEALTHCHECK ", text)
        assert re.search(r"(?m)^CMD \[", text)
        assert not re.search(
            r"(?im)^(?:ARG|ENV) .*?(?:TOKEN|PASSWORD|SECRET|PRIVATE_KEY)", text
        )


def test_ci_has_exact_blocking_jobs_and_sha_pinned_actions() -> None:
    workflow = _yaml(".github/workflows/ci.yml")
    assert workflow["permissions"] == {"contents": "read"}
    assert "concurrency" in workflow
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    assert set(jobs) == {
        "api-unit-static",
        "api-postgres-integration",
        "web-test-build",
        "security-benchmark",
        "playwright-e2e",
        "docker-smoke",
    }
    for job in jobs.values():
        assert isinstance(job, dict)
        assert 1 <= int(job["timeout-minutes"]) <= 30
        for step in job.get("steps", []):
            if "uses" not in step:
                continue
            _action, separator, revision = str(step["uses"]).rpartition("@")
            assert separator and FULL_SHA.fullmatch(revision)


def test_live_workflows_are_manual_secret_guarded_and_upload_safe_outputs() -> None:
    github = _yaml(".github/workflows/live-github-smoke.yml")
    ai = _yaml(".github/workflows/live-ai-benchmark.yml")
    for workflow in (github, ai):
        assert set(workflow["on"]) == {"workflow_dispatch"}
        assert workflow["permissions"] == {"contents": "read"}
        for job in workflow["jobs"].values():
            assert job["environment"] == "live-smoke"
            assert 1 <= int(job["timeout-minutes"]) <= 30
            for step in job["steps"]:
                if "uses" in step:
                    assert FULL_SHA.fullmatch(str(step["uses"]).rpartition("@")[2])

    github_text = (ROOT / ".github/workflows/live-github-smoke.yml").read_text()
    assert "floppy522/ai-release-intelligence-demo" in github_text
    for capability in (
        "installation",
        "milestone",
        "links",
        "checks",
        "candidate",
        "rate_limit",
    ):
        assert capability in github_text

    ai_text = (ROOT / ".github/workflows/live-ai-benchmark.yml").read_text()
    assert "gpt-5.6" in ai_text
    assert "44" in ai_text
    assert "aggregate" in ai_text.lower()
    assert "ai-claims" not in ai_text


def test_smoke_script_checks_health_api_and_web_without_verbose_curl(
    tmp_path: Path,
) -> None:
    calls = tmp_path / "curl.calls"
    fake_curl = tmp_path / "curl"
    fake_curl.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> {calls}\n"
        'case "$*" in\n'
        "  *healthz*) printf '%s' '{\"status\":\"ok\"}' ;;\n"
        "  *api/demo/analysis*) printf '%s' '{\"status\":\"NOT_READY\"}' ;;\n"
        "  *) printf '%s' '<!doctype html><title>Release intelligence</title>' ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)
    environment = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "SMOKE_BASE_URL": "http://stack.test",
        "SMOKE_ATTEMPTS": "1",
        "SMOKE_TIMEOUT_SECONDS": "1",
    }
    result = subprocess.run(
        ["sh", str(ROOT / "ops/smoke.sh")],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    invocation_text = calls.read_text(encoding="utf-8")
    assert "http://stack.test/healthz" in invocation_text
    assert "http://stack.test/api/demo/analysis" in invocation_text
    assert "http://stack.test/" in invocation_text
    assert " -v " not in f" {invocation_text} "
