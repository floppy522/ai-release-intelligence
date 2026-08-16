# Task 12 Implementation / TDD Report

## Status

Implemented the full deterministic readiness assessment and decision-first release
report. The application now evaluates scope, CI checks, release blockers, release
operations, migrations, and configured previous-release back-merges from one
immutable snapshot, then applies the fixed precedence:

1. `INSUFFICIENT_DATA` for incomplete, invalid, inconsistent, or stale evidence;
2. `NOT_READY` for any deterministic or human-marked blocker;
3. `NEEDS_DECISION` for an unresolved decision-eligible finding;
4. `READY` only when no higher-priority condition exists.

No numeric score or AI output participates in assessment.

## TDD evidence

### Assessment RED

- Added `test_assessment_precedence.py` before the production aggregator.
- The first run exposed the intentionally missing Hypothesis dependency. After
  `hypothesis==6.165.9` was added through `uv add --dev hypothesis`, collection
  reached project behavior and failed because `domain.assessment` did not export
  `MAX_SNAPSHOT_AGE` or `assess`.
- The GREEN suite has 17 cases, including deterministic Hypothesis properties for
  status monotonicity and evidence-order permutations, all four precedence states,
  stale/naive/future timestamps, exact decision fingerprints, release-blocker
  decisions, typed-error finding retention, all evaluator families, duplicate
  idempotency, previous-release context, and AI-like untrusted text.

### Loader/orchestration RED

- Added previous-release loader and service tests before extending the request or
  fetch window.
- Collection failed with `TypeError: AnalysisRequest.__init__() got an unexpected
  keyword argument 'previous_milestone_number'`, proving the policy-selected
  context could not enter the loader.
- The GREEN path loads policy before source access, collects current and previous
  milestones inside both complete reconciliation windows, and marks previous
  context only after the windows agree. Partial, missing, rate-limited, oversized,
  or repeatedly changing prior evidence remains incomplete with no trusted context
  marker.

### Report UI RED

- The initial focused Vitest suite failed to resolve the missing `ReleaseReport`.
- Once the report existed, a two-form regression failed because both
  `DecisionForm` instances emitted `id="decision-error"`.
- A one-action regression then failed because the same primary action appeared in
  the attention card and Required Actions section.
- A two-report regression failed because fixed section heading IDs collided.
- Each RED was followed by the minimal production correction: React `useId`, one
  dedicated action occurrence, and generated section-label IDs.

## Backend behavior

- `domain.assessment.assess` is the pure composition boundary. The existing
  application-level signature remains as a compatibility adapter.
- Scope, check, blocker, operations, and back-merge typed evidence errors are
  caught explicitly. Independently proven carried findings survive, while safe,
  bounded, deterministic error codes drive `INSUFFICIENT_DATA` internally.
- Exact duplicate findings are idempotent and the final finding/evidence ordering
  key is deterministic. Input order, repeated evaluation, and irrelevant release
  text cannot alter status.
- Snapshot freshness uses timezone-aware instants and a strict ten-minute maximum.
  Future, naive, inverted, incomplete, or source-error snapshots cannot be ready.
- Accepted risk resolves only the current repository/candidate/check/run/conclusion
  fingerprint. A stale fingerprint leaves the decision open; a release-blocker
  decision produces `NOT_READY`.
- An empty normalized compatibility snapshot can retain the existing no-policy
  `READY` behavior. Any substantive unassessed items, relations, pulls, checks, or
  comparisons without a policy fail closed.
- `AnalysisService` reads the persisted current policy before creating the loader,
  passes explicit previous milestone/branch coordinates, evaluates after snapshot
  load without another GitHub call, and persists the complete ordered finding set.
- The loader bounds current and prior items together, collects prior Issue–PR
  relations and PR records, and limits candidate comparisons to current-release
  pulls. It never infers previous milestone or branch coordinates.
- Task 9 atomic reassessment remains compatible: non-check findings are preserved,
  check findings are recalculated from the immutable stored snapshot, and status is
  recalculated from the resulting blockers/decisions without GitHub access.

## Report behavior

- The exact top-level hierarchy is release verdict, What requires attention,
  Required actions, Decisions, and Supporting details.
- Verdicts include status text plus a color treatment and source-freshness text.
- Every finding has one primary action in the Required Actions section and one
  canonical GitHub evidence link in its attention card.
- `DecisionForm` appears only for the exact advisory decision rule with
  `DECISION_REQUIRED`, a persisted finding ID, and one current `sha256` check-run
  fingerprint. Blockers never receive decision controls.
- Supporting evidence uses native `<details>/<summary>`. Untrusted values render as
  React text, unsafe/noncanonical links are withheld, and no
  `dangerouslySetInnerHTML`, hover-only meaning, duplicate IDs, or readiness score
  is present.
- All four statuses and empty sections use status-specific copy so incomplete or
  contradictory results never claim a clean release.
- The responsive layout reuses centralized tokens and preserves visible keyboard
  focus at narrow widths.

## Files

- `apps/api/src/release_intelligence/domain/assessment.py`
- `apps/api/src/release_intelligence/application/analyze_release.py`
- `apps/api/tests/unit/test_assessment_precedence.py`
- `apps/api/tests/unit/test_release_loader.py`
- `apps/api/tests/integration/test_analysis_route.py`
- `apps/api/pyproject.toml`
- `apps/api/uv.lock`
- `apps/web/src/features/report/ReleaseReport.tsx`
- `apps/web/src/features/report/ReleaseReport.test.tsx`
- `apps/web/src/features/report/FindingCard.tsx`
- `apps/web/src/features/report/SupportingDetails.tsx`
- `apps/web/src/features/decisions/DecisionForm.tsx`
- `apps/web/src/styles/tokens.css`
- `apps/web/src/styles/global.css`
- `apps/web/src/app/App.tsx`

## Fresh verification

- Backend unit/contract suite with warnings as errors and deterministic Hypothesis
  seed: 579 passed.
- Non-database route integration suite with warnings as errors: 43 passed.
- Full frontend Vitest suite: 28 passed across 4 files.
- Backend Ruff: all checks passed.
- Strict mypy: success across 38 source files.
- Task-scoped Ruff format check: 5 files formatted.
- Frontend TypeScript, ESLint, and Vite production build: passed.
- `uv lock --check --offline`: 48 packages resolved.
- `pnpm install --frozen-lockfile --offline`: lockfile up to date.
- Offline PostgreSQL migration SQL: `0001:head` upgrade and `head:0001`
  downgrade both generated successfully without a database connection.
- `git diff --check`: passed before report creation and is rerun on the staged diff
  before commit.

## Concern

PostgreSQL is unavailable locally by project constraint, so no database runtime
suite was attempted and no SQLite or remote substitute was used. Task 12 changes no
database schema; the existing status/finding and JSON snapshot shapes remain
compatible. Repository-wide formatting drift outside the task remains untouched;
all task-owned Python files pass the scoped formatter gate.
