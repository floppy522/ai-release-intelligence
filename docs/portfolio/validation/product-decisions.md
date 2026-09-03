# Research-Informed Product Decisions

## Status and threshold

One scope-preservation decision has been recorded from the initial exploratory
evidence. No product feature has been approved or implemented.

A meaningful change requires either repeated evidence from multiple eligible
participants or one high-severity flaw that invalidates a core workflow
assumption. Integration mentions and preferences do not cross this threshold by
themselves.

## Traceability register

| Decision ID | Research evidence | Evidence class and sample | Contradictions | Implication | Product decision | GitHub issue | Implementation PR | Verification | Release |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| D001 | [Exploratory signals](discovery-findings.md#exploratory-signals---not-gated-patterns) and [negative evidence](discovery-findings.md#contradictions-and-negative-evidence) | Self-reported; 4 exploratory interviews in one organizational context; 0 fully protocol-eligible | Most evidence concerns broader delivery work; no ARI concept test | Status reporting, estimation, and task capture are adjacent needs, not evidence for expanding ARI | Keep `v0.1.0` scope unchanged; prioritize release-specific external discovery | Not applicable; no feature change | This documentation update only | Confirm no feature or recruiter-facing claim changed | `v0.1.0` remains current |

Public evidence links to anonymized aggregate findings, never to raw participant
notes.

## D001 - Keep product scope unchanged after exploratory interviews

**Status:** Accepted

**Date:** 2026-09-03

**Decision owner:** Product owner

### Research evidence

- Public aggregate: [Discovery findings](discovery-findings.md)
- Evidence class: **Self-reported**
- Sample: four exploratory colleagues from one organizational context
- Repeated evidence: recurring manual status/reporting work and fragmented
  delivery information
- Contradicting or limiting evidence: only one clear release-management role;
  no interview fully meets the current last-release protocol; no ARI concept
  test; repository/CI monitoring was described as low-effort by P01
- Threshold assessment: insufficient for a feature change or outcome claim

### Implication

The interviews identify real adjacent operational burdens, but they do not show
that delivery-status generation, task estimation, voice-to-Jira capture, or a
new integration belongs inside ARI. Treating these ideas as validated product
requirements would mix different jobs to be done and weaken the current
release-readiness hypothesis.

### Options considered

| Option | Expected value | Cost or risk | Evidence fit |
| --- | --- | --- | --- |
| Keep scope and continue release-specific discovery | Protects hypothesis clarity and tests the current MVP | Delays exploration of adjacent ideas | Best fit to current evidence quality |
| Add status-report generation | May address recurring PM workload | Expands product and requires new data/workflow boundaries | Repeated adjacent need, but not release-specific validation |
| Add estimation or task-capture features | Could help R&D leads | Creates a different product and outcome model | Two participant-generated ideas; no ARI fit evidence |

### Decision and trade-off

Keep the current one-repository release-readiness scope and non-goals unchanged.
Use follow-up interviews to isolate actual Go/No-Go work. Preserve the adjacent
ideas as research observations, not roadmap commitments.

The trade-off is deferring potentially valuable PM automation while protecting
the ability to learn whether the current ARI problem and domain model are valid.
Revisit this decision only if protocol-eligible participants repeatedly show
that one adjacent capability is necessary for a release-readiness decision.

### Implementation trace

- GitHub issue: not created; no product change approved
- Acceptance criteria: no feature, integration, README outcome, resume claim, or
  release version changes from these interviews
- Pull request: documentation evidence update only
- Verification: repository diff limited to discovery evidence and this decision
- Release note: not applicable
- Released version: `v0.1.0` remains current

### Post-decision check

- Collect release-specific external interviews and at least one neutral ARI
  concept test.
- Reverse or revise only when repeated eligible evidence or one core-invalidating
  flaw crosses the product-change threshold.

## Decision record template

### DNN - Decision title

**Status:** Proposed | Accepted | Rejected | Deferred | Superseded

**Date:** YYYY-MM-DD

**Decision owner:** Product owner

#### Template - Research evidence

- Public aggregate or pattern link:
- Evidence class:
- Supporting sample and roles:
- Repeated across:
- Contradicting or negative evidence:
- Why the evidence threshold is or is not met:

#### Template - Implication

State which user, workflow step, domain assumption, trust boundary, or outcome
is affected. Keep the implication separate from the proposed solution.

#### Template - Options considered

| Option | Expected value | Cost or risk | Evidence fit |
| --- | --- | --- | --- |
| Keep current behavior | - | - | - |
| Minimal change | - | - | - |
| Broader change | - | - | - |

#### Template - Decision and trade-off

State what changes, what deliberately does not change, and why. Preserve
uncertainty and define a reversal trigger.

#### Template - Implementation trace

- GitHub issue:
- Acceptance criteria:
- Pull request:
- Verification evidence:
- Release note:
- Released version:

#### Post-change check

- What evidence will show that the change addressed the finding?
- What would cause the decision to be reversed or revised?

## Release rule

These interviews do not justify `v0.2.0`. Create a new product release only when
external evidence drives a meaningful implemented and verified iteration.
Release notes must state the aggregate sample, what changed because of evidence,
what remains unvalidated, and any measured pilot result with its method and
limitations.
