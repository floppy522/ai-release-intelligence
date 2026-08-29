# ADR 0001: Keep Release Readiness Deterministic

## Status

Accepted

## Context

Release readiness is a Go/No-Go decision that must be reproducible and
auditable from the release evidence. The application normalizes a bounded
GitHub evidence window into an immutable `ReleaseSnapshot`, then the domain
assesses that snapshot against a configured policy and valid human decisions.
The assessment also receives an evaluation time and fails closed when its
snapshot is stale or temporally invalid. It applies fixed precedence for
insufficient data, blocking findings, and decisions; it does not call external
providers.

An optional AI explanation is useful for helping a reviewer understand the
stored report, but a model response is probabilistic and can be unavailable,
malformed, or insufficiently grounded. The explanation service therefore
receives an allowlisted projection of deterministic findings after analysis
and persistence, and validates its references before returning it.

## Decision

For an identical normalized snapshot, policy, valid human decisions, and
evaluation time/freshness context, deterministic rules must produce identical
readiness. The evaluation time is an authoritative input because a snapshot
that is fresh for one evaluation can later become `INSUFFICIENT_DATA`.
Deterministic domain rules remain the sole authority for release status,
finding severity, evidence, and whether an advisory finding is accepted. AI
may explain the supplied deterministic report, but it cannot set status,
severity, evidence, or acceptance.

## Consequences

- Release outcomes can be reproduced from the persisted snapshot, policy
  version, findings, evidence, valid decision history, and the evaluation
  time/freshness context used for the result.
- A stored `READY` assessment is not permanently valid: a later evaluation
  correctly becomes `INSUFFICIENT_DATA` when the immutable source window is
  stale, requiring a fresh analysis rather than reusing old evidence.
- Rule behaviour and status precedence can be unit-tested without model calls,
  network availability, or prompt variance.
- AI output is constrained to a non-authoritative explanation; an unavailable
  or rejected explanation leaves the deterministic report intact.
- New readiness behaviour requires an explicit policy or deterministic rule
  change rather than a prompt or model change.
- The product does not use an LLM to resolve ambiguous evidence; eligible
  advisory failures continue to require a governed human decision.

## Rejected alternatives

### LLM-only decision

An LLM-only decision would make a release verdict depend on model behaviour,
provider availability, and prompt interpretation instead of the normalized
evidence and policy. It would not meet the reproducibility requirement.

### Hybrid LLM score

A hybrid score would make model output a hidden or variable input to the
authoritative result. A deterministic rule engine plus explicit human
decisions already exposes the relevant decision boundary without assigning
authority to the model.

### Human-only manual checklist

A human-only manual checklist would preserve reviewer judgement but would not
consistently collect, normalize, evaluate, and retain the evidence needed for
repeatable release readiness.

## Evidence

- [Deterministic assessment and fixed precedence](../../apps/api/src/release_intelligence/domain/assessment.py)
- [Deterministic rule families](../../apps/api/src/release_intelligence/domain/rules/)
- [Release analysis orchestration](../../apps/api/src/release_intelligence/application/analyze_release.py)
- [AI explanation allowlist and grounding validation](../../apps/api/src/release_intelligence/application/explanations.py)
- [Architecture: AI explanation boundary](../portfolio/architecture.md#ai-explanation-boundary)
