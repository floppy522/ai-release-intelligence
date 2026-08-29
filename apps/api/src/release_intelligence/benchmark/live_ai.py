from __future__ import annotations

import argparse
import asyncio
import math
import os
from collections.abc import Sequence
from decimal import Decimal, DecimalException
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal, cast

from openai import AsyncOpenAI
from pydantic import Field

from release_intelligence.adapters.ai.openai_provider import (
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT_SECONDS,
    OpenAIClient,
    OpenAIExplanationProvider,
)
from release_intelligence.application.explanations import (
    INPUT_LIMITATIONS,
    AIExplanationRejected,
    ExplanationValidator,
)
from release_intelligence.benchmark.runner import BenchmarkRunner
from release_intelligence.benchmark.schema import (
    BenchmarkCatalog,
    BenchmarkPrediction,
    BenchmarkScenario,
    StrictModel,
    load_catalog,
)
from release_intelligence.ports.ai import (
    AIExplanationProvider,
    AIExplanationUnavailable,
    ExplanationEvidence,
    ExplanationFinding,
    ExplanationInput,
    normalize_safe_unicode,
)

EXPECTED_SCENARIOS = 44


class LiveAIBenchmarkAggregate(StrictModel):
    aggregate: Literal[True] = True
    model: Literal["gpt-5.6"] = DEFAULT_MODEL
    scenario_count: int = Field(ge=0)
    success_count: int = Field(ge=0)
    unavailable_count: int = Field(ge=0)
    invalid_grounding_count: int = Field(ge=0)
    p50_ms: float = Field(ge=0)
    p95_ms: float = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost: Decimal = Field(ge=0, decimal_places=6)
    accepted: bool


class LiveAIBenchmarkRunner:
    def __init__(self, provider: AIExplanationProvider) -> None:
        self._provider = provider

    async def run(self, catalog: BenchmarkCatalog) -> LiveAIBenchmarkAggregate:
        deterministic = BenchmarkRunner().run(catalog)
        predictions = {
            item.scenario_id: item.predicted for item in deterministic.scenarios
        }
        successes = 0
        unavailable = 0
        invalid_grounding = 0
        latencies: list[float] = []
        input_tokens = 0
        output_tokens = 0
        cost = Decimal(0)

        for scenario in sorted(catalog.scenarios, key=lambda item: item.id):
            payload = build_live_explanation_input(scenario, predictions[scenario.id])
            try:
                generated = await self._provider.explain(payload)
            except AIExplanationUnavailable:
                unavailable += 1
                continue
            except Exception:  # noqa: BLE001 - live provider boundary fails closed
                unavailable += 1
                continue
            try:
                canonical = ExplanationValidator(payload).validate(generated)
                if canonical.metadata is None:
                    raise AIExplanationRejected()
                repeated = ExplanationValidator(payload).validate(canonical)
                if repeated.model_dump() != canonical.model_dump():
                    raise AIExplanationRejected()
            except (AIExplanationRejected, TypeError, ValueError):
                invalid_grounding += 1
                continue
            metadata = canonical.metadata
            assert metadata is not None
            successes += 1
            latencies.append(float(metadata.latency_seconds * 1_000))
            input_tokens += metadata.input_tokens
            output_tokens += metadata.output_tokens
            cost += metadata.cost

        scenario_count = len(catalog.scenarios)
        accepted = (
            deterministic.accepted
            and scenario_count == EXPECTED_SCENARIOS
            and successes == EXPECTED_SCENARIOS
            and unavailable == 0
            and invalid_grounding == 0
        )
        return LiveAIBenchmarkAggregate(
            scenario_count=scenario_count,
            success_count=successes,
            unavailable_count=unavailable,
            invalid_grounding_count=invalid_grounding,
            p50_ms=_nearest_rank(latencies, 50),
            p95_ms=_nearest_rank(latencies, 95),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
            accepted=accepted,
        )


def build_live_explanation_input(
    scenario: BenchmarkScenario,
    prediction: BenchmarkPrediction,
) -> ExplanationInput:
    findings = tuple(
        ExplanationFinding(
            finding_id=f"benchmark-{scenario.id}-{index}",
            rule_id=finding.rule_id,
            severity=cast(
                Literal[
                    "BLOCKING",
                    "DECISION_REQUIRED",
                    "WARNING",
                    "INSUFFICIENT_DATA",
                ],
                finding.severity,
            ),
            summary=f"{finding.severity} deterministic finding {index}",
            required_action=f"Resolve deterministic finding {index} before release",
            evidence=tuple(
                ExplanationEvidence(
                    evidence_id=evidence_id,
                    source_type="benchmark_evidence",
                    source_id=normalize_safe_unicode(
                        finding.source_id,
                        maximum=200,
                        truncate=True,
                    ),
                )
                for evidence_id in finding.evidence_ids
            ),
        )
        for index, finding in enumerate(prediction.findings, start=1)
    )
    return ExplanationInput(
        deterministic_status=cast(
            Literal["READY", "NOT_READY", "NEEDS_DECISION", "INSUFFICIENT_DATA"],
            prediction.status,
        ),
        release_name=f"Benchmark {scenario.id}",
        source_fetched_at="2026-08-16T12:00:00+00:00",
        findings=findings,
        limitations=INPUT_LIMITATIONS,
    )


def _nearest_rank(values: list[float], percentile: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = math.ceil(percentile / 100 * len(ordered))
    return round(ordered[max(rank - 1, 0)], 3)


def _write_atomic(path: Path, aggregate: LiveAIBenchmarkAggregate) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(aggregate.model_dump_json(indent=2))
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.link(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


async def _run_live(catalog: Path, output: Path) -> bool:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key or os.environ.get("OPENAI_MODEL", DEFAULT_MODEL) != DEFAULT_MODEL:
        raise ValueError("live AI configuration is invalid")
    try:
        input_price = Decimal(os.environ["ARI_OPENAI_INPUT_COST_PER_MILLION"])
        output_price = Decimal(os.environ["ARI_OPENAI_OUTPUT_COST_PER_MILLION"])
    except (KeyError, DecimalException):
        raise ValueError("live AI configuration is invalid") from None

    client = AsyncOpenAI(
        api_key=api_key,
        max_retries=0,
        timeout=DEFAULT_TIMEOUT_SECONDS,
    )
    try:
        provider = OpenAIExplanationProvider(
            client=cast(OpenAIClient, client),
            model=DEFAULT_MODEL,
            input_cost_per_million=input_price,
            output_cost_per_million=output_price,
            timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
        )
        aggregate = await LiveAIBenchmarkRunner(provider).run(load_catalog(catalog))
        _write_atomic(output, aggregate)
        return aggregate.accepted
    finally:
        await client.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the grounded live AI benchmark")
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.catalog.resolve() == args.output.resolve():
            raise ValueError("catalog and output paths must differ")
        if os.path.lexists(args.output):
            raise FileExistsError("live benchmark output already exists")
        accepted = asyncio.run(_run_live(args.catalog, args.output))
    except (OSError, TypeError, ValueError):
        return 2
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
