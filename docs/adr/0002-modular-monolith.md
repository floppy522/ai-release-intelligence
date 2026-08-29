# ADR 0002: Keep the MVP a Modular Monolith

## Status

Accepted

## Context

The release-readiness workflow is one bounded application flow: authorize a
repository, load and normalize release evidence, assess deterministic rules,
persist an analysis run, optionally record a decision, and optionally request
an explanation. The current implementation already separates that work into
domain rules and models, application services, ports, concrete adapters, and
FastAPI API modules.

Production Compose runs one FastAPI API service, a one-shot migration service,
PostgreSQL, and the React web application. Backend module calls are typed
in-process calls, while React and PostgreSQL are external runtime components.
The API persists an analysis snapshot, findings, and evidence together, then
appends governed decisions against that stored run.

## Decision

Keep one FastAPI deployable with isolated domain, application, port, adapter,
and API modules. Keep React and PostgreSQL as external runtime components.
Maintain the internal boundaries so that a provider or use case can be
extracted later through a port only when an operational need justifies it.

## Consequences

- Deployment and operational complexity are lower because the backend has one
  deployable, one API runtime, and no inter-service network contract for its
  internal workflow.
- Persistence can remain transactionally consistent for each analysis-run
  creation and governed decision reassessment.
- Domain and application boundaries remain testable without network hops or
  separately deployed backend services.
- Future extraction remains possible through ports that separate GitHub,
  persistence, authentication, policy, and AI integrations from the core.
- Independent scaling and failure isolation are limited because backend modules
  share one deployable and runtime process.

## Rejected alternatives

### Microservices

Microservices would add service discovery, deployment coordination,
cross-service observability, and distributed consistency concerns before this
MVP has a demonstrated need for independently deployed backend capabilities.

### Serverless functions per rule

Serverless functions per rule would turn a single ordered assessment over one
normalized snapshot into distributed invocation and coordination work. That
would complicate shared policy, evidence lineage, and final status precedence.

### Single unstructured application module

A single unstructured application module would avoid deployment overhead but
would mix domain policy, transport handling, persistence, and provider code.
It would make the deterministic core harder to test and later extract.

## Evidence

- [FastAPI dependency wiring and route registration](../../apps/api/src/release_intelligence/main.py)
- [Domain models and assessment](../../apps/api/src/release_intelligence/domain/)
- [Application services](../../apps/api/src/release_intelligence/application/)
- [Port contracts](../../apps/api/src/release_intelligence/ports/)
- [Concrete adapters](../../apps/api/src/release_intelligence/adapters/)
- [Production Compose topology](../../compose.yaml)
- [Architecture: component boundaries](../portfolio/architecture.md#component-boundaries)
