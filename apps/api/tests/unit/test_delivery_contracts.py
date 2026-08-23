from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path, PurePosixPath

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
    assert postgres["volumes"] == ["postgres_test_data:/var/lib/postgresql"]
    assert "/var/lib/postgresql/data" not in str(postgres["volumes"])
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

    production = _yaml("compose.yaml")
    production_services = production["services"]
    assert isinstance(production_services, dict)
    production_postgres = production_services["postgres"]
    assert isinstance(production_postgres, dict)
    assert production_postgres["volumes"] == ["postgres_data:/var/lib/postgresql"]
    assert "/var/lib/postgresql/data" not in str(production_postgres["volumes"])
    assert set(production["volumes"]) == {"postgres_data"}
    for name, service in production_services.items():
        assert isinstance(service, dict)
        if name != "postgres":
            assert "volumes" not in service


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


def test_api_runtime_venv_preserves_console_script_shebang_target() -> None:
    dockerfile = (ROOT / "apps/api/Dockerfile").read_text(encoding="utf-8")
    builder, runtime = re.split(
        r"(?m)^FROM .+ AS runtime$", dockerfile, maxsplit=1
    )
    workdir_match = re.search(r"(?m)^WORKDIR (\S+)$", builder)
    assert workdir_match is not None
    configured_venv = re.search(
        r"(?m)^\s*UV_PROJECT_ENVIRONMENT=(\S+?)(?:\s*\\)?$", builder
    )
    builder_venv = PurePosixPath(
        configured_venv.group(1)
        if configured_venv is not None
        else str(PurePosixPath(workdir_match.group(1)) / ".venv")
    )
    copy_match = re.search(
        r"(?m)^COPY --from=builder(?: --chown=\S+)? (\S+) (\S+)$", runtime
    )
    assert copy_match is not None
    copied_from = PurePosixPath(copy_match.group(1))
    runtime_venv = PurePosixPath(copy_match.group(2))

    assert copied_from == builder_venv
    assert runtime_venv == builder_venv


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


def test_compose_ci_jobs_use_failure_diagnostics_before_cleanup() -> None:
    workflow = _yaml(".github/workflows/ci.yml")
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    for job_name in ("playwright-e2e", "docker-smoke"):
        job = jobs[job_name]
        assert isinstance(job, dict)
        run_scripts = "\n".join(
            str(step.get("run", "")) for step in job["steps"] if isinstance(step, dict)
        )
        assert "ops/compose_cleanup.sh" in run_scripts


def test_compose_cleanup_reports_bounded_logs_without_dumping_secrets(
    tmp_path: Path,
) -> None:
    calls = tmp_path / "docker.calls"
    fake_docker = tmp_path / "docker"
    fake_docker.write_text(
        "#!/bin/sh\n" f"printf '%s\\n' \"$*\" >> {calls}\n", encoding="utf-8"
    )
    fake_docker.chmod(0o755)
    secret = "postgresql+asyncpg://user:do-not-print@postgres/database"
    environment = {
        **os.environ,
        "ARI_DATABASE_URL": secret,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
    }
    result = subprocess.run(
        ["sh", str(ROOT / "ops/compose_cleanup.sh"), "23"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 23
    invocation_text = calls.read_text(encoding="utf-8")
    assert invocation_text.splitlines() == [
        "compose -f compose.test.yaml ps --all",
        (
            "compose -f compose.test.yaml logs --no-color --timestamps --tail=200 "
            "postgres migrate api web"
        ),
        "compose -f compose.test.yaml down -v --remove-orphans",
    ]
    assert secret not in result.stdout + result.stderr + invocation_text

    calls.unlink()
    success = subprocess.run(
        ["sh", str(ROOT / "ops/compose_cleanup.sh"), "0"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert success.returncode == 0
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "compose -f compose.test.yaml down -v --remove-orphans"
    ]
    assert secret not in success.stdout + success.stderr


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
    assert "release_intelligence.benchmark.live_ai" in ai_text
    assert "responses.create" not in ai_text
    assert "python - <<" not in ai_text
    assert "deterministic-benchmark.json" not in ai_text
    assert "ai-claims" not in ai_text
    assert "unsupported_claim_rate" not in ai_text


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
