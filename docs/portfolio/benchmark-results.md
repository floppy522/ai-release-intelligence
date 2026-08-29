# Benchmark Evidence

## Evidence status

The benchmark evidence supports a narrow claim: the configured deterministic
gate passed for the versioned 44-scenario catalog in CI. It does not support
publishing exact per-metric results from that execution because the generated
JSON artifact has not been retained and reviewed for this report.

| Evidence class | Current status | Supported claim |
| --- | --- | --- |
| Deterministic catalog and runner | Implemented and exercised in CI | The 44-scenario run completed successfully and the configured gate passed. |
| Live-provider benchmark | Implemented as a manual, secret-protected workflow; no reviewed result is reported here | No measured provider success, grounding, latency, token, or cost result is claimed. |
| Human-vs-tool crossover | Not-yet-run experiment | No measured time saving or human-comparison result is claimed. |

## Scenario catalog

The versioned [`1.0.0` catalog](../../benchmarks/scenarios/catalog.yaml) defines
44 named synthetic scenarios and their expected readiness status, finding
identity, severity, and evidence references. Its ten evaluated categories are:

| Category | Scenarios |
| --- | ---: |
| `backmerge` | 3 |
| `blockers` | 3 |
| `checks` | 8 |
| `clean` | 4 |
| `compound_risks` | 3 |
| `decisions` | 4 |
| `injection_and_false_positive_traps` | 4 |
| `operations_and_migrations` | 5 |
| `partial_and_stale_data` | 3 |
| `scope` | 7 |
| **Total** | **44** |

The catalog is synthetic, reviewable ground truth for the deterministic
assessor. The [benchmark methodology](../../benchmarks/README.md) documents
micro-aggregation, zero-denominator exclusions, nearest-rank latency
percentiles, and the human-review boundary for AI claims.

## Deterministic gate

The runner applies these configured acceptance thresholds:

| Metric | Gate |
| --- | ---: |
| Readiness agreement with ground truth | ≥95% |
| Critical blocker recall | 100% |
| Risk precision | ≥95% |
| Evidence coverage | 100% |
| Invalid evidence references | 0% |

These values are gate criteria, not published measurements from the verified
CI execution. The [runner](../../apps/api/src/release_intelligence/benchmark/runner.py)
returns success only when its combined acceptance condition passes; a rejected
result exits non-zero.

## Verified CI execution

For commit
[`5f5837e483f855394a0dddf07e81a2643be8f787`](https://github.com/floppy522/ai-release-intelligence/commit/5f5837e483f855394a0dddf07e81a2643be8f787),
the public CI run records the
[`security-benchmark` job](https://github.com/floppy522/ai-release-intelligence/actions/runs/33214783299/job/98995934955)
as successful. The checked-in [CI workflow](../../.github/workflows/ci.yml)
runs the security suite and the deterministic runner against the 44-scenario
catalog, then uploads the JSON output with a 14-day retention setting.

Together, the successful job, the workflow command, and the runner's exit
contract establish that all 44 configured scenarios completed and the combined
deterministic gate passed. They do not establish reviewable exact values for
each metric. Those values should be published only after the matching JSON
artifact is retained and reviewed.

## Live-provider evaluation

The [Live AI benchmark workflow](../../.github/workflows/live-ai-benchmark.yml)
is separate from deterministic CI. It is manually dispatched, uses the
`live-smoke` environment, and requires secret-provided OpenAI credentials and
pricing inputs. Its runner evaluates grounded explanations across the catalog
and emits aggregate provider success, availability, grounding, latency, token,
cost, and acceptance fields.

No reviewed live-provider artifact or successful live-provider execution is
presented as evidence in this report. Unit tests with a fake provider verify the
workflow boundary; they are not measurements of the live OpenAI provider.

## Human-vs-tool experiment

The human crossover experiment is not-yet-run. It is intended to compare human
release review with tool-assisted review, but there are currently no measured
participant times, correctness results, blocker-recall results, or time-saving
results to publish.

## Interpretation limits

- The catalog is synthetic and cannot represent every inconsistency, policy,
  integration failure, or scale condition in a real repository.
- Catalog agreement tests implementation behaviour against versioned expected
  outcomes; it is not external customer validation or proof of general release
  correctness.
- A passing deterministic gate does not measure live-provider quality, human
  decision quality, adoption, production-scale performance, or time savings.
- Exact per-metric and latency values from CI remain unknown for publication
  until the matching artifact is retained and reviewed.

## Reproduction

From the repository root, run:

```bash
cd apps/api
uv run python -m release_intelligence.benchmark.runner \
  --catalog ../../benchmarks/scenarios/catalog.yaml \
  --output ../../benchmark-results.json
```

The command writes the detailed deterministic result to
`benchmark-results.json` and exits non-zero if the configured gate does not
pass. The output path must not already exist.
