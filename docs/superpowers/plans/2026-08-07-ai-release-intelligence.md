# AI Release Intelligence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a portfolio-grade, evidence-backed GitHub release-readiness gate that turns a Milestone and release-candidate branch into a deterministic Go/No-Go report in no more than five minutes.

**Architecture:** Use a modular monolith: a FastAPI backend owns GitHub ingestion, normalization, rules, decisions, persistence, and optional AI explanation; a React client renders a decision-first report; PostgreSQL stores configuration, immutable snapshots, findings, and audit decisions. The first increment is a fixture-backed vertical slice, then adapters and rules replace the fixture without changing domain interfaces.

**Tech Stack:** Python 3.13, FastAPI, Pydantic, SQLAlchemy 2, Alembic, PostgreSQL 18, httpx, PyJWT/cryptography, OpenAI Responses API, React, TypeScript, Vite, TanStack Query, Vitest, Testing Library, Playwright, Docker Compose, GitHub Actions, uv, pnpm 11.

## Global Constraints

- One GitHub repository, one Milestone as scope, and one `release/YYYY-MM-DD` branch as the candidate/CI target.
- Status is exactly `READY`, `NOT_READY`, `NEEDS_DECISION`, or `INSUFFICIENT_DATA`; no numeric readiness score.
- A snapshot older than ten minutes cannot produce `READY`.
- The LLM cannot set status, severity, decisions, or evidence and must time out after 15 seconds.
- Raw Issue/PR bodies, comments, source code, full CI logs, and secrets never enter an AI request.
- The GitHub App is read-only: Metadata, Issues, Pull requests, Checks, Commit statuses, and Contents.
- No webhooks, scheduler, Redis, Celery, Kafka, vector database, Kubernetes, microservices, chat, or notification integration in MVP.
- Deterministic benchmark profile: at most 100 milestone items, 200 related PRs, and 100 candidate-SHA checks.
- Critical-blocker recall and evidence coverage target 100%; unsupported AI claims and invalid evidence references target zero.
- Use TDD for every behavior change and commit after every independently reviewable task.
- Do not claim a task works until its exact tests, lint, typecheck, or build command has completed successfully in the current turn.

## Locked File Structure

```text
apps/
  api/
    pyproject.toml
    alembic.ini
    alembic/
    src/release_intelligence/
      main.py
      config.py
      api/
        dependencies.py
        schemas.py
        routes/auth.py
        routes/repositories.py
        routes/releases.py
        routes/decisions.py
        routes/explanations.py
      application/
        analyze_release.py
        decisions.py
        explanations.py
      domain/
        models.py
        policy.py
        assessment.py
        rules/scope.py
        rules/checks.py
        rules/blockers.py
        rules/operations.py
        rules/backmerge.py
      ports/
        github.py
        ai.py
        repositories.py
      adapters/
        fixtures/github_source.py
        github/auth.py
        github/client.py
        github/mapper.py
        ai/openai_provider.py
        persistence/models.py
        persistence/repositories.py
      security/
        urls.py
        logging.py
        crypto.py
      benchmark/
        schema.py
        runner.py
    tests/
      unit/
      integration/
      contract/
      security/
      fixtures/github/
  web/
    package.json
    eslint.config.js
    src/
      app/App.tsx
      api/client.ts
      api/types.ts
      features/setup/
      features/report/
      features/decisions/
      styles/tokens.css
      styles/global.css
      test/
        render.tsx
        setup.ts
tests/e2e/
benchmarks/scenarios/catalog.yaml
demo/repository/
docs/
  product-case.md
  threat-model.md
  data-retention.md
  runbook.md
  experiments/human-vs-tool.md
  adr/
ops/
  smoke.sh
.github/workflows/
  ci.yml
  live-github-smoke.yml
  live-ai-benchmark.yml
compose.yaml
compose.test.yaml
.env.example
README.md
```

## Task 1: Fixture-Backed End-to-End Vertical Slice

**Files:**
- Create: `apps/api/pyproject.toml`
- Create: `apps/api/src/release_intelligence/domain/models.py`
- Create: `apps/api/src/release_intelligence/domain/assessment.py`
- Create: `apps/api/src/release_intelligence/adapters/fixtures/github_source.py`
- Create: `apps/api/src/release_intelligence/application/analyze_release.py`
- Create: `apps/api/src/release_intelligence/api/schemas.py`
- Create: `apps/api/src/release_intelligence/main.py`
- Create: `apps/api/tests/unit/test_vertical_slice.py`
- Create: `apps/api/tests/unit/test_api_demo_analysis.py`
- Create: `apps/web/package.json`
- Create: `apps/web/eslint.config.js`
- Create: `apps/web/src/api/types.ts`
- Create: `apps/web/src/api/client.ts`
- Create: `apps/web/src/app/App.tsx`
- Create: `apps/web/src/app/App.test.tsx`
- Create: `apps/web/src/main.tsx`
- Create: `apps/web/src/styles/global.css`
- Create: `apps/web/src/test/render.tsx`
- Create: `apps/web/src/test/setup.ts`
- Create: `apps/web/index.html`
- Create: `apps/web/vite.config.ts`
- Create: `apps/web/tsconfig.json`

**Interfaces:**
- Produces: `ReleaseSnapshot`, `EvidenceRef`, `ReadinessFinding`, `ReadinessAssessment`, `assess_fixture_release() -> ReadinessAssessment`, and `GET /api/demo/analysis`.
- The fixture contains Issue `#142`, label `code-change`, no linked PR, and GitHub evidence URL.

- [ ] **Step 1: Bootstrap only the dependency manifests**

Run:

```bash
mkdir -p apps/api/src/release_intelligence apps/api/tests/unit apps/web/src/{api,app,styles}
cd apps/api
uv init --bare --python 3.13
uv add fastapi pydantic uvicorn
uv add --dev pytest httpx ruff mypy hatchling
cd ../web
pnpm init
pnpm add react react-dom @tanstack/react-query
pnpm add -D typescript vite @vitejs/plugin-react vitest jsdom @testing-library/react @testing-library/jest-dom @types/react @types/react-dom eslint @eslint/js typescript-eslint eslint-plugin-react-hooks
```

Configure `apps/api/pyproject.toml` with a Hatchling build backend, package path `src/release_intelligence`, Python `>=3.13`, strict mypy, Ruff, and pytest paths. Add `test`, `lint`, `typecheck`, and `build` scripts to `apps/web/package.json`; point Vitest at jsdom and `src/test/setup.ts`; configure ESLint for typed TypeScript and React hooks. `render.tsx` must create a fresh `QueryClient` per test and wrap components in `QueryClientProvider`.

Expected: manifests, lockfiles, and test/lint configuration exist; no application behavior exists.

- [ ] **Step 2: Write the failing domain test**

```python
from release_intelligence.application.analyze_release import assess_fixture_release
from release_intelligence.domain.models import ReleaseStatus


def test_missing_pr_blocks_demo_release() -> None:
    assessment = assess_fixture_release()

    assert assessment.status is ReleaseStatus.NOT_READY
    assert assessment.findings[0].rule_id == "scope.code_change_requires_pr"
    assert assessment.findings[0].evidence[0].url.endswith("/issues/142")
```

- [ ] **Step 3: Run the domain test and confirm red**

Run: `cd apps/api && uv run pytest tests/unit/test_vertical_slice.py -v`

Expected: FAIL because `release_intelligence.application.analyze_release` does not exist.

- [ ] **Step 4: Implement the minimal immutable domain and one rule**

```python
class ReleaseStatus(StrEnum):
    READY = "READY"
    NOT_READY = "NOT_READY"
    NEEDS_DECISION = "NEEDS_DECISION"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True)
class EvidenceRef:
    evidence_id: str
    source_type: str
    source_id: str
    url: str
    fingerprint: str


@dataclass(frozen=True)
class ReadinessFinding:
    rule_id: str
    severity: str
    summary: str
    required_action: str
    evidence: tuple[EvidenceRef, ...]


@dataclass(frozen=True)
class ReadinessAssessment:
    status: ReleaseStatus
    findings: tuple[ReadinessFinding, ...]
```

Implement `assess_fixture_release()` so the missing PR finding is the sole blocker.

- [ ] **Step 5: Run the domain test and confirm green**

Run: `cd apps/api && uv run pytest tests/unit/test_vertical_slice.py -v`

Expected: PASS.

- [ ] **Step 6: Write the failing API contract test**

```python
def test_demo_analysis_returns_evidence_backed_status() -> None:
    with TestClient(app) as client:
        response = client.get("/api/demo/analysis")

    assert response.status_code == 200
    assert response.json()["status"] == "NOT_READY"
    assert response.json()["findings"][0]["evidence"][0]["source_id"] == "142"
```

- [ ] **Step 7: Run the API test and confirm red**

Run: `cd apps/api && uv run pytest tests/unit/test_api_demo_analysis.py -v`

Expected: FAIL because the FastAPI route does not exist.

- [ ] **Step 8: Add the minimal API route and schema**

```python
app = FastAPI(title="AI Release Intelligence")


@app.get("/api/demo/analysis", response_model=AssessmentResponse)
def get_demo_analysis() -> ReadinessAssessment:
    return assess_fixture_release()
```

Run: `cd apps/api && uv run pytest tests/unit/test_api_demo_analysis.py -v`

Expected: PASS.

- [ ] **Step 9: Write the failing UI test**

```tsx
it("shows the verdict, blocker, action, and evidence link", async () => {
  vi.mocked(getDemoAnalysis).mockResolvedValue(NOT_READY_FIXTURE);
  renderWithQueryClient(<App />);
  expect(await screen.findByText("NOT READY")).toBeInTheDocument();
  expect(screen.getByText("Issue #142 has no linked PR")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Open evidence" })).toHaveAttribute(
    "href",
    "https://github.com/example/release-demo/issues/142",
  );
});
```

- [ ] **Step 10: Run the UI test and confirm red**

Run: `cd apps/web && pnpm test -- src/app/App.test.tsx`

Expected: FAIL because `App` does not render the report.

- [ ] **Step 11: Implement the minimal decision-first UI**

Render only the release name, status badge, first blocker, required action, and evidence link. Do not add navigation, charts, or a score.

```tsx
export function App() {
  const query = useQuery({ queryKey: ["demo-analysis"], queryFn: getDemoAnalysis });
  if (query.isPending) return <main>Analyzing release…</main>;
  if (query.isError) return <main>Analysis unavailable</main>;
  const finding = query.data.findings[0];
  return (
    <main>
      <h1>Release 2026.08.10</h1>
      <strong>NOT READY</strong>
      <h2>{finding.summary}</h2>
      <p>{finding.required_action}</p>
      <a href={finding.evidence[0].url}>Open evidence</a>
    </main>
  );
}
```

- [ ] **Step 12: Verify the complete first slice**

Run:

```bash
cd apps/api && uv run pytest -v && uv run ruff check src tests && uv run mypy
cd ../web && pnpm test && pnpm lint && pnpm typecheck && pnpm build
```

Expected: all commands exit 0.

- [ ] **Step 13: Commit the vertical slice**

```bash
git add apps/api apps/web
git commit -m "feat: add fixture-backed release readiness slice"
```

## Task 2: PostgreSQL Persistence and Immutable Analysis Runs

**Files:**
- Modify: `apps/api/pyproject.toml`
- Create: `apps/api/alembic.ini`
- Create: `apps/api/alembic/env.py`
- Create: `apps/api/alembic/versions/0001_initial.py`
- Create: `apps/api/src/release_intelligence/adapters/persistence/models.py`
- Create: `apps/api/src/release_intelligence/adapters/persistence/repositories.py`
- Create: `apps/api/src/release_intelligence/ports/repositories.py`
- Create: `apps/api/tests/integration/test_analysis_repository.py`
- Create: `compose.test.yaml`

**Interfaces:**
- Consumes: Task 1 domain objects.
- Produces: `AnalysisRepository.create_run(snapshot, findings, assessment) -> UUID`, `AnalysisRepository.get_run(run_id) -> StoredAnalysisRun`, and tables `repository_connections`, `release_policies`, `analysis_runs`, `release_snapshots`, `readiness_findings`, `human_decisions`, `ai_explanations`, `web_sessions`.

- [ ] **Step 1: Add persistence dependencies**

Run:

```bash
cd apps/api
uv add sqlalchemy asyncpg alembic pydantic-settings cryptography
uv add --dev pytest-asyncio
```

- [ ] **Step 2: Write failing integration tests for atomic create and immutability**

```python
async def test_create_run_persists_snapshot_findings_and_status(repository, fixture_run):
    run_id = await repository.create_run(**fixture_run)
    stored = await repository.get_run(run_id)
    assert stored.assessment.status is ReleaseStatus.NOT_READY
    assert stored.snapshot.milestone_number == 7
    assert stored.findings[0].evidence[0].source_id == "142"


async def test_snapshot_cannot_be_updated(repository, fixture_run):
    run_id = await repository.create_run(**fixture_run)
    with pytest.raises(ImmutableSnapshotError):
        await repository.replace_snapshot(run_id, fixture_run["snapshot"])
```

- [ ] **Step 3: Run the integration tests and confirm red**

Run:

```bash
docker compose -f compose.test.yaml up -d postgres
cd apps/api && uv run pytest tests/integration/test_analysis_repository.py -v
```

Expected: FAIL because repository classes and migrations do not exist.

- [ ] **Step 4: Implement explicit SQLAlchemy models and migration**

Use UUID primary keys, timezone-aware timestamps, JSONB only for normalized snapshot payloads, foreign keys with explicit delete behavior, and a unique `(repository_id, github_milestone_number)` release identity. Store `policy_version` and `source_fetched_at` on every run.

```python
class AnalysisRunRow(Base):
    __tablename__ = "analysis_runs"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    release_id: Mapped[UUID] = mapped_column(ForeignKey("releases.id"), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    assessment_status: Mapped[str | None] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

- [ ] **Step 5: Implement transaction-scoped repositories**

`create_run` inserts run, snapshot, findings, and evidence in one transaction. On any exception, roll back the transaction and leave a separately created failed-run audit row only when the failure occurred before snapshot persistence.

- [ ] **Step 6: Apply migrations and confirm green**

Run:

```bash
cd apps/api
uv run alembic upgrade head
uv run pytest tests/integration/test_analysis_repository.py -v
uv run alembic downgrade base
uv run alembic upgrade head
```

Expected: all commands exit 0.

- [ ] **Step 7: Commit persistence**

```bash
git add apps/api compose.test.yaml
git commit -m "feat: persist immutable release analysis runs"
```

## Task 3: GitHub App Authentication and Repository Authorization

**Files:**
- Create: `apps/api/src/release_intelligence/config.py`
- Create: `apps/api/src/release_intelligence/adapters/github/auth.py`
- Create: `apps/api/src/release_intelligence/api/routes/auth.py`
- Create: `apps/api/src/release_intelligence/api/dependencies.py`
- Create: `apps/api/src/release_intelligence/security/crypto.py`
- Create: `apps/api/tests/unit/test_github_app_auth.py`
- Create: `apps/api/tests/integration/test_auth_routes.py`
- Create: `.env.example`

**Interfaces:**
- Produces: `GitHubAppTokenProvider.installation_token(installation_id) -> SecretStr`, `CurrentUser`, encrypted user credential storage, session cookie authentication, and `require_repository_access(user_id, repository_id)`.

- [ ] **Step 1: Write failing JWT and token-lifetime tests**

```python
def test_app_jwt_uses_ten_minute_maximum_lifetime(token_provider, frozen_time):
    claims = decode_without_verification(token_provider.create_app_jwt())
    assert claims["exp"] - claims["iat"] <= 600


def test_installation_token_is_not_persisted(token_provider, fake_github):
    token_provider.installation_token(123)
    assert token_provider.persistence.saved_tokens == []
```

- [ ] **Step 2: Run auth unit tests and confirm red**

Run: `cd apps/api && uv run pytest tests/unit/test_github_app_auth.py -v`

Expected: FAIL because the provider does not exist.

- [ ] **Step 3: Implement GitHub App JWT and installation exchange**

```python
class GitHubAppTokenProvider:
    async def installation_token(self, installation_id: int) -> SecretStr:
        response = await self._client.post(
            f"/app/installations/{installation_id}/access_tokens",
            headers={"Authorization": f"Bearer {self.create_app_jwt()}"},
        )
        response.raise_for_status()
        return SecretStr(response.json()["token"])
```

Keep the installation token in request memory only.

- [ ] **Step 4: Write failing login/session/authorization tests**

```python
async def test_repository_access_rejects_different_installation(client, user_session):
    response = await client.get(
        "/api/repositories/other-owner/private-repo",
        cookies=user_session.cookies,
    )
    assert response.status_code == 403
```

- [ ] **Step 5: Implement OAuth callback, encrypted user token, and session cookie**

The callback validates `state`, encrypts the user token with Fernet/AES-GCM application key, creates a server-side session, and returns only `HttpOnly`, `Secure`, `SameSite=Lax` cookie metadata. Add CSRF token validation to POST/PUT/DELETE routes.

- [ ] **Step 6: Verify auth and commit**

Run:

```bash
cd apps/api
uv run pytest tests/unit/test_github_app_auth.py tests/integration/test_auth_routes.py -v
uv run ruff check src tests
uv run mypy
```

Expected: all commands exit 0.

```bash
git add apps/api .env.example
git commit -m "feat: authenticate through a least-privilege GitHub App"
```

## Task 4: Typed GitHub REST Client and Contract Fixtures

**Files:**
- Create: `apps/api/src/release_intelligence/ports/github.py`
- Create: `apps/api/src/release_intelligence/adapters/github/client.py`
- Create: `apps/api/src/release_intelligence/adapters/github/mapper.py`
- Create: `apps/api/tests/contract/test_github_client.py`
- Create: `apps/api/tests/fixtures/github/milestone_items.json`
- Create: `apps/api/tests/fixtures/github/issue_timeline.json`
- Create: `apps/api/tests/fixtures/github/pull_request.json`
- Create: `apps/api/tests/fixtures/github/check_runs.json`
- Create: `apps/api/tests/fixtures/github/compare_commits.json`

**Interfaces:**
- Produces: `GitHubSource` protocol methods `get_milestone`, `list_milestone_items`, `list_issue_timeline`, `get_pull_request`, `list_checks_for_ref`, and `compare_commits`; raises `GitHubRateLimited`, `GitHubUnauthorized`, `GitHubNotFound`, or `GitHubPartialData`.

- [ ] **Step 1: Define the port and failing pagination/rate-limit tests**

```python
class GitHubSource(Protocol):
    async def list_milestone_items(self, repo: RepoRef, milestone: int) -> tuple[GitHubItem, ...]: ...
    async def list_checks_for_ref(self, repo: RepoRef, ref: str) -> tuple[GitHubCheck, ...]: ...
    async def compare_commits(self, repo: RepoRef, base: str, head: str) -> CommitComparison: ...
```

```python
async def test_client_follows_link_header_and_preserves_rate_limit(fake_http, github_client):
    items = await github_client.list_milestone_items(REPO, 7)
    assert [item.number for item in items] == [141, 142]
    assert github_client.rate_limit.remaining == 4997
```

- [ ] **Step 2: Run contract tests and confirm red**

Run: `cd apps/api && uv run pytest tests/contract/test_github_client.py -v`

Expected: FAIL because client and types do not exist.

- [ ] **Step 3: Implement REST client with bounded pagination**

Set 10-second connect/read timeouts, maximum 20 pages per endpoint, API version header, typed error mapping, and capture of `X-RateLimit-Remaining`/`X-RateLimit-Reset`. Stop immediately on 403/429 rate-limit responses.

- [ ] **Step 4: Implement fixture mappers**

Map only source IDs, numbers, URLs, state, labels, assignees, milestone, branch refs, SHAs, check status/conclusion, and timestamps. Do not map comments, bodies, logs, or repository contents.

- [ ] **Step 5: Verify and commit**

Run:

```bash
cd apps/api
uv run pytest tests/contract/test_github_client.py -v
uv run ruff check src tests
uv run mypy
```

Expected: all commands exit 0.

```bash
git add apps/api
git commit -m "feat: add typed read-only GitHub client"
```

## Task 5: Release Loader, Snapshot Completeness, and Analysis API

**Files:**
- Modify: `apps/api/src/release_intelligence/domain/models.py`
- Modify: `apps/api/src/release_intelligence/application/analyze_release.py`
- Create: `apps/api/src/release_intelligence/api/routes/releases.py`
- Create: `apps/api/tests/unit/test_release_loader.py`
- Create: `apps/api/tests/integration/test_analysis_route.py`

**Interfaces:**
- Produces: `GitHubReleaseLoader.load(request: AnalysisRequest) -> ReleaseSnapshot`, `AnalysisService.run(request, actor) -> UUID`, `POST /api/analyses`, and `GET /api/analyses/{run_id}`.

- [ ] **Step 1: Write failing tests for complete, partial, and stale snapshots**

```python
async def test_partial_github_fetch_cannot_produce_ready(loader, partial_source):
    snapshot = await loader.load(REQUEST)
    assessment = assess(snapshot, POLICY, decisions=(), now=NOW)
    assert snapshot.complete is False
    assert assessment.status is ReleaseStatus.INSUFFICIENT_DATA


def test_snapshot_older_than_ten_minutes_is_insufficient(complete_snapshot):
    assessment = assess(complete_snapshot, POLICY, (), now=complete_snapshot.fetched_at + timedelta(minutes=11))
    assert assessment.status is ReleaseStatus.INSUFFICIENT_DATA
```

- [ ] **Step 2: Run tests and confirm red**

Run: `cd apps/api && uv run pytest tests/unit/test_release_loader.py -v`

Expected: FAIL because completeness and age rules do not exist.

- [ ] **Step 3: Implement loader and snapshot window metadata**

`ReleaseSnapshot` records `fetch_started_at`, `fetched_at`, `complete`, `source_errors`, `milestone_number`, `candidate_ref`, `candidate_sha`, items, links, checks, and comparisons. If GitHub state changes across fetches and cannot be reconciled once, set `complete=False` with a typed inconsistency error.

- [ ] **Step 4: Write failing route tests**

```python
async def test_post_analysis_returns_persisted_run_id(client, authorized_session):
    response = await client.post(
        "/api/analyses",
        json={"repository_id": str(REPOSITORY_ID), "milestone_number": 7, "candidate_ref": "release/2026-08-10"},
        cookies=authorized_session.cookies,
        headers=authorized_session.csrf_header,
    )
    assert response.status_code == 202
    assert UUID(response.json()["run_id"])
```

- [ ] **Step 5: Implement analysis routes and explicit error responses**

Map unauthorized to 403, missing milestone/branch to 422, GitHub rate limit to a persisted `INSUFFICIENT_DATA` run with reset time, and database failure to 503 after rollback.

- [ ] **Step 6: Verify and commit**

Run:

```bash
cd apps/api
uv run pytest tests/unit/test_release_loader.py tests/integration/test_analysis_route.py -v
uv run ruff check src tests
uv run mypy
```

Expected: all commands exit 0.

```bash
git add apps/api
git commit -m "feat: load and persist normalized release snapshots"
```

## Task 6: Release Policy Configuration and Setup Flow

**Files:**
- Create: `apps/api/src/release_intelligence/domain/policy.py`
- Create: `apps/api/src/release_intelligence/api/routes/repositories.py`
- Create: `apps/api/tests/unit/test_release_policy.py`
- Create: `apps/api/tests/integration/test_policy_routes.py`
- Create: `apps/web/src/features/setup/ReleaseSetup.tsx`
- Create: `apps/web/src/features/setup/ReleaseSetup.test.tsx`
- Modify: `apps/web/src/api/types.ts`
- Modify: `apps/web/src/api/client.ts`

**Interfaces:**
- Produces: `ReleasePolicy`, check category enum `BLOCKING|ADVISORY|IGNORED`, `GET/PUT /api/repositories/{id}/policy`, and setup payload containing main branch, label mapping, check mapping, candidate branch, previous milestone, and previous release branch.

- [ ] **Step 1: Write failing policy invariant tests**

```python
def test_policy_rejects_duplicate_issue_type_labels():
    with pytest.raises(PolicyValidationError):
        ReleasePolicy(code_change_label="code-change", release_ops_label="code-change", **BASE_POLICY)


def test_every_discovered_check_requires_a_category():
    with pytest.raises(UnknownCheckPolicyError):
        validate_check_policy(POLICY, discovered={"api", "new-security-scan"})
```

- [ ] **Step 2: Run tests red, implement Pydantic policy, run green**

Run: `cd apps/api && uv run pytest tests/unit/test_release_policy.py -v`

Expected before implementation: FAIL. Expected after implementation: PASS.

- [ ] **Step 3: Write failing API and UI setup tests**

```tsx
it("requires labels, candidate branch, and a category for each check", async () => {
  render(<ReleaseSetup repositoryId="repo-1" />);
  await user.click(await screen.findByRole("button", { name: "Save policy" }));
  expect(screen.getByText("Classify every discovered check")).toBeInTheDocument();
});
```

- [ ] **Step 4: Implement minimal setup form and persistence**

The form contains no advanced accordion: repository, milestone, candidate branch, three issue labels, discovered checks with category selects, and optional previous-release fields. The backend versions every policy update.

- [ ] **Step 5: Verify and commit**

Run:

```bash
cd apps/api && uv run pytest tests/unit/test_release_policy.py tests/integration/test_policy_routes.py -v
cd ../web && pnpm vitest run src/features/setup/ReleaseSetup.test.tsx && pnpm tsc --noEmit
```

Expected: all commands exit 0.

```bash
git add apps/api apps/web
git commit -m "feat: configure repository release policy"
```

## Task 7: Scope Completeness Rules

**Files:**
- Create: `apps/api/src/release_intelligence/domain/rules/scope.py`
- Create: `apps/api/tests/unit/rules/test_scope_rules.py`
- Create: `apps/api/tests/fixtures/github/scope_cases.json`

**Interfaces:**
- Produces rule IDs `scope.exactly_one_type`, `scope.code_change_requires_pr`, `scope.pr_requires_milestone`, `scope.pr_requires_main_merge`, and `scope.change_requires_candidate_inclusion`.

- [ ] **Step 1: Write parameterized failing tests for all scope rules**

```python
@pytest.mark.parametrize(
    ("case", "expected_rule"),
    [
        ("missing_type", "scope.exactly_one_type"),
        ("two_types", "scope.exactly_one_type"),
        ("missing_pr", "scope.code_change_requires_pr"),
        ("pr_outside_milestone", "scope.pr_requires_milestone"),
        ("pr_not_merged_to_main", "scope.pr_requires_main_merge"),
        ("change_missing_from_candidate", "scope.change_requires_candidate_inclusion"),
    ],
)
def test_scope_violation_is_blocking(case, expected_rule, scope_case_factory):
    findings = evaluate_scope(scope_case_factory(case), POLICY)
    assert expected_rule in {finding.rule_id for finding in findings}
    assert all(finding.evidence for finding in findings)
```

- [ ] **Step 2: Run tests and confirm red**

Run: `cd apps/api && uv run pytest tests/unit/rules/test_scope_rules.py -v`

Expected: FAIL because scope rules do not exist.

- [ ] **Step 3: Implement pure rule functions**

```python
def evaluate_scope(snapshot: ReleaseSnapshot, policy: ReleasePolicy) -> tuple[ReadinessFinding, ...]:
    return tuple(
        finding
        for evaluator in (
            _evaluate_type_labels,
            _evaluate_pr_links,
            _evaluate_pr_milestones,
            _evaluate_main_merge,
            _evaluate_candidate_inclusion,
        )
        for finding in evaluator(snapshot, policy)
    )
```

Each finding carries the Issue/PR URL and a stable evidence fingerprint.

- [ ] **Step 4: Add false-positive tests**

Cover `release-ops` without PR, two linked PRs where one valid PR is sufficient, closed non-blocking issue, and a PR merged before branch cut whose commit is reachable from candidate.

- [ ] **Step 5: Verify and commit**

Run: `cd apps/api && uv run pytest tests/unit/rules/test_scope_rules.py -v`

Expected: PASS.

```bash
git add apps/api
git commit -m "feat: evaluate milestone scope completeness"
```

## Task 8: CI Policy and Check Rules

**Files:**
- Create: `apps/api/src/release_intelligence/domain/rules/checks.py`
- Create: `apps/api/tests/unit/rules/test_check_rules.py`

**Interfaces:**
- Produces rule IDs `checks.blocking_not_successful`, `checks.advisory_requires_decision`, and `checks.unknown_requires_classification`; produces `CheckFingerprint` from repository, candidate SHA, check name, run ID, and conclusion.

- [ ] **Step 1: Write the status matrix as failing tests**

```python
@pytest.mark.parametrize("conclusion", [None, "failure", "cancelled", "timed_out", "skipped", "neutral"])
def test_blocking_check_requires_success(conclusion, snapshot_with_check):
    findings = evaluate_checks(snapshot_with_check("api", conclusion), BLOCKING_POLICY, decisions=())
    assert findings[0].rule_id == "checks.blocking_not_successful"
    assert findings[0].blocks_release is True


def test_unknown_check_requires_classification(snapshot_with_check):
    findings = evaluate_checks(snapshot_with_check("new-scan", "success"), POLICY, decisions=())
    assert findings[0].rule_id == "checks.unknown_requires_classification"
```

- [ ] **Step 2: Run tests and confirm red**

Run: `cd apps/api && uv run pytest tests/unit/rules/test_check_rules.py -v`

Expected: FAIL because check rules do not exist.

- [ ] **Step 3: Implement exact classification and conclusion handling**

Only `success` passes a blocking check. Advisory success passes; advisory pending/failure produces a decision-required finding; ignored checks produce no finding; unknown checks always require classification.

- [ ] **Step 4: Add missing-check and duplicate-name cases**

For every configured blocking name, require exactly one check for candidate SHA. Duplicate context names with conflicting conclusions yield `INSUFFICIENT_DATA` rather than selecting the favorable value.

- [ ] **Step 5: Verify and commit**

Run: `cd apps/api && uv run pytest tests/unit/rules/test_check_rules.py -v`

Expected: PASS.

```bash
git add apps/api
git commit -m "feat: classify candidate CI checks deterministically"
```

## Task 9: Human Decision Audit Trail and Fingerprint Invalidation

**Files:**
- Create: `apps/api/src/release_intelligence/application/decisions.py`
- Create: `apps/api/src/release_intelligence/api/routes/decisions.py`
- Create: `apps/api/tests/unit/test_decisions.py`
- Create: `apps/api/tests/integration/test_decision_routes.py`
- Create: `apps/web/src/features/decisions/DecisionForm.tsx`
- Create: `apps/web/src/features/decisions/DecisionForm.test.tsx`

**Interfaces:**
- Produces: `DecisionKind.ACCEPTED_RISK|RELEASE_BLOCKER`, `DecisionService.record(...) -> HumanDecision`, and `POST /api/analyses/{run_id}/decisions`.

- [ ] **Step 1: Write failing decision-domain tests**

```python
def test_decision_requires_non_blank_reason(decision_service):
    with pytest.raises(DecisionValidationError):
        decision_service.record(FINGERPRINT, DecisionKind.ACCEPTED_RISK, "  ", ACTOR)


def test_changed_run_id_invalidates_old_decision(advisory_check, accepted_decision):
    changed = replace(advisory_check, run_id=advisory_check.run_id + 1)
    findings = evaluate_checks(snapshot(changed), POLICY, decisions=(accepted_decision,))
    assert findings[0].requires_decision is True
```

- [ ] **Step 2: Run tests red and implement decision service green**

Run: `cd apps/api && uv run pytest tests/unit/test_decisions.py -v`

Expected before implementation: FAIL. Expected after implementation: PASS.

- [ ] **Step 3: Write failing API authorization and immutability tests**

Reject decisions from users without repository access, reject unknown finding IDs, and never update an existing decision row; a changed decision creates a new audit row and marks the previous row superseded.

- [ ] **Step 4: Implement the decision endpoint and atomic reassessment**

Within one transaction, insert decision, re-evaluate check findings against the same snapshot, and persist the new assessment. Do not re-fetch GitHub.

- [ ] **Step 5: Write failing UI form tests and implement**

```tsx
it("requires a reason before recording accepted risk", async () => {
  render(<DecisionForm finding={FINDING} />);
  await user.click(screen.getByRole("button", { name: "Accept risk" }));
  expect(screen.getByText("Explain why this risk is acceptable")).toBeInTheDocument();
});
```

Show fingerprint metadata and actor confirmation; never preselect acceptance.

- [ ] **Step 6: Verify and commit**

Run:

```bash
cd apps/api && uv run pytest tests/unit/test_decisions.py tests/integration/test_decision_routes.py -v
cd ../web && pnpm vitest run src/features/decisions/DecisionForm.test.tsx && pnpm tsc --noEmit
```

Expected: all commands exit 0.

```bash
git add apps/api apps/web
git commit -m "feat: record auditable CI risk decisions"
```

## Task 10: Blocker, Release Operations, and Migration Rules

**Files:**
- Create: `apps/api/src/release_intelligence/domain/rules/blockers.py`
- Create: `apps/api/src/release_intelligence/domain/rules/operations.py`
- Create: `apps/api/tests/unit/rules/test_blocker_rules.py`
- Create: `apps/api/tests/unit/rules/test_operations_rules.py`
- Create: `apps/api/tests/fixtures/github/operations_cases.json`

**Interfaces:**
- Produces rule IDs `blockers.open_release_blocker`, `operations.owner_required`, `operations.section_required`, and `operations.migration_evidence_required`.

- [ ] **Step 1: Write failing blocker tests**

```python
def test_open_release_blocker_blocks_and_cannot_be_overridden(snapshot_with_blocker):
    findings = evaluate_blockers(snapshot_with_blocker(state="open"), POLICY)
    assert findings[0].rule_id == "blockers.open_release_blocker"
    assert findings[0].blocks_release is True
    assert findings[0].decision_allowed is False
```

- [ ] **Step 2: Write failing operations and migration tests**

```python
@pytest.mark.parametrize("missing", ["owner", "before", "during", "after"])
def test_release_ops_requires_structured_fields(missing, operation_case):
    findings = evaluate_operations(operation_case(missing=missing), POLICY)
    assert findings


def test_migration_evidence_must_reference_successful_connected_repo_check(migration_case):
    findings = evaluate_operations(migration_case(check_conclusion="failure"), POLICY)
    assert findings[0].rule_id == "operations.migration_evidence_required"
```

- [ ] **Step 3: Run tests and confirm red**

Run: `cd apps/api && uv run pytest tests/unit/rules/test_blocker_rules.py tests/unit/rules/test_operations_rules.py -v`

Expected: FAIL because rules do not exist.

- [ ] **Step 4: Implement deterministic structured-field parsing**

Parse only issue-form fields or exact Markdown headings `Before release`, `During release`, `After release`, and `Migration evidence`. Reject empty strings and placeholders such as `-`, `none`, and unchecked template text. Do not send the text to AI.

- [ ] **Step 5: Validate migration evidence origin**

Accept only a parsed GitHub check/run URL whose owner/repository matches the connected repository and whose normalized check conclusion is `success`.

- [ ] **Step 6: Verify and commit**

Run: `cd apps/api && uv run pytest tests/unit/rules/test_blocker_rules.py tests/unit/rules/test_operations_rules.py -v`

Expected: PASS.

```bash
git add apps/api
git commit -m "feat: gate blockers release operations and migrations"
```

## Task 11: Previous-Release Back-Merge Safeguard

**Files:**
- Create: `apps/api/src/release_intelligence/domain/rules/backmerge.py`
- Create: `apps/api/tests/unit/rules/test_backmerge_rules.py`
- Create: `apps/api/tests/fixtures/github/backmerge_cases.json`

**Interfaces:**
- Produces rule ID `backmerge.main_pr_required`; consumes previous milestone items, PR base branches, Issue–PR links, and merge states from the normalized snapshot.

- [ ] **Step 1: Write failing relation-based tests**

```python
def test_previous_release_pr_requires_related_main_pr(backmerge_case):
    findings = evaluate_backmerge(backmerge_case(main_pr=None), POLICY)
    assert findings[0].rule_id == "backmerge.main_pr_required"


def test_cherry_picked_change_passes_when_issue_links_both_prs(backmerge_case):
    findings = evaluate_backmerge(backmerge_case(different_shas=True, main_pr_merged=True), POLICY)
    assert findings == ()
```

- [ ] **Step 2: Run tests and confirm red**

Run: `cd apps/api && uv run pytest tests/unit/rules/test_backmerge_rules.py -v`

Expected: FAIL because rule does not exist.

- [ ] **Step 3: Implement Issue-relation matching**

For every PR merged into configured previous release branch, require at least one linked Issue and at least one PR linked to that Issue with base `main` and merged state. Do not compare commit hashes for this rule.

- [ ] **Step 4: Add ambiguity tests**

Missing previous-release configuration is a policy error before analysis. Multiple linked Issues pass only when every Issue representing shipped scope has a corresponding main PR.

- [ ] **Step 5: Verify and commit**

Run: `cd apps/api && uv run pytest tests/unit/rules/test_backmerge_rules.py -v`

Expected: PASS.

```bash
git add apps/api
git commit -m "feat: detect missing previous-release back merges"
```

## Task 12: Full Assessment Precedence and Decision-First Report UI

**Files:**
- Modify: `apps/api/src/release_intelligence/domain/assessment.py`
- Modify: `apps/api/src/release_intelligence/application/analyze_release.py`
- Create: `apps/api/tests/unit/test_assessment_precedence.py`
- Create: `apps/web/src/features/report/ReleaseReport.tsx`
- Create: `apps/web/src/features/report/ReleaseReport.test.tsx`
- Create: `apps/web/src/features/report/FindingCard.tsx`
- Create: `apps/web/src/features/report/SupportingDetails.tsx`
- Create: `apps/web/src/styles/tokens.css`
- Modify: `apps/web/src/styles/global.css`
- Modify: `apps/web/src/app/App.tsx`

**Interfaces:**
- Produces `assess(snapshot, policy, decisions, now) -> ReadinessAssessment` with fixed precedence and React report sections Verdict, Attention Required, Required Actions, Decision Queue, and Supporting Details.

- [ ] **Step 1: Write failing precedence and monotonicity tests**

```python
@pytest.mark.parametrize(
    ("complete", "has_blocker", "needs_decision", "expected"),
    [
        (False, True, True, ReleaseStatus.INSUFFICIENT_DATA),
        (True, True, True, ReleaseStatus.NOT_READY),
        (True, False, True, ReleaseStatus.NEEDS_DECISION),
        (True, False, False, ReleaseStatus.READY),
    ],
)
def test_status_precedence(complete, has_blocker, needs_decision, expected, assessment_case):
    assert assess(**assessment_case(complete, has_blocker, needs_decision)).status is expected
```

- [ ] **Step 2: Add Hypothesis invariants**

```python
@given(assessment_inputs())
def test_adding_blocker_never_improves_status(case):
    before = assess(**case)
    after = assess(**case.with_added_blocker())
    assert STATUS_RANK[after.status] <= STATUS_RANK[before.status]
```

Also test missing evidence, stale snapshot, repeated evaluation, and AI-independent status.

- [ ] **Step 3: Run domain tests red, implement aggregator, run green**

Run: `cd apps/api && uv run pytest tests/unit/test_assessment_precedence.py -v`

Expected before implementation: FAIL. Expected after implementation: PASS.

- [ ] **Step 4: Write failing report hierarchy tests**

```tsx
it("orders verdict, blockers, actions, decisions, then supporting details", () => {
  render(<ReleaseReport assessment={NOT_READY_ASSESSMENT} />);
  const headings = screen.getAllByRole("heading").map((node) => node.textContent);
  expect(headings).toEqual([
    "Release 2026.08.10",
    "What requires attention",
    "Required actions",
    "Decisions",
    "Supporting details",
  ]);
  expect(screen.queryByText(/readiness score/i)).not.toBeInTheDocument();
});
```

- [ ] **Step 5: Implement accessible minimal report**

Use semantic headings, status text in addition to color, keyboard-accessible disclosure elements, and escaped text. Put source freshness beside status. Each non-pass finding has one primary action and one evidence link.

- [ ] **Step 6: Verify and commit**

Run:

```bash
cd apps/api && uv run pytest tests/unit/test_assessment_precedence.py -v
cd ../web && pnpm vitest run src/features/report/ReleaseReport.test.tsx && pnpm tsc --noEmit && pnpm vite build
```

Expected: all commands exit 0.

```bash
git add apps/api apps/web
git commit -m "feat: present deterministic release decision report"
```

## Task 13: Grounded AI Explanation with Deterministic Fallback

**Files:**
- Modify: `apps/api/src/release_intelligence/config.py`
- Create: `apps/api/src/release_intelligence/ports/ai.py`
- Create: `apps/api/src/release_intelligence/application/explanations.py`
- Create: `apps/api/src/release_intelligence/adapters/ai/openai_provider.py`
- Create: `apps/api/src/release_intelligence/api/routes/explanations.py`
- Create: `apps/api/tests/unit/test_ai_grounding.py`
- Create: `apps/api/tests/integration/test_explanation_route.py`
- Create: `apps/web/src/features/report/AIExplanation.tsx`
- Create: `apps/web/src/features/report/AIExplanation.test.tsx`
- Modify: `.env.example`

**Interfaces:**
- Produces `AIExplanationProvider.explain(input: ExplanationInput) -> AIExplanation`, `POST /api/analyses/{run_id}/explanation`, and schema fields `summary`, `groups`, `actions`, `limitations`, `confidence`, `finding_ids`, and `evidence_ids`.

- [ ] **Step 1: Write failing prompt-boundary tests**

```python
def test_explanation_input_excludes_raw_untrusted_content(input_builder):
    payload = input_builder(ISSUE_WITH_MALICIOUS_BODY, CHECK_WITH_LOGS)
    serialized = payload.model_dump_json()
    assert "ignore previous instructions" not in serialized
    assert "full_log" not in serialized
    assert "api_key" not in serialized
```

- [ ] **Step 2: Write failing reference-integrity tests**

```python
@pytest.mark.parametrize("mutation", ["unknown_finding", "unknown_evidence", "changed_severity", "unlinked_action"])
def test_invalid_ai_output_is_rejected(mutation, explanation_validator, ai_output):
    with pytest.raises(AIExplanationRejected):
        explanation_validator.validate(ai_output.mutate(mutation))
```

- [ ] **Step 3: Run tests and confirm red**

Run: `cd apps/api && uv run pytest tests/unit/test_ai_grounding.py -v`

Expected: FAIL because AI boundary does not exist.

- [ ] **Step 4: Implement the provider port, Pydantic schema, and validator**

The input contains all blockers/decision-required findings and at most 20 warnings. Truncate each untrusted title/name to 200 characters. Validate IDs against allowlists and severity against deterministic findings.

- [ ] **Step 5: Implement OpenAI Responses adapter**

Use `gpt-5.6` as default, `store=False`, one request, 15-second timeout, strict structured output derived from Pydantic, no tools, and record model/latency/input tokens/output tokens/cost metadata. Add required `OPENAI_INPUT_COST_PER_MILLION` and `OPENAI_OUTPUT_COST_PER_MILLION` decimal settings to `.env.example`; compute cost from observed token counts and the configured prices, never from a hard-coded historical price. Retry once only for 429 or 5xx within the total timeout budget.

```python
response = await asyncio.wait_for(
    self._client.responses.parse(
        model=self._model,
        input=messages,
        text_format=AIExplanation,
        store=False,
    ),
    timeout=15,
)
```

- [ ] **Step 6: Implement route fallback**

Timeout, refusal, parse error, or validation rejection returns HTTP 200 with `{ "state": "unavailable" }`; it never modifies `ReadinessAssessment`.

- [ ] **Step 7: Write failing UI separation test and implement**

```tsx
it("labels AI content and leaves deterministic status unchanged", () => {
  render(<AIExplanation explanation={EXPLANATION} status="NOT_READY" />);
  expect(screen.getByText("AI explanation")).toBeInTheDocument();
  expect(screen.getByText("NOT READY")).toBeInTheDocument();
});
```

- [ ] **Step 8: Verify and commit**

Run:

```bash
cd apps/api && uv run pytest tests/unit/test_ai_grounding.py tests/integration/test_explanation_route.py -v
cd ../web && pnpm vitest run src/features/report/AIExplanation.test.tsx && pnpm tsc --noEmit
```

Expected: all commands exit 0.

```bash
git add apps/api apps/web
git commit -m "feat: add grounded optional AI explanations"
```

## Task 14: Versioned Benchmark Dataset and Metrics

**Files:**
- Create: `apps/api/src/release_intelligence/benchmark/schema.py`
- Create: `apps/api/src/release_intelligence/benchmark/runner.py`
- Create: `apps/api/src/release_intelligence/benchmark/review.py`
- Create: `apps/api/tests/unit/test_benchmark_runner.py`
- Create: `apps/api/tests/unit/test_benchmark_review.py`
- Create: `benchmarks/scenarios/catalog.yaml`
- Create: `benchmarks/reviews/schema.json`
- Create: `benchmarks/README.md`

**Interfaces:**
- Produces deterministic CLI `python -m release_intelligence.benchmark.runner --catalog ../../benchmarks/scenarios/catalog.yaml --output benchmark-results.json`, review CLI `python -m release_intelligence.benchmark.review --claims ai-claims.json --review claim-review.yaml`, and metrics `readiness_accuracy`, `critical_recall`, `risk_precision`, `risk_recall`, `evidence_coverage`, `invalid_evidence_rate`, `unsupported_claim_rate`, `p50_ms`, and `p95_ms`.

- [ ] **Step 1: Write failing benchmark-schema tests**

```python
def test_scenario_requires_ground_truth_and_evidence():
    with pytest.raises(ValidationError):
        BenchmarkScenario.model_validate({"id": "missing-ground-truth"})


def test_critical_miss_fails_acceptance_gate(runner, catalog_with_critical_miss):
    result = runner.run(catalog_with_critical_miss)
    assert result.accepted is False
    assert result.metrics.critical_recall < 1.0
```

- [ ] **Step 2: Run tests and confirm red**

Run: `cd apps/api && uv run pytest tests/unit/test_benchmark_runner.py -v`

Expected: FAIL because schema and runner do not exist.

- [ ] **Step 3: Implement scenario schema and exact metric formulas**

Define risk identity as `(rule_id, source_id)`. Precision is true-positive risks divided by all predicted risks; recall is true-positive risks divided by ground-truth risks; evidence coverage is findings with at least one correct evidence ID divided by all significant findings. Zero-denominator scenarios are excluded from that metric and counted separately.

The deterministic runner validates every AI finding/evidence reference structurally. The review CLI handles semantic support: it exports each atomic AI claim with its cited finding/evidence facts, requires a human verdict (`supported` or `unsupported`), reviewer identity, rationale, and timestamp for every claim, and calculates unsupported-claim rate as unsupported claims divided by reviewed claims. Missing review decisions are a failed gate, never interpreted as zero unsupported claims.

- [ ] **Step 4: Add at least 40 named scenarios**

The catalog must include these categories and counts:

```yaml
categories:
  clean: 4
  scope: 7
  checks: 8
  decisions: 4
  blockers: 3
  operations_and_migrations: 5
  backmerge: 3
  partial_and_stale_data: 3
  compound_risks: 3
  injection_and_false_positive_traps: 4
```

Total: 44 scenarios. Every scenario includes expected status, finding identities, severities, and evidence IDs.

- [ ] **Step 5: Add acceptance-gate output**

The deterministic CLI exits non-zero when readiness agreement is below 0.95, critical recall below 1.0, risk precision below 0.95, evidence coverage below 1.0, or invalid evidence is above 0. It reports `unsupported_claim_rate` as `null` unless an AI claim set and complete human review are supplied; it must never print `0` for an unreviewed set. The review CLI exits non-zero for incomplete review or any unsupported claim.

- [ ] **Step 6: Verify and commit**

Run:

```bash
cd apps/api
uv run pytest tests/unit/test_benchmark_runner.py tests/unit/test_benchmark_review.py -v
uv run python -m release_intelligence.benchmark.runner --catalog ../../benchmarks/scenarios/catalog.yaml --output ../../benchmark-results.json
```

Expected: tests pass, command exits 0, and the output reports 44 scenarios.

```bash
git add apps/api benchmarks
git commit -m "test: add release readiness benchmark dataset"
```

## Task 15: Security Hardening and Adversarial Tests

**Files:**
- Create: `apps/api/src/release_intelligence/security/urls.py`
- Create: `apps/api/src/release_intelligence/security/logging.py`
- Create: `apps/api/tests/security/test_prompt_injection.py`
- Create: `apps/api/tests/security/test_evidence_urls.py`
- Create: `apps/api/tests/security/test_secret_logging.py`
- Create: `apps/api/tests/security/test_authorization.py`
- Create: `apps/web/src/test/security.test.tsx`

**Interfaces:**
- Produces `parse_github_evidence_url(url, expected_repo) -> GitHubEvidenceLocator`, allowlisted structured logger, redaction filter, and repository-scoped authorization dependency.

- [ ] **Step 1: Write failing SSRF and repository-confusion tests**

```python
@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data",
        "https://evil.example/github.com/owner/repo/actions/runs/1",
        "https://github.com/other/repo/actions/runs/1",
        "javascript:alert(1)",
    ],
)
def test_untrusted_evidence_url_is_rejected(url):
    with pytest.raises(InvalidEvidenceURL):
        parse_github_evidence_url(url, expected_repo="owner/repo")
```

- [ ] **Step 2: Write failing prompt-injection tests**

Use malicious Issue/PR/check names containing attempts to mark the release ready, request secrets, add evidence IDs, and emit HTML. Assert deterministic status and AI allowlists are unchanged.

- [ ] **Step 3: Write failing log-leakage tests**

Capture logs during auth failure, GitHub error, database exception, and LLM rejection. Assert tokens, private key fragments, prompt text, raw bodies, and database DSN are absent.

- [ ] **Step 4: Implement strict URL parsing and structured logging**

Accept only `https://github.com/{owner}/{repo}/...` and GitHub API identifiers produced by the adapter. The server never performs HTTP GET against an evidence URL; it uses typed GitHub API calls.

- [ ] **Step 5: Add XSS rendering tests**

Render malicious titles and AI strings and assert they appear as text while no executable element or event handler is created.

- [ ] **Step 6: Verify and commit**

Run:

```bash
cd apps/api && uv run pytest tests/security -v
cd ../web && pnpm vitest run src/test/security.test.tsx
```

Expected: all commands exit 0.

```bash
git add apps/api apps/web
git commit -m "security: harden evidence auth logging and AI boundaries"
```

## Task 16: Docker, CI, Playwright E2E, and Live Smoke Workflows

**Files:**
- Create: `compose.yaml`
- Modify: `compose.test.yaml`
- Create: `apps/api/Dockerfile`
- Create: `apps/web/Dockerfile`
- Create: `tests/e2e/package.json`
- Create: `tests/e2e/playwright.config.ts`
- Create: `tests/e2e/release-readiness.spec.ts`
- Create: `ops/smoke.sh`
- Create: `.github/workflows/ci.yml`
- Create: `.github/workflows/live-github-smoke.yml`
- Create: `.github/workflows/live-ai-benchmark.yml`

**Interfaces:**
- Produces reproducible local stack, deterministic E2E, six blocking CI jobs, and secret-protected manual live checks.

- [ ] **Step 1: Write the failing Playwright vertical scenario**

```ts
test("fixture release moves from needs decision to ready", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Use demo repository" }).click();
  await page.getByLabel("Milestone").selectOption("7");
  await page.getByLabel("Release candidate").selectOption("release/2026-08-10");
  await page.getByRole("button", { name: "Run analysis" }).click();
  await expect(page.getByText("NEEDS DECISION")).toBeVisible();
  await page.getByRole("button", { name: "Accept risk" }).click();
  await page.getByLabel("Reason").fill("Known flaky advisory test; blocking suite is green.");
  await page.getByRole("button", { name: "Record decision" }).click();
  await expect(page.getByText("READY")).toBeVisible();
  await expect(page.getByRole("link", { name: "Open evidence" })).toHaveAttribute("href", /github\.com/);
});
```

- [ ] **Step 2: Run E2E and confirm red**

Run: `cd tests/e2e && pnpm install && pnpm exec playwright test`

Expected: FAIL because the complete stack is not configured.

- [ ] **Step 3: Add production and test containers**

Use multi-stage builds, non-root runtime users, health checks, PostgreSQL 18-alpine, read-only application filesystem where supported, and named volumes only for database state. `.env.example` contains fake values only.

- [ ] **Step 4: Make E2E green with fixture adapter**

`ENVIRONMENT=e2e` selects the fixture GitHub source and fake AI provider. It must not bypass authz, policy, persistence, rules, or decision logic.

- [ ] **Step 5: Create CI workflow**

Jobs:

```text
api-unit-static
api-postgres-integration
web-test-build
security-benchmark
playwright-e2e
docker-smoke
```

Pin third-party Actions to full commit SHAs. External pull requests receive no secrets.

- [ ] **Step 6: Create live smoke workflows**

`live-github-smoke.yml` uses `workflow_dispatch`, GitHub App secrets, and the synthetic demo repository; it verifies installation auth, milestone ingestion, links, checks, candidate branch, and rate-limit metadata. `live-ai-benchmark.yml` uses `workflow_dispatch`, provider secret, all 44 scenarios, and uploads only aggregate metrics.

- [ ] **Step 7: Verify locally and commit**

Run:

```bash
docker compose -f compose.test.yaml up --build -d
cd tests/e2e && pnpm exec playwright test
cd ../..
bash ops/smoke.sh
docker compose -f compose.test.yaml down -v
docker compose config --quiet
```

Expected: all commands exit 0.

```bash
git add compose.yaml compose.test.yaml apps tests ops .github .env.example
git commit -m "ci: add reproducible release intelligence quality gates"
```

## Task 17: Synthetic Public Demo Repository

**Files:**
- Create: `demo/repository/README.md`
- Create: `demo/repository/.github/workflows/release-ci.yml`
- Create: `demo/repository/.github/ISSUE_TEMPLATE/code-change.yml`
- Create: `demo/repository/.github/ISSUE_TEMPLATE/release-ops.yml`
- Create: `demo/repository/scripts/migrate.py`
- Create: `demo/repository/tests/test_migration.py`
- Create: `demo/seed_manifest.yaml`
- Create: `demo/seed_demo_repo.sh`
- Create: `demo/test_seed_manifest.py`

**Interfaces:**
- Produces public repository `floppy522/ai-release-intelligence-demo`, milestone `Release 2026.08.10`, branches `main`, `release/2026-08-03`, and `release/2026-08-10`, plus synthetic Issues/PR/check states aligned with the live smoke workflow.

- [ ] **Step 1: Write a failing manifest test**

```python
def test_demo_manifest_has_required_release_evidence(manifest):
    assert manifest["milestone"] == "Release 2026.08.10"
    assert {"code-change", "release-ops", "release-blocker", "migration-required"} <= set(manifest["labels"])
    assert manifest["candidate_branch"] == "release/2026-08-10"
    assert any(case["expected_status"] == "NEEDS_DECISION" for case in manifest["demo_states"])
```

- [ ] **Step 2: Run the manifest test and confirm red**

Run: `uv run --project apps/api pytest demo/test_seed_manifest.py -v`

Expected: FAIL because demo files do not exist.

- [ ] **Step 3: Implement a deterministic seed manifest and repository content**

The repository contains no real company/product data. Use fictional payment-service Issues, a passing blocking suite, one failed advisory check, migration evidence, one release-ops Issue, and a previous-release PR with a corresponding main PR.

- [ ] **Step 4: Implement idempotent seed script**

The script uses `gh` to create/update labels, milestone, branches, Issues, and PRs by stable title markers. It refuses to run unless `gh auth status` succeeds and the target owner is exactly `floppy522`.

- [ ] **Step 5: Verify locally, then create the public repository**

Run:

```bash
uv run --project apps/api pytest demo/test_seed_manifest.py -v
gh auth status
bash demo/seed_demo_repo.sh floppy522/ai-release-intelligence-demo
gh repo view floppy522/ai-release-intelligence-demo --json nameWithOwner,visibility,url
```

Expected: test passes; final JSON reports `PUBLIC` and the exact repository name.

- [ ] **Step 6: Run live GitHub smoke and commit demo assets**

Run the `Live GitHub smoke` workflow through `gh workflow run`, wait for completion, and require a successful conclusion before commit.

```bash
git add demo
git commit -m "test: add synthetic GitHub release demo"
```

## Task 18: Portfolio Documentation, ADRs, and Human Experiment

**Files:**
- Create: `README.md`
- Create: `docs/product-case.md`
- Create: `docs/threat-model.md`
- Create: `docs/data-retention.md`
- Create: `docs/runbook.md`
- Create: `docs/experiments/human-vs-tool.md`
- Create: `benchmarks/reviews/1.0.0.yaml`
- Create: `docs/adr/0001-deterministic-readiness.md`
- Create: `docs/adr/0002-github-app-read-only.md`
- Create: `docs/adr/0003-no-readiness-score.md`
- Create: `docs/adr/0004-optional-ai-explanation.md`
- Create: `docs/assets/architecture.svg`
- Create: `docs/assets/release-report.png`
- Create: `docs/assets/demo.gif`
- Create: `apps/api/tests/unit/test_documentation_contract.py`

**Interfaces:**
- Produces the complete 3–5 minute interview surface and a measured, limitation-aware experiment report.

- [ ] **Step 1: Write failing documentation-contract tests**

```python
def test_readme_leads_with_problem_and_measured_outcomes(repo_root):
    readme = (repo_root / "README.md").read_text()
    assert readme.index("## Problem") < readme.index("## Technology")
    assert "## Benchmark results" in readme
    assert "## Limitations" in readme


def test_docs_do_not_claim_unmeasured_impact(repo_root):
    text = "\n".join(path.read_text() for path in (repo_root / "docs").rglob("*.md"))
    assert "improved productivity by 70%" not in text.lower()
```

- [ ] **Step 2: Run tests and confirm red**

Run: `cd apps/api && uv run pytest tests/unit/test_documentation_contract.py -v`

Expected: FAIL because portfolio documents do not exist.

- [ ] **Step 3: Write README and product case from measured evidence**

README order is Problem, Demo, Key outcomes, How it works, Product decisions, Architecture, AI safety and grounding, Benchmark results, Engineering quality, Local setup, Limitations, Roadmap, My role. `docs/product-case.md` follows Context, Problem, Research, JTBD, Alternatives, MVP hypothesis, Prioritization, Product decisions, Technical trade-offs, AI design, Validation, Results, Limitations, Next.

- [ ] **Step 4: Write threat model, retention policy, runbook, and ADRs**

Document exact GitHub permissions, STRIDE threats, token lifecycle, deletion behavior, recovery steps, rate-limit behavior, LLM outage behavior, and why the design excludes a score, chat, and write permissions.

- [ ] **Step 5: Produce real screenshots and demo GIF**

Use only synthetic demo data. Capture setup, NOT_READY report, decision form, and READY report from the running application. Architecture SVG must match implemented modules and data flow.

- [ ] **Step 6: Execute the human-vs-tool experiment**

Use six scenarios and crossover order `M,T,M,T,M,T` for the first pass and `T,M,T,M,T,M` for the second. Record participant count, per-scenario time, correct status, critical blockers found, evidence opened, median, relative change, and limitations. Do not prefill results before measurement.

Run the live AI benchmark, export its atomic claim packet, and review every claim against the cited deterministic finding/evidence facts. Record reviewer, rationale, and timestamp in `benchmarks/reviews/1.0.0.yaml`; run the review CLI and require zero unsupported claims. If a claim is unsupported, retain the failed evidence, fix the grounding boundary, rerun the live benchmark, and review the new packet.

- [ ] **Step 7: Verify documentation and commit**

Run:

```bash
cd apps/api && uv run pytest tests/unit/test_documentation_contract.py -v
cd ../..
test -s docs/assets/architecture.svg
test -s docs/assets/release-report.png
test -s docs/assets/demo.gif
rg -n "Benchmark results|Limitations|My role" README.md
uv run --project apps/api python -m release_intelligence.benchmark.review --claims ai-claims.json --review benchmarks/reviews/1.0.0.yaml
```

Expected: all commands exit 0, the experiment file contains measured rows rather than an empty template, every live AI claim has a review decision, and unsupported-claim rate is zero.

```bash
git add README.md docs benchmarks/reviews/1.0.0.yaml apps/api/tests/unit/test_documentation_contract.py
git commit -m "docs: present measured release intelligence product case"
```

## Task 19: Final Verification and Portfolio Release Gate

**Files:**
- Modify only files required to fix failures revealed by the commands below.
- Create: `docs/verification/1.0.0.md`

**Interfaces:**
- Produces one evidence document containing exact commands, timestamps, exit codes, test counts, benchmark metrics, live smoke run URLs, model/cost metadata, and known limitations.

- [ ] **Step 1: Run the complete backend quality gate**

```bash
cd apps/api
uv sync --locked --all-groups
uv run alembic upgrade head
uv run pytest -v
uv run ruff check src tests
uv run mypy
```

Expected: every command exits 0.

- [ ] **Step 2: Run the complete frontend quality gate**

```bash
cd apps/web
pnpm install --frozen-lockfile
pnpm vitest run
pnpm lint
pnpm tsc --noEmit
pnpm vite build
```

Expected: every command exits 0.

- [ ] **Step 3: Run E2E, benchmark, and Docker gates**

```bash
docker compose -f compose.test.yaml up --build -d
cd tests/e2e && pnpm install --frozen-lockfile && pnpm exec playwright test
cd ../..
uv run --project apps/api python -m release_intelligence.benchmark.runner --catalog benchmarks/scenarios/catalog.yaml --output benchmark-results.json
bash ops/smoke.sh
docker compose -f compose.test.yaml down -v
docker compose config --quiet
```

Expected: every command exits 0; benchmark reports 44 scenarios and all acceptance thresholds pass.

- [ ] **Step 4: Run live provider and GitHub smoke gates**

Trigger `Live GitHub smoke` and `Live AI benchmark`, wait for both, and record successful workflow run URLs. Download the exact live AI claim artifact referenced by `benchmarks/reviews/1.0.0.yaml`, rerun the review CLI against it, and require zero unsupported claims. Confirm AI cost per explanation is at most $0.05 and p95 explanation latency is at most 15 seconds for the documented model/configuration.

- [ ] **Step 5: Verify repository hygiene**

```bash
git status --short
git ls-files | rg '(^|/)(\.env|.*\.pem|.*\.key|.*\.dump|.*\.age)$' && exit 1 || true
git grep -I -E 'gh[ps]_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{16,}|BEGIN .*PRIVATE KEY' -- . && exit 1 || true
```

Expected: clean status and no prohibited tracked artifact or secret pattern.

- [ ] **Step 6: Write verification evidence**

Record only observed results in `docs/verification/1.0.0.md`. Include failures and limitations if any gate did not meet its target; do not label the release complete while a gate is failing.

- [ ] **Step 7: Commit the verified release evidence**

```bash
git add docs/verification/1.0.0.md benchmark-results.json
git commit -m "docs: record AI Release Intelligence 1.0.0 verification"
```

Do not push, tag, or open a pull request until `superpowers:verification-before-completion` has independently re-run the relevant full commands and confirmed their output.
