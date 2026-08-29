# AI Release Intelligence Portfolio Package Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a complete, evidence-backed portfolio documentation package for AI Release Intelligence and integrate three real deterministic-demo screenshots into the root README.

**Architecture:** The root README remains the short product entry point. Five focused portfolio documents and three ADRs provide deeper product, architecture, security, operational, evaluation, and decision context; all verified claims link back to the current implementation, CI, or public synthetic demo. Screenshots are captured from the existing credential-free E2E Compose stack after the text package is verified.

**Tech Stack:** GitHub-flavored Markdown, Mermaid, GitHub Actions, Docker Compose, React E2E fixture, Bash, Python standard library, Git

**Spec:** `docs/superpowers/specs/2026-08-28-portfolio-package-design.md`

## Global Constraints

- Use current implementation on `feature/ai-release-intelligence-mvp` as the primary source of truth.
- Present the 30–60 minute manual baseline as experience-based and unmeasured.
- Present the five-minute outcome as a design target, not a measured result.
- Do not claim external customer validation, adoption, willingness to pay, or measured time savings.
- Do not invent exact benchmark metric values when retained evidence proves only that the configured gate passed.
- Use only fictional public demo data in screenshots and portfolio evidence.
- Do not expose credentials, private repository names, browser profile data, or unrelated tabs.
- Do not change product behaviour, assessment rules, APIs, integrations, or deployment architecture.
- Keep PR #2 as draft until the final release-gate review is explicitly approved.
- Every document must be complete: no unfinished markers, placeholder secrets, or unsupported measured claims.
- Every shell command must match the current repository and be non-destructive by default.
- Every Mermaid diagram must use GitHub-compatible syntax.

## File Map

**Create:**

- `docs/portfolio/product-case.md` — product problem, user, hypothesis, delivered scope, demo, validation limits
- `docs/portfolio/architecture.md` — system context, components, data flow, decision lifecycle, failure boundaries
- `docs/portfolio/threat-model.md` — assets, trust boundaries, threats, controls, residual risks
- `docs/portfolio/operations-runbook.md` — startup, health, migrations, diagnostics, recovery, credential and data operations
- `docs/portfolio/benchmark-results.md` — evaluation design, verified CI gate, evidence classes, limitations
- `docs/adr/0001-deterministic-readiness-core.md` — deterministic authority decision
- `docs/adr/0002-modular-monolith.md` — deployment and module boundary decision
- `docs/adr/0003-fingerprinted-human-decisions.md` — stale-decision invalidation decision
- `docs/portfolio/assets/01-release-setup.png` — real setup-state screenshot
- `docs/portfolio/assets/02-needs-decision.png` — real advisory-decision screenshot
- `docs/portfolio/assets/03-ready-after-decision.png` — real ready-state screenshot

**Modify:**

- `README.md` — portfolio navigation and screenshot gallery
- PR #2 description — final HEAD, evidence, documentation inventory, merge gate

**Evidence sources:**

- `docs/superpowers/specs/2026-08-07-ai-release-intelligence-design.md`
- `apps/api/src/release_intelligence/domain/`
- `apps/api/src/release_intelligence/application/`
- `apps/api/src/release_intelligence/adapters/github/`
- `apps/api/src/release_intelligence/security/`
- `apps/api/src/release_intelligence/api/routes/`
- `compose.yaml`
- `compose.test.yaml`
- `.env.example`
- `.github/workflows/ci.yml`
- `benchmarks/README.md`
- `benchmarks/scenarios/catalog.yaml`
- `tests/e2e/release-readiness.spec.ts`
- public demo workflow run `33214335082`
- successful portfolio README CI commit `5f5837e483f855394a0dddf07e81a2643be8f787`

---

### Task 1: Product Case

**Files:**

- Create: `docs/portfolio/product-case.md`

**Interfaces:**

- Consumes: approved product problem, persona, JTBD, MVP scope, verified demo evidence, explicit validation limitations
- Produces: the recruiter-facing product narrative linked from README

- [ ] **Step 1: Create the document with the exact section structure**

Write these headings in order:

```markdown
# Product Case

## Executive summary
## User and job to be done
## Current release-readiness workflow
## Product hypothesis
## Decision principles
## Delivered MVP
## Verified demo walkthrough
## Success measures and evidence status
## Current limitations
## Next validation steps
```

Include these factual boundaries:

- persona: Release Manager or Technical Project Manager in a small GitHub-native team;
- JTBD: obtain a trustworthy status and prioritized required actions within minutes without manually reconciling GitHub objects;
- experience-based manual baseline: 30–60 minutes, not externally measured;
- target: no more than five minutes, not yet validated;
- LLM never determines readiness;
- delivered states: `READY`, `NOT_READY`, `NEEDS_DECISION`, `INSUFFICIENT_DATA`;
- public synthetic demo links;
- no external interviews completed before design.

- [ ] **Step 2: Add a concise delivered-versus-unvalidated table**

Use three rows:

| Evidence class | Include |
| --- | --- |
| Verified | implementation, CI, demo topology, workflow jobs |
| Target | correctness thresholds, five-minute outcome |
| Unvalidated | adoption, demand, willingness to pay, measured human time saving |

- [ ] **Step 3: Verify factual language**

Run:

```bash
rg -n "30–60|five minutes|not.*validat|LLM|READY|NOT_READY|NEEDS_DECISION|INSUFFICIENT_DATA"   docs/portfolio/product-case.md
git diff --check -- docs/portfolio/product-case.md
```

Expected: every required boundary appears; `git diff --check` produces no output.

- [ ] **Step 4: Commit**

```bash
git add docs/portfolio/product-case.md
git commit -m "docs: add AI release intelligence product case"
```

---

### Task 2: Architecture Narrative

**Files:**

- Create: `docs/portfolio/architecture.md`

**Interfaces:**

- Consumes: current modular-monolith source layout, approved design, Compose topology
- Produces: technical-interview architecture narrative and diagrams

- [ ] **Step 1: Create the architecture sections**

Use:

```markdown
# Architecture

## System context
## Component boundaries
## Analysis sequence
## Evidence and persistence model
## Human-decision lifecycle
## AI explanation boundary
## Deployment topology
## Failure behaviour
## Source map
```

- [ ] **Step 2: Add three GitHub-compatible Mermaid diagrams**

Diagram 1: system context with User, React UI, FastAPI API, GitHub, PostgreSQL,
and optional OpenAI provider.

Diagram 2: analysis sequence:

```text
request → GitHub fetch → normalize → persist immutable snapshot
→ deterministic findings → assessment → optional explanation
```

Diagram 3: decision lifecycle:

```text
advisory finding → fingerprint → human decision
→ valid while fingerprint matches → invalid after evidence change
```

Keep each diagram under eight nodes and quote labels containing punctuation.

- [ ] **Step 3: Document implemented failure behaviour**

Cover these exact outcomes:

- partial or stale mandatory GitHub data cannot produce `READY`;
- GitHub rate-limit state is preserved and analysis becomes insufficient;
- a database failure rolls back the run;
- an invalid evidence URL is rejected;
- a malformed, refused, or timed-out AI response leaves the deterministic report intact.

- [ ] **Step 4: Verify links and diagram count**

Run:

```bash
test "$(rg -c '^\`\`\`mermaid$' docs/portfolio/architecture.md)" -eq 3
rg -n "INSUFFICIENT_DATA|rate limit|roll back|evidence URL|AI"   docs/portfolio/architecture.md
git diff --check -- docs/portfolio/architecture.md
```

Expected: three Mermaid blocks and no whitespace errors.

- [ ] **Step 5: Commit**

```bash
git add docs/portfolio/architecture.md
git commit -m "docs: add portfolio architecture narrative"
```

---

### Task 3: Threat Model

**Files:**

- Create: `docs/portfolio/threat-model.md`

**Interfaces:**

- Consumes: authentication, crypto, logging, GitHub adapter, AI validation, authorization and security tests
- Produces: reviewable security model with explicit residual risk

- [ ] **Step 1: Create the threat-model sections**

Use:

```markdown
# Threat Model

## Scope and assurance level
## Protected assets
## Actors
## Trust boundaries
## Entry points
## Threats and controls
## Residual risks
## Verification evidence
## Review triggers
```

State that this is a project threat model, not a penetration-test report.

- [ ] **Step 2: Add the threat table**

Include at least these columns:

| Threat | Asset / boundary | Implemented control | Residual risk | Evidence |

Cover:

- OAuth state interception or replay;
- session theft;
- CSRF;
- cross-installation repository access;
- GitHub installation-token exposure;
- credential-at-rest exposure;
- unsafe evidence URL / SSRF;
- stored untrusted GitHub text;
- prompt injection and unsupported AI claims;
- stale risk decisions;
- partial GitHub responses and rate limits;
- dependency or CI action compromise;
- secret leakage through application or proxy logs.

- [ ] **Step 3: Link controls to implementation**

Reference exact paths including:

- `apps/api/src/release_intelligence/api/routes/auth.py`;
- `apps/api/src/release_intelligence/api/dependencies.py`;
- `apps/api/src/release_intelligence/security/crypto.py`;
- `apps/api/src/release_intelligence/security/logging.py`;
- `apps/api/src/release_intelligence/adapters/github/auth.py`;
- `apps/api/src/release_intelligence/application/explanations.py`;
- `apps/api/tests/security/`.

- [ ] **Step 4: Verify coverage**

Run:

```bash
rg -n "OAuth|session|CSRF|installation|credential|SSRF|prompt|stale|partial|dependency|logs"   docs/portfolio/threat-model.md
git diff --check -- docs/portfolio/threat-model.md
```

Expected: every required threat class appears and no whitespace errors exist.

- [ ] **Step 5: Commit**

```bash
git add docs/portfolio/threat-model.md
git commit -m "docs: add project threat model"
```

---

### Task 4: Operations Runbook

**Files:**

- Create: `docs/portfolio/operations-runbook.md`

**Interfaces:**

- Consumes: `.env.example`, production/test Compose files, Alembic configuration, health and cleanup scripts
- Produces: safe developer/evaluator operating procedure

- [ ] **Step 1: Create the runbook sections**

Use:

```markdown
# Operations Runbook

## Scope
## Prerequisites
## GitHub App configuration
## Environment configuration
## Start and verify
## Database migrations
## Stop and restart
## Deterministic demo stack
## Diagnostics
## Recovery procedures
## Credential rotation
## Data retention and cleanup
## Escalation conditions
```

- [ ] **Step 2: Add exact normal-operation commands**

Production-style local stack:

```bash
cp .env.example .env
docker compose config --quiet
docker compose up --build -d --wait
curl --fail --silent --show-error http://127.0.0.1:8080/healthz
docker compose ps
docker compose down
```

Credential-free deterministic stack:

```bash
docker compose -f compose.test.yaml config --quiet
docker compose -f compose.test.yaml up --build -d --wait
curl --fail --silent --show-error http://127.0.0.1:4173/healthz
docker compose -f compose.test.yaml down
```

State clearly that `docker compose down -v` deletes the corresponding named
database volume and requires an explicit data-destruction decision.

- [ ] **Step 3: Add diagnostic commands**

Include:

```bash
docker compose ps --all
docker compose logs --no-color --timestamps --tail=200 postgres migrate api web
docker compose exec api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).read().decode())"
```

Map common symptoms to the following boundaries: configuration validation,
PostgreSQL health, migration failure, API health, GitHub authorization/rate
limit, OAuth callback, and optional AI failure.

- [ ] **Step 4: Define recovery and escalation**

Recovery must prefer:

1. preserve logs;
2. stop without deleting volumes;
3. correct configuration or upstream availability;
4. restart and verify health;
5. perform a new analysis run rather than reuse partial state.

Escalate before deleting volumes, rotating a shared credential, changing a
migration, weakening a security control, or accepting a release risk.

- [ ] **Step 5: Verify command accuracy**

Run:

```bash
docker compose -f compose.test.yaml config --quiet
rg -n "down -v|rate limit|OAuth|migration|rotate|escalat"   docs/portfolio/operations-runbook.md
git diff --check -- docs/portfolio/operations-runbook.md
```

Expected: Compose configuration validates and destructive behaviour is explicit.

- [ ] **Step 6: Commit**

```bash
git add docs/portfolio/operations-runbook.md
git commit -m "docs: add operations runbook"
```

---

### Task 5: Benchmark Evidence Report

**Files:**

- Create: `docs/portfolio/benchmark-results.md`

**Interfaces:**

- Consumes: benchmark catalog, methodology, CI workflow, verified workflow run for current documentation baseline
- Produces: accurate evaluation report without invented metrics

- [ ] **Step 1: Create the report sections**

Use:

```markdown
# Benchmark Evidence

## Evidence status
## Scenario catalog
## Deterministic gate
## Verified CI execution
## Live-provider evaluation
## Human-vs-tool experiment
## Interpretation limits
## Reproduction
```

- [ ] **Step 2: Document the configured thresholds**

Use this exact table:

| Metric | Gate |
| --- | ---: |
| Readiness agreement with ground truth | ≥95% |
| Critical blocker recall | 100% |
| Risk precision | ≥95% |
| Evidence coverage | 100% |
| Invalid evidence references | 0% |

Explain that current retained evidence proves the configured gate and all 44
scenarios completed successfully in CI, but does not justify publishing exact
per-metric values until the artifact is retained and reviewed.

- [ ] **Step 3: Link verified execution**

Reference:

- commit `5f5837e483f855394a0dddf07e81a2643be8f787`;
- CI workflow `.github/workflows/ci.yml`;
- `security-benchmark` job;
- `benchmarks/scenarios/catalog.yaml`;
- `benchmarks/README.md`.

Distinguish deterministic CI from the secret-protected live OpenAI benchmark
and the not-yet-run human crossover experiment.

- [ ] **Step 4: Add the reproduction command**

```bash
cd apps/api
uv run python -m release_intelligence.benchmark.runner \
  --catalog ../../benchmarks/scenarios/catalog.yaml \
  --output ../../benchmark-results.json
```

- [ ] **Step 5: Verify claim boundaries**

Run:

```bash
rg -n "44|≥95%|100%|0%|does not|not-yet|5f5837e"   docs/portfolio/benchmark-results.md
git diff --check -- docs/portfolio/benchmark-results.md
```

Expected: both verified facts and missing evidence are explicit.

- [ ] **Step 6: Commit**

```bash
git add docs/portfolio/benchmark-results.md
git commit -m "docs: add benchmark evidence report"
```

---

### Task 6: ADR 0001 — Deterministic Readiness Authority

**Files:**

- Create: `docs/adr/0001-deterministic-readiness-core.md`

**Interfaces:**

- Consumes: deterministic rules and optional explanation boundary
- Produces: durable rationale for excluding the LLM from Go/No-Go authority

- [ ] **Step 1: Write the ADR**

Use headings:

```markdown
# ADR 0001: Keep Release Readiness Deterministic

## Status
Accepted

## Context
## Decision
## Consequences
## Rejected alternatives
## Evidence
```

Decision: identical normalized snapshot, policy, and valid human decisions must
produce identical readiness; AI may explain but cannot set status, severity,
evidence, or acceptance.

Rejected alternatives: LLM-only decision, hybrid LLM score, and human-only
manual checklist.

- [ ] **Step 2: Verify and commit**

```bash
rg -n "Accepted|identical|cannot|LLM-only|hybrid|manual"   docs/adr/0001-deterministic-readiness-core.md
git diff --check -- docs/adr/0001-deterministic-readiness-core.md
git add docs/adr/0001-deterministic-readiness-core.md
git commit -m "docs: record deterministic readiness ADR"
```

---

### Task 7: ADR 0002 — Modular Monolith

**Files:**

- Create: `docs/adr/0002-modular-monolith.md`

**Interfaces:**

- Consumes: current application, domain, ports, adapters, and single Compose deployment
- Produces: rationale for one deployable with internal boundaries

- [ ] **Step 1: Write the ADR**

Decision: keep one FastAPI deployable with isolated domain, application, port,
adapter, and API modules; keep React and PostgreSQL as external runtime
components.

Consequences:

- lower deployment and operational complexity;
- transactionally consistent persistence;
- testable boundaries without network hops;
- future extraction remains possible through ports;
- independent scaling and failure isolation are limited.

Rejected alternatives: microservices, serverless functions per rule, and a
single unstructured application module.

- [ ] **Step 2: Verify and commit**

```bash
rg -n "Accepted|deployable|domain|application|adapter|microservices|serverless|scaling"   docs/adr/0002-modular-monolith.md
git diff --check -- docs/adr/0002-modular-monolith.md
git add docs/adr/0002-modular-monolith.md
git commit -m "docs: record modular monolith ADR"
```

---

### Task 8: ADR 0003 — Fingerprinted Human Decisions

**Files:**

- Create: `docs/adr/0003-fingerprinted-human-decisions.md`

**Interfaces:**

- Consumes: advisory finding identity, snapshot state, human-decision persistence
- Produces: rationale for invalidating stale waivers

- [ ] **Step 1: Write the ADR**

Decision: bind every accepted-risk or release-blocker decision to repository,
candidate SHA, check identity, run identity, conclusion, and rule/evidence
fingerprint. A changed fingerprint requires a new decision.

Consequences:

- waivers are auditable and cannot silently survive changed evidence;
- repeated decisions may create user friction;
- identity construction becomes a security- and correctness-critical contract.

Rejected alternatives: decision by check name only, permanent repository-level
waiver, and unstructured chat approval.

- [ ] **Step 2: Verify and commit**

```bash
rg -n "Accepted|repository|candidate SHA|check identity|fingerprint|new decision|chat"   docs/adr/0003-fingerprinted-human-decisions.md
git diff --check -- docs/adr/0003-fingerprinted-human-decisions.md
git add docs/adr/0003-fingerprinted-human-decisions.md
git commit -m "docs: record fingerprinted decisions ADR"
```

---

### Task 9: Text-Package Integration

**Files:**

- Modify: `README.md`

**Interfaces:**

- Consumes: Tasks 1–8
- Produces: complete text navigation from the repository entry point

- [ ] **Step 1: Update the Documentation section**

Add links in this order:

1. Product case
2. Architecture
3. Threat model
4. Operations runbook
5. Benchmark evidence
6. ADR 0001
7. ADR 0002
8. ADR 0003
9. Original approved design
10. Implementation plan

Do not duplicate the documents in README.

- [ ] **Step 2: Add a portfolio-evidence note**

Immediately before Documentation, add a short paragraph stating that:

- public demo evidence is synthetic;
- CI and demo topology are verified;
- human time savings and external adoption remain unvalidated.

- [ ] **Step 3: Verify relative links**

Run from repository root:

```bash
python - <<'PY'
from pathlib import Path
import re

files = [Path("README.md"), *Path("docs/portfolio").glob("*.md"), *Path("docs/adr").glob("*.md")]
pattern = re.compile(r"\[[^]]+\]\(([^)]+)\)")
missing = []
for source in files:
    text = source.read_text(encoding="utf-8")
    for target in pattern.findall(text):
        if "://" in target or target.startswith("#"):
            continue
        clean = target.split("#", 1)[0]
        resolved = (source.parent / clean).resolve()
        if clean and not resolved.exists():
            missing.append(f"{source}: {target}")
if missing:
    raise SystemExit("\n".join(missing))
print(f"relative links verified across {len(files)} Markdown files")
PY

git diff --check -- README.md docs/portfolio docs/adr
```

Expected: every relative link resolves and no whitespace errors exist.

- [ ] **Step 4: Scan for unfinished markers and placeholder secrets**

```bash
if rg -n -i '\b(todo|tbd)\b|replace-with-|example-secret|your-token'   README.md docs/portfolio docs/adr; then
  echo "unfinished or placeholder content found" >&2
  exit 1
fi
```

Expected: no matches.

- [ ] **Step 5: Commit**

```bash
git add README.md docs/portfolio docs/adr
git commit -m "docs: integrate portfolio documentation"
```

---

### Task 10: Text-Package Review and CI Gate

**Files:**

- Review: `README.md`
- Review: `docs/portfolio/*.md`
- Review: `docs/adr/*.md`

**Interfaces:**

- Consumes: complete text-only package
- Produces: reviewed, pushed text baseline before screenshots

- [ ] **Step 1: Review the branch scope**

```bash
git status --short
git diff --stat origin/main...HEAD
git diff --check origin/main...HEAD
```

Expected: working tree clean; no whitespace errors.

- [ ] **Step 2: Re-run seeder contract tests**

```bash
uv run --project apps/api pytest   demo/test_seed_manifest.py   demo/test_seed_state.py   -q -W error
```

Expected: `52 passed`.

- [ ] **Step 3: Push the branch**

```bash
git push origin feature/ai-release-intelligence-mvp
```

Expected: remote branch advances without force-push.

- [ ] **Step 4: Verify GitHub Actions**

Verify both workflow runs on the pushed HEAD:

- `CI` — completed/success;
- `Task 2 PostgreSQL persistence` — completed/success.

Do not proceed to screenshots if either workflow fails.

---

### Task 11: Capture Real Deterministic Screenshots

**Files:**

- Create: `docs/portfolio/assets/01-release-setup.png`
- Create: `docs/portfolio/assets/02-needs-decision.png`
- Create: `docs/portfolio/assets/03-ready-after-decision.png`

**Interfaces:**

- Consumes: credential-free E2E stack and verified UI flow
- Produces: three privacy-safe portfolio screenshots

- [ ] **Step 1: Start the deterministic stack**

```bash
docker compose -f compose.test.yaml config --quiet
docker compose -f compose.test.yaml up --build -d --wait
curl --fail --silent --show-error http://127.0.0.1:4173/healthz
```

Expected: health response contains `"status":"ok"`.

- [ ] **Step 2: Prepare a consistent browser viewport**

Open `http://127.0.0.1:4173` in a browser with:

- viewport: 1440 × 1024;
- zoom: 100%;
- no bookmarks bar, password prompts, extensions, unrelated tabs, or private
  browser/profile information visible.

- [ ] **Step 3: Capture setup state**

1. Click `Use demo repository`.
2. Select milestone `7`.
3. Select candidate `release/2026-08-10`.
4. Do not run analysis yet.
5. Save a full-page PNG as `01-release-setup.png`.

- [ ] **Step 4: Capture needs-decision state**

1. Click `Run analysis`.
2. Confirm the header shows `NEEDS DECISION`.
3. Confirm the advisory finding and `Open evidence` link are visible.
4. Save a full-page PNG as `02-needs-decision.png`.

- [ ] **Step 5: Capture ready state**

1. Click `Accept risk`.
2. Enter exactly: `Known flaky advisory test; blocking suite is green.`
3. Check the human-decision confirmation.
4. Click `Record decision`.
5. Confirm the header shows `READY`.
6. Confirm `No findings require attention for this snapshot.` is visible.
7. Save a full-page PNG as `03-ready-after-decision.png`.

- [ ] **Step 6: Copy screenshots into the repository**

If the browser saved files to the Windows Downloads folder:

```bash
mkdir -p docs/portfolio/assets
install -m 0644 /mnt/c/Users/Valera/Downloads/01-release-setup.png   docs/portfolio/assets/01-release-setup.png
install -m 0644 /mnt/c/Users/Valera/Downloads/02-needs-decision.png   docs/portfolio/assets/02-needs-decision.png
install -m 0644 /mnt/c/Users/Valera/Downloads/03-ready-after-decision.png   docs/portfolio/assets/03-ready-after-decision.png
```

- [ ] **Step 7: Validate PNG signatures and dimensions**

```bash
python - <<'PY'
from pathlib import Path
import struct

paths = [
    Path("docs/portfolio/assets/01-release-setup.png"),
    Path("docs/portfolio/assets/02-needs-decision.png"),
    Path("docs/portfolio/assets/03-ready-after-decision.png"),
]
dimensions = []
for path in paths:
    raw = path.read_bytes()
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise SystemExit(f"{path} is not a PNG")
    width, height = struct.unpack(">II", raw[16:24])
    if width < 1200 or height < 700:
        raise SystemExit(f"{path} is too small: {width}x{height}")
    dimensions.append((width, height))
if len(set(dimensions)) != 1:
    raise SystemExit(f"inconsistent dimensions: {dimensions}")
print(f"validated {len(paths)} screenshots at {dimensions[0][0]}x{dimensions[0][1]}")
PY
```

Expected: all three screenshots share one readable dimension.

- [ ] **Step 8: Stop the stack without deleting the volume**

```bash
docker compose -f compose.test.yaml down
```

- [ ] **Step 9: Commit**

```bash
git add docs/portfolio/assets
git commit -m "docs: add deterministic demo screenshots"
```

---

### Task 12: Embed Screenshots and Complete README

**Files:**

- Modify: `README.md`

**Interfaces:**

- Consumes: validated screenshots from Task 11
- Produces: visual portfolio entry point

- [ ] **Step 1: Add a Demo walkthrough gallery**

Under `Verified synthetic demo`, embed:

```markdown
### Demo walkthrough

#### 1. Configure the release
![Release setup with demo repository, milestone, and candidate branch](docs/portfolio/assets/01-release-setup.png)

#### 2. Review an advisory failure
![Needs-decision report with advisory CI evidence](docs/portfolio/assets/02-needs-decision.png)

#### 3. Record a human decision
![Ready report after a documented accepted-risk decision](docs/portfolio/assets/03-ready-after-decision.png)
```

- [ ] **Step 2: Verify image links**

```bash
for image in   docs/portfolio/assets/01-release-setup.png   docs/portfolio/assets/02-needs-decision.png   docs/portfolio/assets/03-ready-after-decision.png
do
  test -s "$image"
  rg -F "$image" README.md
done
git diff --check -- README.md
```

Expected: every non-empty image is referenced once and Markdown has no whitespace errors.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: embed portfolio demo walkthrough"
```

---

### Task 13: Final Verification and PR Handoff

**Files:**

- Review: all portfolio documents, ADRs, screenshots, README
- Update: PR #2 description and draft state only after explicit approval

**Interfaces:**

- Consumes: complete portfolio package
- Produces: final release-gate evidence and a Ready-for-review decision

- [ ] **Step 1: Run final repository checks**

```bash
git status --short
git diff --check origin/main...HEAD
uv run --project apps/api pytest   demo/test_seed_manifest.py   demo/test_seed_state.py   -q -W error
```

Expected: clean working tree, no diff errors, `52 passed`.

- [ ] **Step 2: Push without force**

```bash
git push origin feature/ai-release-intelligence-mvp
```

- [ ] **Step 3: Verify final remote evidence**

Verify:

- PR #2 head SHA equals local `git rev-parse HEAD`;
- PR remains mergeable and open;
- `CI` completed successfully on final HEAD;
- `Task 2 PostgreSQL persistence` completed successfully on final HEAD;
- demo repository still exposes six managed branches, four issues, three merged
  PRs, successful `blocking-suite`, and intentional advisory failure;
- every README public link opens.

- [ ] **Step 4: Update PR #2 description**

Replace verification evidence with the final HEAD and list:

- five portfolio documents;
- three ADRs;
- three real screenshots;
- `52 passed` seeder tests;
- successful full CI;
- verified live-demo topology and workflow evidence.

- [ ] **Step 5: Present the final release-gate decision**

Report:

- completed deliverables;
- all verification evidence;
- any residual limitation;
- whether the package meets every completion criterion in the specification.

Do not convert the PR from draft until the user explicitly approves
`Ready for review`.

- [ ] **Step 6: Mark ready only after explicit approval**

After the user approves, convert PR #2 to Ready for review and verify:

- `draft=false`;
- `state=open`;
- head and base remain
  `feature/ai-release-intelligence-mvp → main`;
- mergeability remains true.
