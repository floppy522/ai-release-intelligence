# ADR 0003: Bind Human Decisions to Evidence Fingerprints

## Status

Accepted

## Context

An accepted-risk or release-blocker decision is meaningful only for the
specific advisory failure a reviewer examined. A check with the same displayed
name can represent a different candidate commit, check run, or conclusion, so
a reusable waiver could silently authorize changed evidence.

The current domain creates a SHA-256 evidence fingerprint from the repository,
candidate SHA, check name, GitHub check-run identity, and conclusion. The
persistence boundary accepts a decision only for the current
`checks.advisory_requires_decision` finding in the authorized repository when
the submitted fingerprint matches that finding's stored evidence. It appends
the decision and its reassessment to the immutable analysis-run history.

## Decision

Bind every accepted-risk or release-blocker decision to its repository,
candidate SHA, check identity, run identity, conclusion, and rule/evidence
fingerprint. The decision is additionally anchored to the current advisory
finding and analysis run. A changed fingerprint requires a new decision.

## Consequences

- Waivers are auditable and cannot silently survive changed evidence: a fresh
  analysis with changed fingerprinted check evidence has no matching decision.
- Persisted actor, reason, timestamp, decision kind, finding, run, fingerprint,
  supersession lineage, and reassessed outcome let reviewers reconstruct the
  governing action.
- Repeated decisions may create user friction when a candidate SHA, check run,
  conclusion, or other fingerprint input changes, even if the check name is
  familiar.
- Identity construction becomes a security- and correctness-critical contract;
  canonicalization, validation, and fingerprint inputs must remain stable and
  complete.
- A decision applies only to an eligible advisory finding, rather than acting
  as a general override for unrelated blocking or insufficient evidence.

## Rejected alternatives

### Decision by check name only

Decision by check name only would conflate distinct executions of a check and
could retain a waiver after the repository state or conclusion changed.

### Permanent repository-level waiver

A permanent repository-level waiver would authorize future candidates without
showing that their specific evidence was reviewed. It would also bypass the
analysis-run audit trail.

### Unstructured chat approval

Unstructured chat approval does not provide a validated repository, finding,
candidate SHA, check identity, fingerprint, or immutable reassessment record.
It therefore cannot establish a durable decision boundary.

## Evidence

- [Check identity and fingerprint construction](../../apps/api/src/release_intelligence/domain/rules/checks.py)
- [Decision service validation](../../apps/api/src/release_intelligence/application/decisions.py)
- [Decision eligibility, locking, and persistence](../../apps/api/src/release_intelligence/adapters/persistence/repositories.py)
- [Immutable decision reassessment migration](../../apps/api/alembic/versions/0003_decision_assessments.py)
- [Architecture: human-decision lifecycle](../portfolio/architecture.md#human-decision-lifecycle)
