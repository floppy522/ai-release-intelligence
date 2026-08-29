# Threat Model

## Scope and assurance level

This is a project threat model for the AI Release Intelligence MVP, not a
penetration-test report. It describes controls that are present in the current
repository and the risks that remain; it does not claim independent testing of
a deployed environment, GitHub, OpenAI, PostgreSQL, the runner, or the network
and TLS configuration around them.

The assurance basis is source inspection plus deterministic automated tests.
The model covers the browser-facing API, GitHub OAuth and App integrations,
PostgreSQL persistence, deterministic readiness evaluation, optional AI
explanations, the web renderer, logging, and repository CI. Deployment-specific
identity administration, host hardening, TLS termination, backups, monitoring,
and incident response are outside the implemented evidence boundary.

## Protected assets

- GitHub OAuth codes, user access tokens, GitHub App private-key material,
  installation tokens, the OAuth client secret, and the optional AI API key.
- Opaque session and CSRF bearer values, OAuth state and browser-binding values,
  and the server-side records derived from them.
- User-to-installation and installation-to-repository authorization mappings.
- Private repository metadata, normalized snapshots, policies, findings,
  evidence identifiers, and human decision reasons.
- The integrity of deterministic readiness status, evidence lineage, decision
  eligibility, and the fingerprints to which accepted risks are bound.
- Application, dependency, proxy, CI, and benchmark logs or artifacts that
  could expose credentials or private repository data.

## Actors

- An authenticated GitHub user evaluating repositories available through an
  authorized GitHub App installation.
- A repository contributor whose issue, pull-request, label, branch, and check
  text is untrusted even when GitHub authenticated it.
- An unauthenticated or authenticated attacker seeking another user's session,
  repository data, credentials, or an incorrect readiness result.
- GitHub as the OAuth identity provider, App-token issuer, and evidence source.
- The optional AI provider and model, which are untrusted for readiness and
  receive only a bounded explanation projection.
- Operators and maintainers with access to runtime configuration, the database,
  CI settings, logs, dependencies, or deployment infrastructure.

## Trust boundaries

| Boundary | Data crossing it | Enforced in this repository |
| --- | --- | --- |
| Browser to API | OAuth callback parameters, cookies, CSRF header, repository selections, policy and decision requests | Server-side session lookup, unsafe-method CSRF middleware, Pydantic request validation, and repository authorization |
| API to GitHub OAuth | Authorization state, browser code, client credentials, user token | Browser-bound single-use state, bounded callback inputs, TLS URLs, `SecretStr`, and caller-safe errors |
| API to GitHub App/REST | App JWT, installation ID and token, repository-specific requests, paginated responses | Short-lived App JWT, non-persisted installation token, fixed API origin, timeouts, same-origin pagination, bounded pages, and strict response mapping |
| GitHub data to deterministic domain and database | Repository-controlled text and evidence metadata | Typed and bounded mapping, evidence URL validation, bounded evidence windows, immutable normalized snapshots, and fail-closed assessment |
| API to PostgreSQL | Encrypted user credential, digested bearer material, authorization mappings, snapshots, decisions | Fernet authenticated encryption, one-way token digests, transactions, row locking, repository identity checks, and immutable decision lineage |
| Deterministic result to AI provider | Selected findings, exact actions, identifiers, and bounded repository-derived text | Explicit input allowlist, no tools or secrets, structured output, post-generation grounding validation, `store=False`, and safe unavailability fallback |
| API result to browser UI | Untrusted deterministic and AI text plus evidence URLs | React text rendering and an independent client-side canonical evidence-link check |
| Repository to CI runner | Source, lockfiles, workflows, third-party actions and packages | Read-only workflow permissions, actions pinned to commit SHAs, locked installs, and security/benchmark jobs |

The production Compose file keeps PostgreSQL on an internal network and binds
the web port to loopback. It does not establish an internet-facing TLS boundary;
`Secure` cookies depend on a correctly configured HTTPS deployment outside this
repository.

## Entry points

- `GET /api/auth/github/login` and `GET /api/auth/github/callback`, including
  query parameters returned through the browser.
- The `session` and `oauth_binding` cookies, `GET /api/auth/csrf`, and the
  `X-CSRF-Token` header on unsafe methods.
- Authenticated repository, policy, analysis, decision, and explanation API
  routes under `/api/`.
- GitHub OAuth, App installation-token, REST, and pagination responses.
- GitHub issue, pull-request, branch, label, check, timeline, and milestone data
  normalized into a release snapshot.
- PostgreSQL contents and environment-supplied deployment credentials.
- Optional AI requests and structured responses.
- Nginx/Uvicorn/application/dependency logs, GitHub Actions workflows,
  dependency registries, container registries, and uploaded CI artifacts.

## Threats and controls

| Threat | Asset / boundary | Implemented control | Residual risk | Evidence |
| --- | --- | --- | --- | --- |
| OAuth state interception or replay | OAuth callback and authenticated identity | Login creates independent high-entropy state and browser-binding values. Only their SHA-256 digests are stored. The binding is in a `Secure`, `HttpOnly`, `SameSite=Lax` cookie scoped to the callback, and the store atomically deletes a matching, unexpired pair before code exchange. Callback parameters are bounded. | An attacker who obtains the code, state, and binding cookie together may still win the callback race. There is no PKCE control in this implementation. OAuth query values can also reach infrastructure logs outside the application filter. | [`auth.py`](../../apps/api/src/release_intelligence/api/routes/auth.py), [`crypto.py`](../../apps/api/src/release_intelligence/security/crypto.py), [`AuthRepository`](../../apps/api/src/release_intelligence/adapters/persistence/auth.py), [`test_auth_routes.py`](../../apps/api/tests/integration/test_auth_routes.py) |
| Session theft | Browser/API session boundary | The browser receives an opaque high-entropy cookie with `Secure`, `HttpOnly`, `SameSite=Lax`, and a bounded lifetime. PostgreSQL stores only its digest; every request resolves an unexpired server-side session, and logout deletes it. | A stolen session bearer remains usable until expiry or logout. The code has no per-request rotation, device binding, session inventory, or theft detection; TLS and endpoint security are deployment responsibilities. | [`auth.py`](../../apps/api/src/release_intelligence/api/routes/auth.py), [`dependencies.py`](../../apps/api/src/release_intelligence/api/dependencies.py), [`crypto.py`](../../apps/api/src/release_intelligence/security/crypto.py) |
| CSRF against a state-changing route | Browser/API boundary | Global middleware covers `POST`, `PUT`, `PATCH`, and `DELETE`, requires a valid server-side session and an `X-CSRF-Token` whose digest matches that session, and caches the verified context for route dependencies. The authenticated bootstrap response is `no-store` with a no-referrer policy; logout also declares the CSRF dependency. | Same-origin script execution can retrieve the token and act as the user. The guarantee also depends on future state changes not being added to safe HTTP methods or bypassed outside the middleware. | [`main.py`](../../apps/api/src/release_intelligence/main.py), [`dependencies.py`](../../apps/api/src/release_intelligence/api/dependencies.py), [`auth.py`](../../apps/api/src/release_intelligence/api/routes/auth.py), [`test_auth_routes.py`](../../apps/api/tests/integration/test_auth_routes.py) |
| Cross-installation or cross-repository access | Authorization mappings and private analysis data | Repository lookup joins the current GitHub user to an installation-access row and the requested repository connection. Analysis creation uses the installation ID returned by that lookup. Fetching runs, creating decisions, generating explanations, and reading/writing policies re-check repository access; run lookups hide denials as `404`. Decision persistence repeats the authorized repository identity check inside the transaction. | Correctness depends on installation-access provisioning and timely revocation. The repository does not show webhook-driven reconciliation of GitHub membership or installation changes, so a stale local mapping persists until removed or GitHub rejects upstream access. | [`dependencies.py`](../../apps/api/src/release_intelligence/api/dependencies.py), [`AuthRepository`](../../apps/api/src/release_intelligence/adapters/persistence/auth.py), [`releases.py`](../../apps/api/src/release_intelligence/api/routes/releases.py), [`test_authorization.py`](../../apps/api/tests/security/test_authorization.py) |
| GitHub installation-token exposure | API/GitHub boundary | The API signs a ten-minute App JWT, mints an installation token on demand, wraps it in `SecretStr`, keeps it in process memory, passes it only as an authorization header, and does not persist or return it. HTTP dependency logs are redacted. | The token is available in API and HTTP-client memory until references are released and remains exposed to a compromised process, dependency, debugger, or GitHub endpoint. The application does not inspect token expiry or enforce GitHub App permission scope; those are upstream configuration controls. | [`GitHub App auth`](../../apps/api/src/release_intelligence/adapters/github/auth.py), [`GitHub client`](../../apps/api/src/release_intelligence/adapters/github/client.py), [`main.py`](../../apps/api/src/release_intelligence/main.py), [`test_github_app_auth.py`](../../apps/api/tests/unit/test_github_app_auth.py) |
| Credential-at-rest exposure | PostgreSQL and runtime configuration | Long-lived user tokens are Fernet-encrypted before persistence with a deployment-owned key. OAuth state, browser bindings, session tokens, and CSRF values are persisted only as one-way digests. Configuration represents secrets as `SecretStr` and rejects malformed key material. | Database ciphertext plus the single deployment key permits decryption. No KMS/envelope encryption, key version, automated rotation, or revocation workflow is implemented; environment and host access remain privileged trust points. | [`crypto.py`](../../apps/api/src/release_intelligence/security/crypto.py), [`AuthRepository`](../../apps/api/src/release_intelligence/adapters/persistence/auth.py), [`config.py`](../../apps/api/src/release_intelligence/config.py), [`test_auth_repository.py`](../../apps/api/tests/integration/test_auth_repository.py) |
| Unsafe evidence URL or SSRF/open-redirect link | GitHub data/domain/UI boundaries | Evidence parsing does not dereference the value. It accepts only exact ASCII `https://github.com` URLs with no credentials, explicit port, query, fragment, percent encoding, backslash, or path ambiguity; the repository and resource shapes are allowlisted and canonicalized. GitHub pagination links are separately restricted to the configured API origin before requests. The UI independently validates repository, resource type, and source identity before rendering a link. | A future consumer could bypass the parser and dereference stored raw data. GitHub/DNS/TLS compromise remains upstream, and accepted links intentionally navigate users to GitHub. | [`urls.py`](../../apps/api/src/release_intelligence/security/urls.py), [`GitHub client`](../../apps/api/src/release_intelligence/adapters/github/client.py), [`test_evidence_urls.py`](../../apps/api/tests/security/test_evidence_urls.py), [`web security test`](../../apps/web/src/test/security.test.tsx) |
| Stored untrusted GitHub text causing injection or resource abuse | GitHub data/database/API/UI boundary | The adapter enforces payload types and explicit bounds, including 512-character titles and 65,536-character issue bodies; the loader bounds collections and stores a normalized snapshot rather than full raw responses, source, comments, or CI logs. Deterministic rules treat strings as data. React renders hostile strings as text and does not use raw HTML for findings or explanations. | Untrusted text is still retained in normalized snapshots and returned in some API fields. A future renderer, export, log statement, or query could introduce a new injection sink, and accepted per-field limits do not eliminate storage or display abuse. | [`mapper.py`](../../apps/api/src/release_intelligence/adapters/github/mapper.py), [`analyze_release.py`](../../apps/api/src/release_intelligence/application/analyze_release.py), [`test_prompt_injection.py`](../../apps/api/tests/security/test_prompt_injection.py), [`web security test`](../../apps/web/src/test/security.test.tsx) |
| Prompt injection or unsupported AI claims | Deterministic-result/AI boundary | AI input is an allowlisted projection of selected findings, exact actions and evidence IDs; repository-derived text is Unicode-normalized, rejected on unsafe categories, and truncated. The prompt labels all values untrusted, the provider uses strict structured output with no tools and `store=False`, and application validation requires exact finding/evidence coverage, deterministic severities, and exact supplied actions. Summary, groups, actions, limitations, and confidence are canonicalized; rejection or provider failure returns `unavailable` without changing readiness. | Selected private metadata is disclosed to the configured provider. Provider retention and training guarantees are contractual, not proven here. A malicious provider can deny explanation availability, while the deterministic result remains authoritative. | [`explanations.py`](../../apps/api/src/release_intelligence/application/explanations.py), [`AI provider`](../../apps/api/src/release_intelligence/adapters/ai/openai_provider.py), [`AI schemas`](../../apps/api/src/release_intelligence/ports/ai.py), [`test_prompt_injection.py`](../../apps/api/tests/security/test_prompt_injection.py), [`test_ai_grounding.py`](../../apps/api/tests/unit/test_ai_grounding.py) |
| Stale risk decision applied to changed evidence | Decision fingerprint and readiness integrity | An advisory-check fingerprint hashes repository, candidate SHA, check name, run ID, and conclusion. The API exposes a fingerprint only while a finding is decision-eligible. Persistence locks the run, verifies repository/run/finding identity and exact fingerprint, reassesses the same immutable snapshot and policy, and appends the decision plus reassessment atomically. Snapshots older than ten minutes fail closed as `INSUFFICIENT_DATA`. | The decision transaction does not re-fetch GitHub. Evidence may change inside the freshness window, and collision resistance depends on SHA-256. A later analysis creates a new snapshot rather than revoking old rows, so consumers must use the current run. | [`checks.py`](../../apps/api/src/release_intelligence/domain/rules/checks.py), [`decisions.py`](../../apps/api/src/release_intelligence/application/decisions.py), [`AnalysisRepository`](../../apps/api/src/release_intelligence/adapters/persistence/repositories.py), [`assessment.py`](../../apps/api/src/release_intelligence/domain/assessment.py), [`test_decision_reassessment.py`](../../apps/api/tests/unit/test_decision_reassessment.py) |
| Partial GitHub response, pagination manipulation, rate limit, or changing source window | GitHub/API boundary and readiness integrity | The REST adapter applies timeouts, strict payload mapping, a 20-page bound, same-origin next-link validation, and duplicate/count/cycle checks where identity is available. It classifies transport, malformed, non-success, and rate-limit responses without returning a partial collection. The loader collects the full evidence window twice, retries an inconsistent window once, and converts partial/rate-limited results into an incomplete snapshot; assessment then returns `INSUFFICIENT_DATA`. | Availability is fail-closed rather than recovered: there is no durable retry queue or backoff, repeated double collection consumes quota, and a source that changes continually prevents a decision. Some ordinary list endpoints cannot prove an upstream total when GitHub omits one. | [`GitHub client`](../../apps/api/src/release_intelligence/adapters/github/client.py), [`rate_limits.py`](../../apps/api/src/release_intelligence/adapters/github/rate_limits.py), [`analyze_release.py`](../../apps/api/src/release_intelligence/application/analyze_release.py), [`test_github_client.py`](../../apps/api/tests/contract/test_github_client.py) |
| Dependency or CI action compromise | Repository/CI/build boundary and all secrets available to a job | Python and Node installs use committed lockfiles with locked/frozen modes; `uv.lock` records package hashes. Third-party actions in the checked workflows are pinned to full commit SHAs, top-level workflow permissions are `contents: read`, live-secret workflows are manual and environment-scoped, and CI runs tests, linting, type checks, and the security benchmark. | Lockfiles and SHA pins preserve chosen artifacts but do not establish publisher trust. Base images are version-tagged rather than digest-pinned, and no SBOM, signature verification, provenance enforcement, or vulnerability scan is evident. A compromised runner, registry, pinned artifact, or maintainer can still subvert a build. | [`ci.yml`](../../.github/workflows/ci.yml), [`live AI workflow`](../../.github/workflows/live-ai-benchmark.yml), [`live GitHub workflow`](../../.github/workflows/live-github-smoke.yml), [`uv.lock`](../../apps/api/uv.lock), [`pnpm-lock.yaml`](../../apps/web/pnpm-lock.yaml) |
| Secret leakage through application or proxy logs | Application/dependency/proxy logging boundary | Application logging is allowlisted and fail-closed: unknown messages, arguments, exceptions, stacks, and extras are removed. `httpx`, `httpcore`, `openai`, and `sqlalchemy` records are redacted at record creation. The Uvicorn access filter strips the entire query, and the production container disables Uvicorn access logs. Tests inject OAuth values, tokens, key fragments, database URLs, prompt text, and hostile objects. | The filter covers named dependency families, not every possible logger or external collector. The checked-in Nginx configuration disables access logs only for `/healthz`; it does not redact `/api/` request targets, so OAuth callback code/state may enter proxy access logs. Platform, load-balancer, database, CI, and crash logs remain outside this control. | [`logging.py`](../../apps/api/src/release_intelligence/security/logging.py), [`test_secret_logging.py`](../../apps/api/tests/security/test_secret_logging.py), [`test_access_logging.py`](../../apps/api/tests/unit/test_access_logging.py), [`Nginx proxy`](../../apps/web/nginx.conf), [`API Dockerfile`](../../apps/api/Dockerfile) |

## Residual risks

The most important unresolved deployment risks are proxy query logging and the
absence of repository-proven TLS termination. Before an internet-facing launch,
the callback request target should be excluded or redacted at every proxy and
collector, and HTTPS plus trusted forwarding behavior must be verified end to
end.

Credential confidentiality still relies on one environment-supplied Fernet key
and the security of the API host. Session bearers are not rotated or bound to a
device, and local authorization can lag GitHub revocation. GitHub App permission
scope, secret rotation, backup encryption, log retention, and incident-response
procedures require operational controls not demonstrated by this codebase.

Deterministic readiness fails closed on stale or partial evidence, but that
protects integrity by reducing availability. A human decision is bound tightly
to an immutable snapshot and check fingerprint, not to a live re-fetch at the
moment of acceptance. The optional AI path does not control status, but it sends
selected private metadata to a third party and can become unavailable.

Supply-chain controls improve reproducibility but do not prove artifact or
runner integrity. Stored untrusted text is safely bounded and rendered in the
current paths, yet each new renderer, export, logger, URL consumer, AI field, or
repository provider creates a fresh injection boundary that needs review.

## Verification evidence

The repository's security evidence is deterministic test coverage, not an
external assessment:

| Security property | Verification |
| --- | --- |
| Repository-scoped authorization and hidden cross-repository runs | [`apps/api/tests/security/test_authorization.py`](../../apps/api/tests/security/test_authorization.py) |
| Repository-bound evidence URLs and SSRF/open-link payload rejection | [`apps/api/tests/security/test_evidence_urls.py`](../../apps/api/tests/security/test_evidence_urls.py) and [`apps/web/src/test/security.test.tsx`](../../apps/web/src/test/security.test.tsx) |
| Stored GitHub prompt injection cannot alter deterministic status or enter the AI allowlist | [`apps/api/tests/security/test_prompt_injection.py`](../../apps/api/tests/security/test_prompt_injection.py) |
| Application and dependency secrets fail closed under logging | [`apps/api/tests/security/test_secret_logging.py`](../../apps/api/tests/security/test_secret_logging.py) |
| OAuth replay, browser binding, session cookies, CSRF, encryption, and sanitized failures | [`apps/api/tests/integration/test_auth_routes.py`](../../apps/api/tests/integration/test_auth_routes.py) and [`apps/api/tests/integration/test_auth_repository.py`](../../apps/api/tests/integration/test_auth_repository.py) |
| OAuth/App token construction and caller-safe upstream failures | [`apps/api/tests/unit/test_github_app_auth.py`](../../apps/api/tests/unit/test_github_app_auth.py) |
| Query removal from application access logs | [`apps/api/tests/unit/test_access_logging.py`](../../apps/api/tests/unit/test_access_logging.py) |
| AI reference grounding, exact actions, canonicalization, refusal, timeout, and failure fallback | [`apps/api/tests/unit/test_ai_grounding.py`](../../apps/api/tests/unit/test_ai_grounding.py) |
| Fingerprinted decision reassessment and stale/conflicting decision rejection | [`apps/api/tests/unit/test_decision_reassessment.py`](../../apps/api/tests/unit/test_decision_reassessment.py) and [`apps/api/tests/integration/test_decision_repository.py`](../../apps/api/tests/integration/test_decision_repository.py) |
| Partial response, pagination-origin, duplicate, timeout, authorization, and rate-limit handling | [`apps/api/tests/contract/test_github_client.py`](../../apps/api/tests/contract/test_github_client.py) |

The focused security suite lives at
[`apps/api/tests/security/`](../../apps/api/tests/security/), and CI runs it both
with the API unit/contract suite and in the security-benchmark job in
[`.github/workflows/ci.yml`](../../.github/workflows/ci.yml).

## Review triggers

Review this model and its linked tests when any of the following changes:

- OAuth scopes, callback routing, state handling, cookies, session lifetime,
  CSRF coverage, login/logout behavior, or identity provider;
- GitHub App permissions, installation provisioning/revocation, token lifetime,
  REST origin, API version, pagination, retry, or evidence-window logic;
- repository authorization schema, tenancy assumptions, policy/run/decision
  routes, fingerprint inputs, freshness window, or immutable lineage;
- normalized GitHub fields, size limits, evidence URL families, UI rendering,
  exports, stored content, or any server-side URL dereference;
- AI provider, model, prompt, input allowlist, structured schema, grounding
  validator, retention setting, tools, retry, or fallback behavior;
- encryption algorithm or key handling, secret source, database/backup access,
  proxy/TLS topology, log formatter/collector, or retention policy;
- lockfiles, package manager, container base image, GitHub Action, workflow
  permission, artifact upload, runner, or production deployment path;
- a security incident, newly disclosed dependency vulnerability, new external
  tenant, or decision to expose the Compose topology beyond loopback.
