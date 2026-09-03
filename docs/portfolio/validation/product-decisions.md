# Research-Informed Product Decisions

## Status

External validation is paused. One scope-preservation decision is retained; no
feature, integration, recruiter-facing claim, or release was created from the
exploratory interviews.

## Traceability register

| Decision | Evidence | Implication | Product decision | Implementation |
| --- | --- | --- | --- | --- |
| D001 - Preserve `v0.1.0` scope | [Four internal exploratory summaries](discovery-findings.md); Self-reported; 0 fully protocol-eligible; no ARI concept test | Status reporting, estimation, and task capture are adjacent needs, not validated ARI requirements | Keep the one-repository release-readiness scope and current non-goals | No issue, feature PR, README claim, resume claim, or release |

## D001 - Preserve scope after exploratory interviews

**Status:** Accepted

**Date:** 2026-09-03

### Evidence and contradictions

- Four summaries described manual delivery-status work and fragmented
  information.
- Most reported burden concerned broader reporting, meetings, planning, and
  coordination rather than Go/No-Go review.
- Only one participant had a clear release-management role in the supplied
  summary.
- No interview met the current protocol, no workflow was observed, and no ARI
  concept test was documented.
- One participant described repository and CI monitoring as low-effort.

### Decision

Keep `v0.1.0` scope unchanged. Do not add status-report generation, estimation,
voice-to-Jira capture, or new integrations based on this evidence. Preserve
these ideas only as research observations.

The trade-off is deferring potentially useful adjacent automation while keeping
ARI's release-readiness hypothesis testable and recruiter-facing claims
defensible.

### Verification and reversal trigger

- Product implementation: unchanged.
- GitHub issue: not created because no product change was approved.
- Release: `v0.1.0` remains current.
- Resume and profile claims: unchanged.
- Reconsider only if multiple eligible practitioners identify the same
  release-critical gap or one high-severity finding invalidates a core workflow
  assumption.

## Change threshold

Future product changes require repeated evidence from multiple eligible
participants or one high-severity flaw that invalidates a core assumption. Each
accepted change should trace:

`evidence -> implication -> decision -> issue -> PR -> verification -> release`

Completing research alone does not justify `v0.2.0`.
