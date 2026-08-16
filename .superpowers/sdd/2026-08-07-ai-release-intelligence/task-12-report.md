# Task 12 Implementation / TDD Report

## Status

Implemented the deterministic release assessment and decision-first report, then
completed fix rounds 1 and 2 for all reported Important review findings. Readiness
remains strictly rule-based with fixed precedence:

1. `INSUFFICIENT_DATA` for incomplete, invalid, inconsistent, or stale evidence;
2. `NOT_READY` for any deterministic or human-marked blocker;
3. `NEEDS_DECISION` for an unresolved decision-eligible finding;
4. `READY` otherwise.

No numeric score or AI output participates in the result.

## Fix-round TDD evidence

### Backend RED and GREEN

- The first assessment regression run failed collection because
  `refresh_snapshot_freshness` did not exist.
- The first route regression run failed collection because
  `MissingReleasePolicy` did not exist.
- After the initial separation, four existing route fixtures failed because they
  relied on an invalid short candidate SHA or constructed a production service
  without policy. Those fixtures now provide a valid configured policy; a new
  explicit missing-policy route case proves the loader is never entered.
- Focused policy/freshness/insufficiency coverage is 36 passed.
- Full backend unit and contract coverage is 584 passed with warnings as errors
  and deterministic Hypothesis seed `0`.

### Decision reassessment RED and GREEN

- Review reproduced that persistence replayed checks only and derived status from
  the surviving finding severities, allowing stored insufficiency to be promoted.
- The replacement boundary rejects any persisted state other than fresh
  `NEEDS_DECISION`, verifies the exact current finding/fingerprint, and invokes the
  complete pure assessor over the stored snapshot, exact stored policy, active
  decisions, and decision time without GitHub access.
- Focused pure reassessment coverage is 3 passed: persisted-insufficient rejection,
  stale-at-decision rejection, and release-blocker replay.
- Real PostgreSQL contracts were added/updated and collect successfully. The 20
  collected analysis/decision contracts inventory includes full reassessment
  round-trip, trigger-forced atomic rollback, persisted-insufficient rejection,
  stale-snapshot rejection, one-winner concurrency, stale/noneligible finding
  rejection, finding-ID metadata, and insufficiency-plus-business-finding
  round-trip.

### Report/UI RED and GREEN

- Focused Vitest RED had seven behavior failures: a second supporting-details
  evidence link, a decision form under `INSUFFICIENT_DATA`, four repository or
  resource URL validation bypasses, and App still loading the demo instead of the
  analysis-run DTO.
- Focused report plus App coverage is 21 passed, followed by a clean TypeScript
  check.
- Full frontend coverage is 45 passed across five files; TypeScript, ESLint, and
  the Vite production build pass.

### Fix round 2: insufficiency presentation and authenticated CSRF bootstrap

- Focused backend RED was exactly two failures: the missing `/api/auth/csrf`
  route returned 404 where an authenticated request required 200 and an anonymous
  request required 401. Focused backend GREEN is 4 passed with 9 deselected,
  covering bootstrap success, digest mismatch, callback issuance, and unsafe-method
  validation.
- The login callback and bootstrap endpoint server-derive domain-separated CSRF
  material from the high-entropy HttpOnly session token. Persistence retains only
  its digest. Bootstrap authenticates the session, verifies the stored digest, and
  returns the raw token only in a `no-store`, `no-cache`, `no-referrer` response.
  The endpoint does not rotate or persist raw material, so a safe GET cannot be
  abused to invalidate another browser's active form.
- Focused frontend RED had nine behavior failures across the initially missing
  client bootstrap, incorrect insufficiency label, implicit demo route, absent demo
  warning, and App's missing fail-closed bootstrap handling. Focused GREEN is 27
  passed across three files.
- `INSUFFICIENT_DATA` findings now carry an explicit “Insufficient data” label,
  explanatory readiness text, and distinct slate treatment. In mixed
  business-plus-insufficiency reports they are never presented as advisory or
  decision eligible.
- App now requires both a real `analysis_run_id` and an authenticated server CSRF
  bootstrap before rendering a production report. Bootstrap failures and empty
  tokens fail closed. Fixture data loads only for exact `?demo=fixture`, with a
  visible non-production warning; the default route is a neutral landing state and
  never silently shows a READY fixture.

## Backend design

- Policy-dependent `assess` requires a validated `ReleasePolicy`. Production
  `AnalysisService` requires a persisted policy before loader creation. The only
  no-policy compatibility is the explicit trusted legacy demo fixture boundary.
- `refresh_snapshot_freshness` is policy-independent. GET applies it to the stored
  assessment, retaining the stored status/findings while the immutable snapshot is
  valid and appending safe insufficiency findings only when validity or freshness
  fails.
- Every insufficiency code is allowlisted and bounded, then materialized as a
  deterministic `evidence.<code>` finding with a fixed safe message/action and
  digest fingerprint. Exact duplicates collapse and ordering is stable. Raw source
  messages, response bodies, URLs, and secrets never enter the finding text.
- The existing finding/evidence persistence shape stores and round-trips those
  reasons without a schema change, including alongside independently proven
  business findings.
- `StoredAnalysisRun` now includes immutable finding-row presentation metadata.
  The GET DTO exposes real run/finding IDs, authoritative per-finding eligibility
  and current fingerprint, repository identity, release name, and source timestamp.
  Freshness invalidation clears decision eligibility in the response.
- Decision persistence locks the run, checks the latest persisted assessment
  status, loads the exact stored policy version and snapshot, computes current and
  final complete assessments, validates the exact original finding row, then
  appends decision plus complete assessment in the existing transaction. Any
  failure rolls back together.

## Report design

- The semantic order remains verdict/freshness, What requires attention, Required
  actions, Decisions, and Supporting details, with no readiness score.
- App uses `analysis_run_id` as explicit navigation state for the real
  `/api/analyses/{run_id}` DTO. It fetches CSRF material from the authenticated
  same-origin `/api/auth/csrf` bootstrap and uses real server
  run/finding/fingerprint values; a successful decision refetches the run. The
  trusted fixture is isolated behind explicit `?demo=fixture` navigation and is
  visibly labeled.
- Decision forms render only when overall status is `NEEDS_DECISION`, the server
  marks the exact finding eligible, and real UUID/fingerprint/CSRF values are
  present. Blocked, insufficient, stale, or malformed findings never get controls.
- Each finding has at most one canonical actionable link in the attention card.
  Supporting details contain non-link evidence metadata only.
- Evidence links require HTTPS `github.com`, no credentials, explicit port, query,
  fragment, or extra path segments, the authoritative owner/repository, and the
  exact resource family for the evidence type. Unsafe values are rendered as
  escaped text with the link withheld.
- Native disclosure, unique React IDs, text-plus-color status, keyboard-native
  controls, responsive styles, and status-specific empty-state copy remain intact.

## Verification

- Backend unit/contract/non-database integration: 630 passed,
  `-W error --hypothesis-seed=0`.
- Non-database route integration: 46 passed, `-W error`.
- Backend Ruff: passed.
- Strict mypy: passed for 38 source files.
- Scoped Ruff formatting: modified Python files formatted.
- Frontend Vitest: 45 passed across five files.
- Frontend TypeScript, ESLint, and Vite build: passed.
- `uv lock --check --offline`: 48 packages resolved.
- `pnpm install --frozen-lockfile --offline`: up to date.
- Offline PostgreSQL SQL generation: `0001_initial:head` upgrade and
  `head:0001_initial` downgrade passed.
- PostgreSQL integration collection: 33 repository/migration contracts; this
  round changes no persistence contract or schema.
- `git diff --check`: passed.

## Concerns

PostgreSQL is unavailable locally by project constraint, so the real database
contracts were inventoried and collected but not executed; no SQLite or remote
substitute was used. The stricter current-finding rule intentionally rejects a
second decision for an already resolved exact fingerprint, while preserving the
existing atomic audit schema and transaction semantics for still-current decisions.
