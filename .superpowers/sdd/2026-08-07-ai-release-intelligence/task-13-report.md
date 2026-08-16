# Task 13 Implementation / TDD Report

## Status

Implemented optional grounded AI explanations without adding any path by which AI
can change deterministic readiness, severity, decision eligibility, evidence,
fingerprints, or audit decisions. The authenticated report exposes an opt-in AI
request after the authoritative deterministic report. Disabled, refused, timed
out, malformed, ungrounded, or failed explanations collapse to HTTP 200
`{"state":"unavailable"}`.

## TDD evidence

### Grounding and provider RED/GREEN

- Initial focused RED failed collection because the official `openai` package was
  absent. After adding the official package and lock, RED moved to the intended
  missing application boundary: `release_intelligence.adapters.ai` did not exist.
- The first grounding/provider GREEN was 22 passed. Additional schema, refusal,
  rate-limit, and non-retryable-status cases brought focused grounding coverage to
  33 tests.
- The prompt projection includes only immutable normalized assessment facts:
  deterministic status, bounded release name/freshness, stored finding IDs,
  deterministic rule/severity/summary/action, and bounded evidence IDs/type/source
  IDs. It excludes snapshot bodies, check log content, comments, code, URLs,
  fingerprints, candidate SHA, credentials, actor reasons, and arbitrary prompts.
- All blocking, decision-required, and insufficient-data findings are included;
  other warning/advisory findings are limited to 20. Untrusted names and titles are
  NFC-normalized, control-cleaned, and truncated to 200 Unicode code points.
- Model output uses an exact extra-forbidden Pydantic Structured Outputs schema
  containing only `summary`, `groups`, `actions`, `limitations`, `confidence`,
  `finding_ids`, and `evidence_ids`. Provider metadata is a Pydantic private
  attribute and is absent from the model-facing schema.
- Application validation rejects unknown or duplicate IDs, evidence not linked to
  the cited finding, severity conflicts, invented actions, unlinked actions,
  repeated/conflicting finding groups/actions, mismatched summary references, and
  malformed or unbounded values. Accepted collections are normalized into a safe
  deterministic order.

### Provider and metadata boundary

- Added official `openai==3.1.0` through normal `uv add`, updating both
  `pyproject.toml` and `uv.lock`. No live provider request was made.
- The installed SDK's typed `AsyncResponses.parse` interface matches the plan:
  `model`, `input`, `text_format`, and `store` remain supported. The current SDK
  also exposes a per-call `timeout`; the implementation retains the plan's outer
  total-budget timeout so a retry cannot reset the 15-second budget.
- Each permitted generation performs one logical `responses.parse` Structured
  Outputs operation using exact alias `gpt-5.6`, `text_format=AIExplanation`,
  `store=False`, and no tools. The production SDK client has internal retries
  disabled. One application retry is allowed only for 429 or 5xx while time remains.
- Refusal, timeout, SDK/API error, parse error, invalid usage, cost overflow, and
  schema/reference rejection expose only the safe unavailable state.
- Metadata records the provider-observed model, elapsed latency, observed input and
  output tokens, and a six-decimal `Decimal` cost calculated only from required
  configured current per-million prices. Model names, prices, tokens, latency, and
  cost are finite, nonnegative, and bounded before logging or response serialization.

### Route/configuration RED/GREEN

- The route/configuration RED was exactly 14 failures: seven missing optional and
  bounded settings plus seven missing route/wiring behaviors. Focused GREEN was 36
  passed before later single-attempt coverage.
- `OPENAI_API_KEY` remains optional (`ARI_OPENAI_API_KEY` under the application's
  existing environment prefix). With no key, prices remain unset, startup succeeds,
  no OpenAI client is constructed, and the route returns unavailable. With a key,
  both current Decimal input/output prices are mandatory and bounded. The default
  model is exactly `gpt-5.6`.
- The route loads only the stored analysis run, authenticates the current session,
  verifies repository authorization before provider access, and inherits the
  global unsafe-method CSRF enforcement. Unauthorized runs are hidden as 404.
- A final lifecycle audit added a repeated-request RED: four route cases showed two
  provider calls where only one was allowed. The single-attempt service guard and
  production reservation store made all eight route cases green. The existing
  unique `ai_explanations.analysis_run_id` row atomically reserves a run before the
  provider call, records either bounded available content or terminal unavailable,
  and prevents repeated or concurrent service instances from invoking the provider
  again. No migration was needed. Injected/fake services enforce the same invariant
  in memory.

### UI RED/GREEN

- Initial UI RED failed because `AIExplanation.tsx` did not exist. Component GREEN
  is six tests covering labeling, deterministic-status separation, grounded action
  rendering, no AI-created evidence links, escaped hostile text, and accessible
  loading/unavailable/disabled states.
- The report places the optional AI section after deterministic verdict, findings,
  required actions, decisions, and supporting evidence. It visually uses a dashed,
  muted panel and explicitly says it cannot change the deterministic report.
- The authenticated App presents an opt-in generation button, sends same-origin
  CSRF-protected POST, handles loading/available/unavailable without hiding the
  deterministic report, validates the bounded response shape before rendering, and
  never converts AI evidence IDs into links.

## Verification

- Backend unit/contract/non-database integration: 672 passed with `-W error` and
  deterministic Hypothesis seed `0`.
- Focused AI grounding/route coverage: 41 passed.
- Backend Ruff: passed.
- Strict mypy: passed for 43 source files.
- Scoped Ruff formatting: passed.
- Frontend Vitest: 54 passed across six files.
- Frontend TypeScript, ESLint, and Vite production build: passed.
- `uv lock --check --offline`: 53 packages resolved.
- `pnpm install --frozen-lockfile --offline`: up to date.
- Offline PostgreSQL SQL generation: `0001_initial:head` upgrade and
  `head:0001_initial` downgrade passed using an explicit non-connecting local dummy
  URL.
- PostgreSQL integration collection: 33 repository/migration contracts.
- `git diff --check`: passed.

## Concerns

PostgreSQL is unavailable locally by project constraint, so the real database
contracts were collected but not executed; no SQLite or remote substitute was
used. The production single-attempt adapter uses the existing PostgreSQL
`ai_explanations` unique row and required no schema change. A process crash after
reservation intentionally leaves that run unavailable rather than risking a second
provider request, preserving the stricter one-attempt and deterministic-fallback
contract.
