# Controlled Pilot Methodology

## Status and objective

**Pilot status:** Paused; no runs are scheduled

**Outcome hypothesis:** Unvalidated

The pilot compares manual release-readiness review with ARI-assisted review on
small, comparable scenarios. Its purpose is to estimate active review effort,
critical-blocker recall, evidence coverage, and decision trust in this sample;
it is not a production-adoption or market-demand test.

## Participants and unit of analysis

Target 2-3 eligible release practitioners. The primary unit is one participant
completing one scenario under one condition. Report participants, scenarios,
and completed runs separately so repeated runs are not presented as independent
users.

## Scenario design

Create at least one matched pair of scenarios with equivalent complexity. Each
scenario must specify before the session:

- repository and milestone scope;
- candidate branch and immutable commit SHA;
- required policy checks;
- known critical blockers and non-blocking findings;
- expected operations, migration, and back-merge evidence where applicable;
- expected final readiness status and required actions;
- direct evidence links or fixture identifiers;
- scenario version and reviewer of the ground truth.

Prefer synthetic or public evidence. Do not import private production data into
the repository. Do not design every scenario around the current product's happy
path; include ambiguity, irrelevant failed checks, and incomplete data where
these reflect the research question.

## Ground truth

Two reviewers should independently inspect the scenario manifest and evidence,
then reconcile disagreements before the pilot. Freeze the agreed ground truth
by scenario version and commit SHA. The participant and facilitator must not
change it during a run.

Ground truth contains:

```text
expected decision
critical blocker IDs
non-blocking finding IDs
required evidence-check IDs
required actions
policy assumptions
```

If ground truth is discovered to be wrong or ambiguous, mark the run invalid,
record the reason, correct and version the scenario, and rerun only if feasible.
Do not score the participant against revised truth retroactively.

## Conditions

### Manual condition

The participant uses the GitHub surfaces and scenario materials normally
available to a reviewer. They may take notes. ARI and its derived report are not
available.

### ARI-assisted condition

The participant starts from the ARI report and may open any linked or manual
evidence. Manual verification is allowed and must be observed; the test is
decision support, not blind reliance.

## Assignment and order

Use a counterbalanced crossover where sample size allows it:

- Participant A: manual scenario 1, ARI-assisted scenario 2.
- Participant B: ARI-assisted scenario 1, manual scenario 2.
- Participant C: use the order that best balances completed runs.

A participant must not review the same scenario in both conditions because
memory would contaminate time and recall. Record prior familiarity with ARI and
the scenario. Give only the minimum mechanics training needed to operate the
demo; do not teach the expected decision.

## Timing rules

Use a monotonic timer or timestamped recording when consent permits.

- **Start:** the participant receives the scenario goal and can begin inspecting
  evidence.
- **Stop:** the participant states a final Go/No-Go decision and required actions.
- **Active review:** time spent inspecting, navigating, reasoning, recording, or
  asking scenario-clarification questions.
- **Waiting:** system loading, facilitator interruption, or other delay that does
  not require participant review activity. Log separately.
- **Pause:** pause for technical failure unrelated to the condition; record the
  duration and cause.

Report active review time as the primary measure. Preserve total elapsed and
waiting time so exclusions are auditable. Do not stop the clock when the
participant manually verifies an ARI finding; that is part of assisted review.

## Measures

### Critical-blocker recall

```text
correctly identified ground-truth critical blockers
---------------------------------------------------
total ground-truth critical blockers
```

Report `not applicable` for a scenario with no critical blockers; do not treat a
zero denominator as 100%.

### False blockers

Count ground-truth non-blocking items that the participant incorrectly treats
as release blockers. Report counts, not only a rate.

### Evidence coverage

```text
required checks supported by evidence identified by the participant
--------------------------------------------------------------------
total required ground-truth checks
```

### Decision correctness

Exact match to the ground-truth final status plus identification of every
required critical action. Report status correctness and action completeness
separately when they differ.

### Decision confidence and trust

After each run collect:

- confidence from 1 (not confident) to 5 (fully confident);
- decision-support acceptance: `Yes`, `Yes, with manual verification`, or `No`;
- evidence opened first and evidence still verified manually;
- what created or reduced trust;
- any finding or omission that would invalidate use in a real decision.

Confidence and acceptance are **Self-reported**. Observed navigation and timing
are **Verified**.

## Comparison method

- Publish every valid run in a per-scenario table with participant IDs removed
  or coarsened when re-identification risk exists.
- Compare condition medians for active review time, blocker recall, evidence
  coverage, and confidence.
- For matched scenario pairs, also report the scenario-level and participant-
  level deltas; do not combine unmatched scenarios without explanation.
- Calculate time reduction as `(manual median - assisted median) / manual median`.
- Report false blocker counts and every missed critical blocker explicitly.
- With 2-3 participants, use descriptive statistics only. Do not report
  statistical significance or extrapolate to a population.

## Decision rule and safety stop

The pre-declared positive heuristic is a 30% or greater reduction in median
active review time, no reduction in critical-blocker recall, no silently missed
critical blocker, and participant willingness to use the report as decision
support. The 30% value is a **Target**, not an expected or current result.

Any silently missed critical blocker is a safety-significant result. Stop using
the affected scenario for positive outcome claims, investigate whether ground
truth, product behavior, or facilitation caused it, and preserve the result.

## Limitations to report

- sample size and recruitment source;
- synthetic, public, or reconstructed scenario mix;
- scenario comparability and ground-truth review;
- participant familiarity with GitHub, ARI, and release policy;
- order, learning, and facilitator effects;
- technical failures and excluded time;
- difference between controlled decision support and production adoption.
