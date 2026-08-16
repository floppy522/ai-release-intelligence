# Task 13 Implementation / TDD Report

## Status

Implemented optional grounded AI explanations without adding any path by which AI
can change deterministic readiness, severity, decision eligibility, evidence,
fingerprints, or audit decisions. The authenticated report exposes an opt-in AI
request after the authoritative deterministic report. Disabled, refused, timed
out, malformed, ungrounded, or failed explanations collapse to HTTP 200
`{"state":"unavailable"}`.

Fix round 1 closes the persistence-transition, mixed-refusal, free-form prose,
exact-model, Unicode, and absolute-deadline findings. No live OpenAI request,
SQLite database, remote database, or remote migration was used.

## TDD evidence

### Grounding, provider, and configuration

- Initial Task 13 RED first failed because the official `openai` package and AI
  boundary did not exist. The official `openai==3.1.0` dependency and lock were
  added normally, with provider behavior tested only through injected fakes.
- Fix-round review reproduction confirmed all five application/provider findings
  against the committed code. Persistence RED was two failures and one error
  because head ended at 0003 and the guarded trigger did not exist. Exact-model
  RED was two failures; mixed-refusal/output RED was three failures; the four new
  grounding cases all failed; backend Unicode RED was nine failures; and deadline
  RED was four failures.
- The prompt projection includes only immutable normalized assessment facts:
  deterministic status, bounded release name/freshness, stored finding IDs,
  deterministic rule/severity/summary/action, and bounded evidence IDs/type/source
  IDs. It excludes bodies, comments, code, logs, URLs, fingerprints, candidate
  SHAs, credentials, actor reasons, secrets, and arbitrary prompts. All critical
  findings and at most 20 warnings are included.
- A single backend Unicode policy NFC-normalizes strings, rejects `Cc`, `Cf`
  (including bidi controls), `Cs` (including lone surrogates), `Zl`, and `Zp`, and
  enforces Python code-point limits. Prompt labels are normalized before their
  200-code-point truncation; structured output fields use the same policy without
  truncating. Tests cover controls, bidi, surrogates, line/paragraph separators,
  NFD input, and exact 200/201 astral-code-point boundaries.
- Structured output retains the required strict Pydantic schema fields. The
  application now requires exact coverage of every supplied finding and evidence
  ID, exactly one group and action occurrence per finding, deterministic severity,
  exact supplied actions, and the exact deterministic evidence union for every
  group and action. Unknown, omitted, duplicate, conflicting, partially grounded,
  or over-bounded content is rejected.
- AI can choose only bounded grouping. Accepted `summary`, group title/prose,
  limitations, confidence, ID order, and action/reference order are replaced with
  deterministic canonical values and ordering. Thus hostile claims using otherwise
  valid IDs cannot reach rendering. Legitimate multi-finding groups and input
  permutations are covered.
- Configuration and provider construction accept only the literal model alias
  `gpt-5.6`; any direct or environment-derived substitution fails validation.
  `OPENAI_API_KEY` remains optional. Prices remain required only when enabled and
  are bounded nonnegative `Decimal` values.

### Provider request, refusal, deadline, and metadata boundary

- The installed SDK's current typed `AsyncResponses.parse` accepts `model`,
  `input`, `text_format`, `store`, and the per-call `timeout` argument. Production
  calls use exact `gpt-5.6`, `text_format=AIExplanation`, `store=False`, no tools,
  and an SDK client with internal retries disabled.
- Before reading `output_parsed`, the provider walks every `response.output`
  message/content item. Any refusal, including mixed refusal plus parsed text, is
  unavailable. Refusal-only, mixed, parsed-only, malformed output, and missing
  parsed content are covered.
- Every attempt receives only the remaining absolute 15-second budget as its SDK
  timeout. An outer `asyncio.wait` uses the same deadline. At expiry, the provider
  detaches and cancels the SDK task without awaiting cancellation-suppressing work,
  returns unavailable immediately, owns the late task, and consumes its eventual
  result/exception. Exact-deadline results are rejected. Only the first 429/5xx may
  retry, and only while budget remains; timeout never retries.
- Metadata records provider-observed model, elapsed latency, observed input/output
  tokens, and six-decimal cost computed with `Decimal` from required configured
  per-million prices. Models, latency, usage, prices, arithmetic, and cost remain
  bounded before response serialization. Raw prompts, secrets, keys, and provider
  errors are not logged or exposed.

### PostgreSQL transition and retention contract

- Migration `0004_ai_explanation_transitions` replaces 0001's blanket explanation
  UPDATE trigger. New rows must be structurally exact pending reservations.
- A pending row can transition exactly once to exact unavailable or to an available
  object containing exactly `state`, object `explanation`, and object `metadata`.
  Its ID, analysis-run ownership, and creation time cannot change. Pending no-op,
  invalid terminal shapes, terminal updates, direct deletes, and terminal inserts
  fail at the database boundary.
- Direct explanation deletion is rejected at trigger depth one. Nested repository
  retention cascades remain permitted, matching the existing PostgreSQL retention
  design. Downgrade restores the original immutable UPDATE trigger.
- Real PostgreSQL contracts cover upgrade from 0003 with historical terminal and
  pending rows, success/failure terminals, transaction rollback/crash behavior,
  forbidden insert/mutation/delete, parent cascade, and concurrent one-winner
  transitions. They were collected only; PostgreSQL was unavailable, so this
  report does not claim runtime PostgreSQL GREEN.

### Route and UI boundary

- The POST route remains authenticated, repository-authorized, and protected by
  global unsafe-method CSRF enforcement. It loads only the immutable persisted
  assessment and never persists or changes readiness. Unauthorized runs remain
  hidden as 404; every provider/refusal/validation/persistence failure returns the
  exact sanitized unavailable response.
- With no key, production wiring creates no OpenAI client or explanation service;
  startup succeeds and the authorized route returns unavailable. The unique
  explanation reservation still permits one logical provider attempt per run
  across repeated/concurrent service instances.
- The UI remains visibly subordinate to the deterministic report, labels the
  section “AI explanation,” repeats but does not replace deterministic readiness,
  renders no AI-created evidence links, safely escapes content, and covers idle,
  loading, unavailable, disabled, and available states.
- The client response boundary requires NFC, matching prohibited Unicode
  categories, trimmed nonempty strings, and `Array.from` code-point bounds. It
  rejects bidi, control, surrogate, separator, non-NFC, and 201-astral-character
  payloads while accepting exactly 200 astral characters.

## Verification

- Backend unit/contract/non-database route integration: 702 passed with
  `-W error --hypothesis-seed=0`.
- Focused grounding/provider/migration: 63 passed; focused route/auth/fallback:
  8 passed.
- Backend Ruff and strict mypy: passed for 43 source files. Scoped Ruff formatting:
  passed.
- Frontend Vitest: 61 passed across six files. TypeScript, ESLint, and Vite
  production build: passed.
- `uv lock --check --offline`: 53 packages resolved.
- `pnpm install --frozen-lockfile --offline`: up to date.
- Offline PostgreSQL upgrade through 0004 and full 0004-to-base downgrade SQL:
  generated successfully.
- PostgreSQL integration collection: 39 contracts, including six new explanation
  transition/upgrade contracts; not executed locally.
- `git diff --check`: passed.

## Concerns

PostgreSQL is unavailable locally by project constraint, so real database runtime
and migration contracts were collected but not executed; no SQLite or remote
substitute was used. A process crash after a committed pending reservation
intentionally leaves that run unavailable rather than risking a second provider
attempt. If an SDK coroutine ignores cancellation, the route still returns at the
absolute deadline; the provider retains that task only until it finishes so a late
exception cannot become unhandled.
