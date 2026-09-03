# External Validation Research Plan

## Status and scope

**Evidence status:** Unvalidated

**Product version at research start:** `v0.1.0`

**Research start:** 2026-09-03

This study tests whether the release-readiness problem and the current AI
Release Intelligence (ARI) workflow generalize beyond the author's own
release-management experience. It is designed to find disconfirming evidence
as well as supporting evidence.

The repository's current public claims remain unchanged. The
[README](../../../README.md) and [product case](../product-case.md) already
state that the public demo and automated checks are synthetic or technical
evidence, while external adoption and measured human time savings remain
unvalidated.

## Evidence classes

Every note, synthesis, metric, and public claim must use one of these classes:

| Class | Meaning |
| --- | --- |
| **Verified** | Directly observed, recorded with consent, or reproducibly measured. |
| **Self-reported** | Stated by a participant but not independently observed. |
| **Target** | A desired future outcome or decision threshold. |
| **Unvalidated** | Not yet supported by external evidence. |

An inference may be used in analysis only when it is explicitly labelled as an
inference and linked to the underlying verified or self-reported evidence. An
estimate must never be presented as a measurement.

## Research questions and hypotheses

### H1 - Problem

Release Managers and Technical Project Managers regularly spend meaningful
active time manually reconciling release scope, pull requests, CI checks,
blockers, and operational evidence.

- **Supporting evidence:** repeated last-release accounts or observations of
  manual reconciliation, with material active effort and operational frequency.
- **Weakening or falsifying evidence:** most eligible teams have reliable
  automated readiness checks; manual work is rare, immaterial, or limited to an
  unusual process; participants do not recognize the proposed workflow.

### H2 - Workflow fragmentation

Relevant release-readiness evidence is distributed across multiple GitHub
surfaces or related engineering systems and requires manual reconciliation.

- **Supporting evidence:** multiple sources are opened and cross-checked during
  the last actual review; evidence is carried mentally or copied into a separate
  record.
- **Weakening or falsifying evidence:** a single trusted surface already provides
  sufficient evidence; navigation across surfaces does not require meaningful
  interpretation or reconciliation.

### H3 - Solution usefulness

A deterministic, evidence-linked readiness report with explicit statuses
`READY`, `NOT_READY`, `NEEDS_DECISION`, and `INSUFFICIENT_DATA` can support real
Go/No-Go decision workflows.

- **Supporting evidence:** multiple eligible practitioners can map the report to
  their last-release workflow, identify evidence they would inspect, and regard
  the report as usable decision support.
- **Weakening or falsifying evidence:** the domain model omits a common critical
  decision input; statuses conflict with real authority or risk handling; users
  must reconstruct the review manually despite the report.

### H4 - Outcome

ARI reduces active human review effort without reducing critical-blocker recall
or decision trust.

- **Supporting evidence:** controlled comparisons show a material reduction in
  median active review time with no loss of critical-blocker recall, no silently
  missed critical blocker, and acceptable decision-support trust.
- **Weakening or falsifying evidence:** review time does not improve materially;
  recall or evidence coverage falls; a critical blocker is missed; participants
  reject the report as decision support.
- **Claim rule:** H4 remains **Unvalidated** until measured in the pilot. Discovery
  opinions cannot establish this outcome.

## Sample and eligibility

### Discovery target

- 6-10 practitioners, with an initial scheduling target of 8.
- At least 3 participants outside the author's current employer where feasible.
- Priority roles: Release Manager; Technical Project Manager; Engineering
  Manager or Team Lead with release ownership; Platform or DevOps lead who
  participates in Go/No-Go.

### Inclusion criteria

A participant must have participated in at least one planned production release
decision during the previous six months and be able to reconstruct a recent
real review without disclosing protected information.

### Exclusion criteria

Exclude people whose knowledge is limited to general preferences, deployment
execution without release-decision participation, or releases too distant to
reconstruct reliably. Record exclusions privately; do not replace an ineligible
participant merely to protect a target ratio.

## Four-phase method

### Phase A - Problem discovery

Run a 30-40 minute interview anchored in the participant's last actual release.
Do not show ARI until the problem-discovery and baseline questions are complete.
Capture the decision owner, sequence, evidence surfaces, manual checks,
self-reported active review time, uncertainty, failed-CI handling, blockers,
operations, migrations, accepted risks, and back-merges where applicable.

### Phase B - Concept test

Show the current public synthetic demo only after discovery. Probe distrust,
missing evidence, first evidence links opened, policy configurability, fallback
to manual review, and trust-destroying false positives or false negatives. Do
not treat liking, feature requests, or hypothetical willingness as validation.

### Phase C - Workflow observation

Observe 2-3 release-review workflows where feasible. Measure active review time,
surfaces visited, repeated navigation, checks performed, information held
mentally versus recorded, uncertainty, missed checks, and reconciliation versus
waiting time. Use screen sharing only with consent. If NDA constraints prevent
direct observation, use a synthetic or public reconstruction and label it.

### Phase D - Controlled pilot

Run 2-3 comparable manual-versus-ARI-assisted scenarios according to
[pilot-methodology.md](pilot-methodology.md). Do not optimize for the current
five-minute target before measuring actual behavior.

## Measures and operational definitions

| Measure | Definition | Evidence class |
| --- | --- | --- |
| Self-reported active review time | Participant's estimate for the last actual review; record range if uncertain. | Self-reported |
| Observed active review time | Clocked participant activity from review start to stated decision and required actions, excluding separately logged waiting. | Verified |
| Evidence surfaces | Distinct pages, views, tools, or systems used to reach the decision. | Verified when observed; otherwise Self-reported |
| Manual reconciliation | A human comparison or interpretation of two or more evidence items not already resolved by a trusted decision surface. | Verified when observed; otherwise Self-reported |
| Critical-blocker recall | Correctly identified ground-truth critical blockers divided by all ground-truth critical blockers. | Verified |
| False blockers | Non-blocking ground-truth items incorrectly classified as release blockers. | Verified |
| Evidence coverage | Required ground-truth checks for which the participant identifies sufficient supporting evidence, divided by all required checks. | Verified |
| Decision confidence | Participant rating from 1 (not confident) to 5 (fully confident), collected after each scenario. | Self-reported |
| Decision-support acceptance | `Yes`, `Yes, with manual verification`, or `No` after using the report. | Self-reported |

Waiting time, total elapsed release lead time, and active review time must be
reported separately.

## Decision gates

These are pre-declared decision heuristics, not facts or statistical proof.

### Problem-validation gate

A useful positive signal requires all of the following:

- at least 5 of the first 8 eligible practitioners perform meaningful manual
  reconciliation;
- at least 4 of 8 describe it as recurring material friction;
- the workflow occurs frequently enough to have operational consequence;
- no dominant contradicting pattern makes the target user definition invalid.

### Solution-validation gate

A useful positive signal requires multiple practitioners to see the
evidence-linked report as usable in their workflow and no fundamental mismatch
between ARI's domain model and common release-review practice. Counts, roles,
contradictions, and missing critical inputs must be reported.

### Pilot gate

A useful positive signal requires:

- a material reduction in median active review time; 30% or more is the current
  **Target**, not a promised result;
- no reduction in critical-blocker recall;
- no critical blocker silently missed;
- willingness to use the report as decision support, even if manual verification
  remains.

Failure to cross a gate is a research result. It must not be hidden by changing
the threshold after data collection.

## Analysis and synthesis rules

- Preserve one row per eligible participant in the private tracker.
- Assign pseudonymous IDs before notes are taken.
- Separate direct observation, participant statement, and researcher inference.
- Add a recurring theme to public findings only when supported by at least two
  eligible participants; show the exact supporting count and contradictions.
- Do not infer prevalence from this purposive qualitative sample.
- Do not average away a missed critical blocker.
- Keep discovery, observation, and pilot evidence separate.
- Freeze a dated public synthesis at each meaningful evidence milestone.

## Product-change rule

Recommend a meaningful product change only when it is supported by repeated
evidence from multiple participants or by one high-severity flaw that invalidates
a core workflow assumption. Each accepted change must trace:

`research evidence -> implication -> product decision -> issue -> PR -> verification -> release`

Mentions of integrations, notifications, or broader scope are not sufficient by
themselves. Jira, GitLab, Bitbucket, multi-repository releases, notifications,
and deployment orchestration remain non-goals unless the evidence changes the
product boundary.

## Privacy, consent, and NDA handling

- Keep names, contact data, employer names, raw notes, recordings, internal
  issue names, production incident details, private-system screenshots,
  customer data, and credentials outside the public repository.
- Ask permission before recording or observing a screen. Record the consent
  scope privately.
- Stop or redirect discussion when protected details appear. Replace them with
  the minimum safe abstraction in notes.
- Publish only anonymized aggregates. Do not use a quotation publicly without
  explicit quotation approval.
- Keep personal-project evidence separate from employer experience and never
  imply employer endorsement or product usage.

## Publication policy

Do not update recruiter-facing claims after individual interviews. The README,
product case, release notes, profile, and resume may change only at a meaningful
evidence milestone and must include sample, method, observed values, and
limitations appropriate to the claim. Research completion alone does not justify
`v0.2.0`; only a meaningful evidence-driven product iteration does.
