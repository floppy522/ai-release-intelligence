from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from release_intelligence.adapters.ai.openai_provider import OpenAIExplanationProvider
from release_intelligence.benchmark import live_ai
from release_intelligence.benchmark.live_ai import LiveAIBenchmarkRunner
from release_intelligence.benchmark.schema import load_catalog
from release_intelligence.ports.ai import (
    AIExplanation,
    ExplanationAction,
    ExplanationGroup,
    ExplanationInput,
)

CATALOG = Path(__file__).parents[4] / "benchmarks/scenarios/catalog.yaml"


class GroundedResponses:
    def __init__(
        self, *, unavailable_once: bool = False, invalid_once: bool = False
    ) -> None:
        self.calls: list[dict[str, object]] = []
        self.unavailable_once = unavailable_once
        self.invalid_once = invalid_once

    async def parse(self, **values: object) -> object:
        self.calls.append(values)
        if self.unavailable_once:
            self.unavailable_once = False
            raise ValueError("synthetic provider failure")
        messages = values["input"]
        assert isinstance(messages, list)
        user_message = messages[1]
        assert isinstance(user_message, dict)
        content = user_message["content"]
        assert isinstance(content, str)
        payload = ExplanationInput.model_validate_json(content.split("\n", 1)[1])
        actions = tuple(
            ExplanationAction(
                action=finding.required_action,
                finding_ids=(finding.finding_id,),
                evidence_ids=tuple(
                    evidence.evidence_id for evidence in finding.evidence
                ),
            )
            for finding in payload.findings
        )
        if self.invalid_once and actions:
            self.invalid_once = False
            actions = actions[1:]
        explanation = AIExplanation(
            summary="Candidate model prose is canonicalized by the validator.",
            groups=tuple(
                ExplanationGroup(
                    title="Candidate group",
                    explanation="Candidate model prose.",
                    severity=finding.severity,
                    finding_ids=(finding.finding_id,),
                    evidence_ids=tuple(
                        evidence.evidence_id for evidence in finding.evidence
                    ),
                )
                for finding in payload.findings
            ),
            actions=actions,
            limitations=payload.limitations,
            confidence="HIGH",
            finding_ids=tuple(finding.finding_id for finding in payload.findings),
            evidence_ids=tuple(
                evidence.evidence_id
                for finding in payload.findings
                for evidence in finding.evidence
            ),
        )
        return SimpleNamespace(
            output_parsed=explanation,
            model="gpt-5.6-2026-08-20",
            usage=SimpleNamespace(input_tokens=100, output_tokens=50),
            output=(
                SimpleNamespace(
                    type="message",
                    content=(SimpleNamespace(type="output_text"),),
                ),
            ),
        )


async def test_live_runner_uses_task13_boundary_for_exact_catalog() -> None:
    responses = GroundedResponses()
    provider = OpenAIExplanationProvider(
        client=SimpleNamespace(responses=responses),
        model="gpt-5.6",
        input_cost_per_million=Decimal("2.50"),
        output_cost_per_million=Decimal("10.00"),
    )

    aggregate = await LiveAIBenchmarkRunner(provider).run(load_catalog(CATALOG))

    assert aggregate.scenario_count == 44
    assert aggregate.success_count == 44
    assert aggregate.unavailable_count == 0
    assert aggregate.invalid_grounding_count == 0
    assert aggregate.input_tokens == 4_400
    assert aggregate.output_tokens == 2_200
    assert aggregate.cost == Decimal("0.033000")
    assert aggregate.model == "gpt-5.6"
    assert aggregate.accepted is True
    assert len(responses.calls) == 44
    assert all(call["model"] == "gpt-5.6" for call in responses.calls)
    assert all(call["text_format"] is AIExplanation for call in responses.calls)
    assert all(call["store"] is False for call in responses.calls)
    assert all(0 < float(call["timeout"]) <= 15 for call in responses.calls)


async def test_live_aggregate_contains_no_scenario_or_claim_payloads() -> None:
    responses = GroundedResponses()
    provider = OpenAIExplanationProvider(
        client=SimpleNamespace(responses=responses),
        model="gpt-5.6",
        input_cost_per_million=Decimal("2.50"),
        output_cost_per_million=Decimal("10.00"),
    )

    aggregate = await LiveAIBenchmarkRunner(provider).run(load_catalog(CATALOG))
    serialized = aggregate.model_dump_json()
    keys = set(json.loads(serialized))

    assert keys == {
        "aggregate",
        "model",
        "scenario_count",
        "success_count",
        "unavailable_count",
        "invalid_grounding_count",
        "p50_ms",
        "p95_ms",
        "input_tokens",
        "output_tokens",
        "cost",
        "accepted",
    }
    assert "unsupported_claim_rate" not in serialized
    assert "finding_ids" not in serialized
    assert "evidence_ids" not in serialized
    assert "prompt" not in serialized.casefold()


async def test_live_runner_fails_closed_on_unavailable_or_invalid_grounding() -> None:
    catalog = load_catalog(CATALOG)
    results = []
    for responses in (
        GroundedResponses(unavailable_once=True),
        GroundedResponses(invalid_once=True),
    ):
        provider = OpenAIExplanationProvider(
            client=SimpleNamespace(responses=responses),
            model="gpt-5.6",
            input_cost_per_million=Decimal("2.50"),
            output_cost_per_million=Decimal("10.00"),
        )
        results.append(await LiveAIBenchmarkRunner(provider).run(catalog))

    assert results[0].success_count == 43
    assert results[0].unavailable_count == 1
    assert results[0].invalid_grounding_count == 0
    assert results[0].accepted is False
    assert results[1].success_count == 43
    assert results[1].unavailable_count == 0
    assert results[1].invalid_grounding_count == 1
    assert results[1].accepted is False


def test_live_cli_disables_sdk_retries_and_writes_only_aggregate(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    instances = []

    class FakeAsyncOpenAI:
        def __init__(self, **values: object) -> None:
            self.values = values
            self.responses = GroundedResponses()
            self.closed = False
            instances.append(self)

        async def close(self) -> None:
            self.closed = True

    output = tmp_path / "aggregate.json"
    monkeypatch.setattr(live_ai, "AsyncOpenAI", FakeAsyncOpenAI)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")
    monkeypatch.setenv("ARI_OPENAI_INPUT_COST_PER_MILLION", "2.50")
    monkeypatch.setenv("ARI_OPENAI_OUTPUT_COST_PER_MILLION", "10.00")

    exit_code = live_ai.main(["--catalog", str(CATALOG), "--output", str(output)])

    assert exit_code == 0
    assert len(instances) == 1
    assert instances[0].values == {
        "api_key": "test-only-key",
        "max_retries": 0,
        "timeout": 15.0,
    }
    assert instances[0].closed is True
    assert json.loads(output.read_text())["scenario_count"] == 44
    assert capsys.readouterr() == ("", "")
