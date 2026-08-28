# AI Release Intelligence Portfolio Package — Design Specification

**Date:** 2026-08-28

**Status:** Approved in chat; awaiting review of this written specification

**Owner:** Valeriy Malov

## 1. Purpose

The portfolio package will turn the existing AI Release Intelligence MVP into a
reviewable product case for recruiters, engineering managers, and technical
interviewers. It will explain the product problem, architecture, security model,
operational behaviour, evaluation boundary, and key technical decisions without
requiring the reader to reverse-engineer the implementation.

The package must distinguish three classes of evidence:

1. **Verified implementation evidence** — source code, CI jobs, the synthetic
   demo topology, and GitHub Actions results.
2. **Design targets** — acceptance thresholds and the five-minute decision
   target.
3. **Unvalidated hypotheses** — adoption, external demand, willingness to pay,
   and human time savings.

No design target or experience-based baseline may be presented as a measured
product result.

## 2. Audience and reading paths

### Recruiter or hiring manager

The reader should understand the problem, user, differentiating product
decision, delivered scope, and project limitations in five minutes.

Primary path:

`README.md → product-case.md → screenshots`

### Engineering manager or technical interviewer

The reader should be able to inspect architecture, trust boundaries, security
controls, operational procedures, evaluation methodology, and trade-offs.

Primary path:

`README.md → architecture.md → threat-model.md → ADRs → benchmark-results.md`

### Developer or evaluator

The reader should be able to start the system, run deterministic checks, inspect
the synthetic GitHub evidence, and diagnose common failures.

Primary path:

`README.md → operations-runbook.md → benchmark-results.md`

## 3. Deliverables

### 3.1 Product case

Create `docs/portfolio/product-case.md`.

It will contain:

- executive summary;
- primary persona and JTBD;
- current manual release-readiness workflow;
- experience-based 30–60 minute baseline, explicitly labelled as unmeasured;
- product hypothesis and decision principles;
- MVP scope and non-goals;
- verified capabilities;
- synthetic demo walkthrough;
- validation status and limitations;
- next validation steps.

The product case must not introduce market-size, revenue, adoption, or
time-saving claims that are absent from verified evidence.

### 3.2 Architecture

Create `docs/portfolio/architecture.md`.

It will contain:

- system context;
- modular-monolith component diagram;
- deterministic analysis sequence;
- normalized snapshot and evidence model;
- human-decision fingerprint lifecycle;
- AI explanation boundary and fallback;
- persistence and deployment topology;
- failure behaviour;
- links to relevant source directories and workflows.

All diagrams will use GitHub-rendered Mermaid. Architecture statements must be
traceable to the approved design specification or current implementation.

### 3.3 Threat model

Create `docs/portfolio/threat-model.md`.

It will define:

- protected assets;
- actors and trust boundaries;
- entry points;
- threat table covering authentication, authorization, token exposure, CSRF,
  SSRF/evidence URLs, prompt injection, stored untrusted content, data leakage,
  stale human decisions, dependency compromise, and partial GitHub responses;
- implemented controls;
- residual risks;
- security verification links.

The document will state explicitly that it is a project threat model, not an
external penetration-test report.

### 3.4 Operations runbook

Create `docs/portfolio/operations-runbook.md`.

It will cover:

- prerequisites;
- GitHub App permissions and OAuth callback;
- environment configuration;
- Docker Compose startup and shutdown;
- health verification;
- database migrations;
- deterministic demo use;
- log inspection;
- common GitHub, database, OAuth, migration, and AI-provider failures;
- safe recovery;
- backup and restore boundaries;
- credential rotation;
- data cleanup;
- escalation conditions.

Commands must be copyable, non-destructive by default, and distinguish normal
shutdown from volume deletion.

### 3.5 Benchmark results

Create `docs/portfolio/benchmark-results.md`.

It will document:

- the versioned 44-scenario catalog;
- evaluated categories;
- acceptance thresholds;
- the CI command and workflow;
- the verified fact that the security-benchmark job completed successfully for
  commit `5f5837e483f855394a0dddf07e81a2643be8f787`;
- the difference between deterministic benchmark evidence, live-provider
  evaluation, and the future human-vs-tool experiment;
- limitations of synthetic evidence.

The document must not invent per-metric values when the current retained
evidence proves only that the configured gate passed. Exact numeric results may
be added later only from a retained benchmark artifact.

### 3.6 Architecture Decision Records

Create:

- `docs/adr/0001-deterministic-readiness-core.md`;
- `docs/adr/0002-modular-monolith.md`;
- `docs/adr/0003-fingerprinted-human-decisions.md`.

Each ADR will use the same structure:

- Status;
- Context;
- Decision;
- Consequences;
- Rejected alternatives;
- Evidence.

ADR decisions:

1. The LLM cannot determine release readiness.
2. The MVP remains a modular monolith rather than microservices.
3. Advisory decisions bind to an exact evidence fingerprint and become invalid
   when the underlying state changes.

### 3.7 Screenshots

Create `docs/portfolio/assets/` and add three real PNG screenshots captured
from the deterministic test stack:

1. `01-release-setup.png` — demo repository, milestone, and candidate selection;
2. `02-needs-decision.png` — advisory failure with `NEEDS_DECISION`;
3. `03-ready-after-decision.png` — `READY` after a documented accepted-risk
   decision.

Screenshots must:

- come from the implemented UI, not a generated mock-up;
- contain no credentials, browser profile data, unrelated tabs, or private
  repository names;
- use a consistent viewport;
- show only fictional demo data;
- remain readable on a GitHub README page.

The screenshots will be captured after the text deliverables are committed. The
deterministic Compose stack will be used so no production credentials are
required.

### 3.8 README and PR integration

Update `README.md` to:

- link every portfolio document;
- embed the three screenshots after they exist;
- keep the verified demo links;
- preserve explicit limitations;
- avoid duplicating full documents.

Update PR #2 to:

- list the completed portfolio deliverables;
- reference the final HEAD;
- retain verified live-demo evidence;
- replace the draft merge gate only after final verification.

## 4. Information architecture

```text
README.md
docs/
├── adr/
│   ├── 0001-deterministic-readiness-core.md
│   ├── 0002-modular-monolith.md
│   └── 0003-fingerprinted-human-decisions.md
└── portfolio/
    ├── product-case.md
    ├── architecture.md
    ├── threat-model.md
    ├── operations-runbook.md
    ├── benchmark-results.md
    └── assets/
        ├── 01-release-setup.png
        ├── 02-needs-decision.png
        └── 03-ready-after-decision.png
```

Each file has one responsibility. The root README is navigation and product
summary, not a duplicate of the complete package.

## 5. Source hierarchy

When sources conflict, use this order:

1. current implementation on `feature/ai-release-intelligence-mvp`;
2. successful GitHub Actions evidence for the current HEAD;
3. verified synthetic demo repository state;
4. approved design specification;
5. implementation plan.

Future-state items from the original design must not be described as delivered
unless they exist in the current branch.

## 6. Screenshot capture approach

The capture workflow will use `compose.test.yaml`, the persisted Playwright
scenario, and the deterministic fixture adapter. This avoids real GitHub App and
OpenAI credentials.

The implementation plan must define exact startup, capture, sanitization,
validation, and shutdown commands after verifying the current test-stack port
and fixture behaviour. If automated capture would modify application behaviour,
the plan must instead use a manual browser capture with an exact checklist.

## 7. Verification strategy

### Text verification

- every Markdown link resolves within the branch or to verified public GitHub
  evidence;
- no `TODO`, `TBD`, placeholder secret, or unsupported measured claim remains;
- Mermaid blocks use GitHub-compatible syntax;
- shell commands match repository files and workflows;
- repository terminology is consistent.

### Screenshot verification

- exact filenames exist;
- PNG files open successfully;
- dimensions and viewport are consistent;
- no sensitive or unrelated information is visible;
- README image links render.

### Repository verification

- source branch contains only the intended documentation and asset changes;
- full pull-request CI passes on the final commit;
- synthetic demo topology remains available;
- PR body references the final verified commit;
- PR remains draft until the final release-gate review is explicitly approved.

## 8. Sequencing

1. Create the five text documents and three ADRs.
2. Review the text package for factual consistency and unsupported claims.
3. Update README navigation.
4. Run CI on the text-only commit.
5. Start the deterministic test stack and capture three real screenshots.
6. Add screenshots and embed them in README.
7. Run final CI and validate public links.
8. Update PR #2 and perform the final Ready-for-review decision.

## 9. Non-goals

This package will not:

- deploy the application publicly;
- create generated UI mock-ups;
- claim external customer validation;
- claim measured time savings;
- add product features;
- change the deterministic assessment algorithm;
- add new integrations;
- publish secrets or private operational data;
- merge PR #2 automatically.

## 10. Completion criteria

The package is complete when:

- all five portfolio documents and three ADRs exist and are internally
  consistent;
- three real synthetic-demo screenshots are embedded in README;
- verified claims are linked to public evidence;
- unvalidated hypotheses are labelled;
- the final branch CI is green;
- PR #2 accurately describes the final HEAD;
- a final review finds no unresolved portfolio or release-gate blocker.
