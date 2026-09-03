# Research-Informed Product Decisions

## Status and threshold

**No external-evidence-driven product decision has been recorded.**

Do not implement every request. A meaningful change requires either repeated
evidence from multiple eligible participants or one high-severity flaw that
invalidates a core workflow assumption. Integration mentions and preferences
do not cross this threshold by themselves.

## Traceability register

| Decision ID | Research evidence | Evidence class and sample | Contradictions | Implication | Product decision | GitHub issue | Implementation PR | Verification | Release |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| - | None recorded. | Unvalidated; n=0 | - | - | - | - | - | - | - |

Public evidence should link to an aggregate section or pattern ID in
[discovery-findings.md](discovery-findings.md) or
[pilot-results.md](pilot-results.md), never to raw participant notes.

## Decision record template

### DNN - Decision title

**Status:** Proposed | Accepted | Rejected | Deferred | Superseded

**Date:** YYYY-MM-DD

**Decision owner:** Product owner

#### Research evidence

- Public aggregate or pattern link:
- Evidence class:
- Supporting sample and roles:
- Repeated across:
- Contradicting or negative evidence:
- Why the evidence threshold is or is not met:

#### Implication

State which user, workflow step, domain assumption, trust boundary, or outcome
is affected. Keep the implication separate from the proposed solution.

#### Options considered

| Option | Expected value | Cost or risk | Evidence fit |
| --- | --- | --- | --- |
| Keep current behavior | - | - | - |
| Minimal change | - | - | - |
| Broader change | - | - | - |

#### Decision and trade-off

State what changes, what deliberately does not change, and why. Preserve
uncertainty and define a reversal trigger.

#### Implementation trace

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

Completing research does not justify `v0.2.0`. Create a new product release only
when external evidence drives a meaningful implemented and verified iteration.
Release notes must state the aggregate sample, what changed because of evidence,
what remains unvalidated, and any measured pilot result with its method and
limitations.
