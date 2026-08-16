# Release-readiness benchmark

`scenarios/catalog.yaml` is the versioned, reviewable ground truth for the
deterministic release assessor. Version `1.0.0` contains 44 named scenarios in
the required ten categories. Each scenario registers every valid evidence ID
and specifies the expected release status, risk identity (`rule_id`,
`source_id`), severity, and evidence IDs.

Run from `apps/api`:

```bash
uv run python -m release_intelligence.benchmark.runner \
  --catalog ../../benchmarks/scenarios/catalog.yaml \
  --output ../../benchmark-results.json
```

The runner executes the production deterministic assessor. Ratios are
micro-aggregated across eligible scenarios. A scenario with a zero denominator
is excluded from that metric and recorded in `excluded_denominators`.
Percentiles use nearest rank: sort observed milliseconds and select
`ceil(percentile / 100 * n)`. The gate requires readiness accuracy >= 0.95,
critical recall = 1.0, risk precision >= 0.95, evidence coverage = 1.0, and
invalid evidence rate = 0.0. `unsupported_claim_rate` remains `null` until a
complete human review exists.

AI prose must first be split into atomic claims. A claims JSON document includes
one stable `claim_id`, one single-line statement, and one or more cited
deterministic facts per claim. Review every claim with the schema in
`reviews/schema.json`, then run:

```bash
uv run python -m release_intelligence.benchmark.review \
  --claims ai-claims.json --review claim-review.yaml
```

Incomplete reviews and any unsupported claim exit non-zero. Missing decisions
never produce a zero unsupported-claim rate.
