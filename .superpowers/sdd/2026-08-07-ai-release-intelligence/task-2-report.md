# Task 2 Report: PostgreSQL Persistence and Immutable Analysis Runs

## Status

**BLOCKED** — this environment has no PostgreSQL 18 runtime or supported
container runtime. Per the task constraint, PostgreSQL integration tests must
not silently use SQLite or another substitute, so no persistence implementation
or migration was started.

## Implemented behavior

None. Task 2's required PostgreSQL-backed atomic create and immutable audit
records were deliberately not implemented without a PostgreSQL 18 test target.
Task 1 behavior was left unchanged.

## Files changed

- `.superpowers/sdd/2026-08-07-ai-release-intelligence/task-2-report.md`

## Environment evidence

The required compose command cannot start the specified PostgreSQL service:

```bash
cd /workspace/scratch/4ce8811ebbe2/ai-release-intelligence/.worktrees/ai-release-intelligence
docker --version
docker compose version
```

Observed output for each command:

```text
/bin/bash: docker: command not found
```

Both commands exit with status 127. The available alternatives and native
PostgreSQL binaries were also checked:

```bash
command -v docker || true
command -v podman || true
command -v psql || true
command -v pg_config || true
command -v postgres || true
command -v initdb || true
find /usr -type f \( -name postgres -o -name psql \) 2>/dev/null | head -20
find /var/run/postgresql -maxdepth 1 -type s 2>/dev/null || true
```

Observed result: no executable paths, PostgreSQL binaries, or local PostgreSQL
socket. The only filesystem match was a Bash-completion definition:
`/usr/share/bash-completion/completions/psql`.

## RED cycles

Not started. The exact required prerequisite command is blocked before the
integration-test RED phase:

```bash
docker compose -f compose.test.yaml up -d postgres
```

Expected/observed failure in this environment:

```text
/bin/bash: docker: command not found
```

Creating a SQLite-backed test or repository to make progress would violate the
explicit PostgreSQL 18-only test requirement.

## GREEN, migration, and regression commands

Not run, because no PostgreSQL 18 service is available:

```bash
cd apps/api
uv run alembic upgrade head
uv run pytest tests/integration/test_analysis_repository.py -v
uv run alembic downgrade base
uv run alembic upgrade head
uv run pytest tests/unit/test_vertical_slice.py tests/unit/test_api_demo_analysis.py -v -W error
```

## Self-review

- No SQLite or in-memory persistence substitute was introduced.
- No public Task 1 contracts or status vocabulary were changed.
- No persistence migrations, models, repositories, or integration tests were
  added without the ability to prove PostgreSQL 18 behavior.
- The working tree was clean before this report was added; no unrelated changes
  were touched.

## Concerns / unblock requirement

Provide a Docker/Compose-compatible runtime capable of starting the requested
PostgreSQL 18 service, or a reachable PostgreSQL 18 instance plus its connection
details. Then Task 2 can be implemented through the required observed
PostgreSQL RED/GREEN cycles.

## Unblocked implementation attempt

The approved PostgreSQL 18.4 package mechanism was evaluated without using a
database substitute. It could not be started in this sandbox because the
existing `nobody` account is not mapped in the sandbox's user namespace.

### Package and binary evidence

The package was installed only beneath one task-specific temporary directory
under `/tmp`. Before installation, registry integrity metadata was fetched with:

```bash
npm --cache "$pg_task_dir/.npm-cache" view embedded-postgres@18.4.0-beta.17 dist.integrity --json
```

The returned integrity metadata was saved only inside that temporary directory.
`npm --cache "$pg_task_dir/.npm-cache" install --ignore-scripts --no-audit
--no-fund embedded-postgres@18.4.0-beta.17` completed successfully, and the
installed manifest reported `embedded-postgres@18.4.0-beta.17`. The package
contained native Linux x64 `initdb`, `pg_ctl`, and `postgres` binaries.

### User-namespace blocker

The required existing-user startup did not work. These commands provided the
relevant evidence:

```bash
id
cat /proc/self/uid_map
cat /proc/self/gid_map
setpriv --reuid=65534 --regid=65534 --clear-groups -- id
runuser -u nobody -- id
```

Observed results:

```text
uid=0(root) gid=0(root) groups=0(root)
0 0 1
0 0 1
setpriv: setresuid failed: Invalid argument
runuser: cannot set groups: Operation not permitted
```

The attempted data-directory ownership change also failed with `Invalid
argument`. Since PostgreSQL refuses to run as root, and the task explicitly
forbids `createPostgresUser`, spoofing or patching root checks, SQLite, PGlite,
and fakes, no server could be started as the required account.

Consequently `SELECT version()` could not be run and PostgreSQL 18.4 behavior
could not be proven. No Task 2 RED/GREEN test, migration round-trip, or Task 1
regression command was run after this blocker; doing so would not satisfy the
required PostgreSQL-backed verification.

### Cleanup and repository state

No PostgreSQL server started: no `postmaster.pid` was created. The exact
task-specific temporary package/data directory, its npm cache, and the local
task marker were removed after the failed startup attempt. No external or Neon
resources were accessed or mutated. The transient `uv add` dependency changes
were reverted; no Task 2 application, migration, test, or configuration files
remain changed.

## Remote TDD RED phase

### Status

**READY FOR REMOTE RED** — this phase defines the PostgreSQL contract and the
minimal GitHub Actions harness only. It does not claim a remote result; the
controller must push the commit and inspect the workflow run.

### Files

- `apps/api/pyproject.toml` and `apps/api/uv.lock`: added only the test/runtime
  dependencies required for the Task 2 PostgreSQL contract (`alembic`,
  `asyncpg`, `sqlalchemy`, and `pytest-asyncio`) and enabled async pytest.
- `apps/api/tests/integration/test_analysis_repository.py`: real-PostgreSQL
  integration contracts. `DATABASE_URL` is mandatory, accepts only PostgreSQL
  URLs, runs `alembic upgrade head`, and connects with `asyncpg`; it contains no
  SQLite, fake, or skip path.
- `compose.test.yaml`: planned local `postgres:18` service with a health check.
- `.github/workflows/task-2-postgres.yml`: temporary push-only workflow for
  `feature/ai-release-intelligence`, with read-only contents permission, Python
  3.13, a healthy `postgres:18` service, `uv sync`, and exactly
  `uv run pytest tests/integration/test_analysis_repository.py -v`.

No Alembic configuration or migrations, persistence models, repository
implementation, or production persistence interface was added in this phase.

### Protected behaviors

- A successful `create_run` must insert one analysis run, one snapshot, and one
  finding as a single PostgreSQL-backed result.
- An invalid evidence-free finding must leave no snapshot or finding rows. A
  separately committed failure audit row is allowed only as the single
  `FAILED` analysis-run row, never as a partial report.
- `replace_snapshot` must raise `ImmutableSnapshotError`, leaving the original
  stored source state unchanged.
- `get_run` must reproduce the run ID, snapshot, findings, assessment,
  `policy_version`, and timezone-aware `source_fetched_at` exactly.

### Local RED evidence

The local environment still has no `DATABASE_URL` or Docker/PostgreSQL runtime.
The test nevertheless reached the intended first missing production boundary
before attempting a database connection:

```bash
cd /workspace/scratch/4ce8811ebbe2/ai-release-intelligence/.worktrees/ai-release-intelligence/apps/api
UV_CACHE_DIR=/tmp/ai-release-intelligence-caches/uv UV_PYTHON_INSTALL_DIR=/tmp/ai-release-intelligence-caches/uv-python uv run pytest tests/integration/test_analysis_repository.py -v
```

Observed output (exit 2):

```text
collected 0 items / 1 error
E   ModuleNotFoundError: No module named 'release_intelligence.adapters.persistence'
```

This is the required RED cause: the repository adapter does not exist. It is
not a dependency, YAML, health-check, or database-readiness failure.

The strongest non-PostgreSQL checks that can run locally were:

```bash
cd /workspace/scratch/4ce8811ebbe2/ai-release-intelligence/.worktrees/ai-release-intelligence/apps/api
UV_CACHE_DIR=/tmp/ai-release-intelligence-caches/uv UV_PYTHON_INSTALL_DIR=/tmp/ai-release-intelligence-caches/uv-python uv lock --check && UV_CACHE_DIR=/tmp/ai-release-intelligence-caches/uv UV_PYTHON_INSTALL_DIR=/tmp/ai-release-intelligence-caches/uv-python uv run pytest tests/unit -v -W error && UV_CACHE_DIR=/tmp/ai-release-intelligence-caches/uv UV_PYTHON_INSTALL_DIR=/tmp/ai-release-intelligence-caches/uv-python uv run ruff check src tests && UV_CACHE_DIR=/tmp/ai-release-intelligence-caches/uv UV_PYTHON_INSTALL_DIR=/tmp/ai-release-intelligence-caches/uv-python uv run mypy
```

Observed output (exit 0):

```text
Resolved 41 packages in 0.60ms
3 passed in 0.19s
All checks passed!
Success: no issues found in 7 source files
```

The focused integration file also passed Ruff before the expected RED command:

```bash
UV_CACHE_DIR=/tmp/ai-release-intelligence-caches/uv UV_PYTHON_INSTALL_DIR=/tmp/ai-release-intelligence-caches/uv-python uv run ruff check tests/integration/test_analysis_repository.py
```

Observed output (exit 0): `All checks passed!`

### Expected remote result and concern

After the controller pushes this commit, GitHub Actions should complete
dependency synchronization and PostgreSQL 18 health checking, then fail the
focused contract at collection with
`ModuleNotFoundError: No module named 'release_intelligence.adapters.persistence'`.
That result proves the desired RED boundary only after controller-provided
workflow evidence. The next phase must add the missing production persistence
adapter and migrations, then run this unchanged suite against the service.
