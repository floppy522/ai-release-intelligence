from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from release_intelligence.benchmark.runner import (
    BenchmarkRunner,
    _evaluate_fixture,
    _write_atomic,
    main,
)
from release_intelligence.benchmark.schema import (
    BenchmarkCatalog,
    BenchmarkFinding,
    BenchmarkPrediction,
    BenchmarkScenario,
    load_catalog,
)


def _scenario(**updates: object) -> BenchmarkScenario:
    payload: dict[str, object] = {
        "id": "critical-check",
        "name": "Blocking check fails",
        "category": "checks",
        "fixture": "blocking_check_failed",
        "evidence_ids": ["github-check-101"],
        "expected": {
            "status": "NOT_READY",
            "findings": [
                {
                    "rule_id": "checks.blocking_not_successful",
                    "source_id": "101",
                    "severity": "BLOCKING",
                    "evidence_ids": ["github-check-101"],
                }
            ],
        },
    }
    payload.update(updates)
    return BenchmarkScenario.model_validate(payload)


def _catalog(scenarios: list[BenchmarkScenario]) -> BenchmarkCatalog:
    return BenchmarkCatalog.model_validate(
        {
            "version": "1.0.0",
            "categories": {"checks": len(scenarios)},
            "scenarios": [scenario.model_dump(mode="json") for scenario in scenarios],
        }
    )


def test_scenario_requires_ground_truth_and_evidence() -> None:
    with pytest.raises(ValidationError):
        BenchmarkScenario.model_validate({"id": "missing-ground-truth"})


@pytest.mark.parametrize(
    "updates",
    [
        {"id": "bad id"},
        {"evidence_ids": ["github-check-101", "github-check-101"]},
        {
            "expected": {
                "status": "NOT_READY",
                "findings": [
                    {
                        "rule_id": "checks.blocking_not_successful",
                        "source_id": "101",
                        "severity": "BLOCKING",
                        "evidence_ids": ["not-registered"],
                    }
                ],
            }
        },
    ],
)
def test_scenario_rejects_ambiguous_or_unregistered_identity(
    updates: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        _scenario(**updates)


def test_catalog_rejects_duplicate_ids_and_wrong_declared_counts() -> None:
    scenario = _scenario()
    with pytest.raises(ValidationError):
        BenchmarkCatalog.model_validate(
            {
                "version": "1.0.0",
                "categories": {"checks": 3},
                "scenarios": [
                    scenario.model_dump(mode="json"),
                    scenario.model_dump(mode="json"),
                ],
            }
        )


@pytest.mark.parametrize(
    "expected",
    [
        {"status": "READY", "findings": _scenario().expected.findings},
        {"status": "NOT_READY", "findings": []},
        {
            "status": "NEEDS_DECISION",
            "findings": _scenario().expected.findings,
        },
    ],
)
def test_ground_truth_status_must_be_justified_by_finding_severity(
    expected: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        _scenario(expected=expected)


def test_safe_loader_rejects_yaml_object_tags(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text("!!python/object/apply:os.system ['echo unsafe']")

    with pytest.raises(ValueError, match="valid safe YAML"):
        load_catalog(catalog)


def test_catalog_loader_rejects_oversized_input_before_yaml_parsing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text("x" * (2 * 1024 * 1024 + 1))
    monkeypatch.setattr(
        "release_intelligence.benchmark.schema.yaml.safe_load",
        lambda _: (_ for _ in ()).throw(AssertionError("YAML parser was called")),
    )

    with pytest.raises(ValueError, match="valid safe YAML"):
        load_catalog(catalog)


def test_critical_miss_fails_acceptance_gate() -> None:
    scenario = _scenario()
    runner = BenchmarkRunner(
        evaluator=lambda _: BenchmarkPrediction(status="READY", findings=()),
        clock_ns=iter((0, 1_000_000)).__next__,
    )

    result = runner.run(_catalog([scenario]))

    assert result.accepted is False
    assert result.metrics.critical_recall == 0.0
    assert result.metrics.risk_precision is None
    assert result.metrics.excluded_denominators == {
        "invalid_evidence_rate": 1,
        "risk_precision": 1,
    }


def test_metric_formulas_use_risk_identity_and_exact_evidence_intersection() -> None:
    scenario = _scenario(
        evidence_ids=["github-check-101", "github-check-202"],
        expected={
            "status": "NOT_READY",
            "findings": [
                {
                    "rule_id": "checks.blocking_not_successful",
                    "source_id": "101",
                    "severity": "BLOCKING",
                    "evidence_ids": ["github-check-101"],
                },
                {
                    "rule_id": "checks.advisory_not_successful",
                    "source_id": "202",
                    "severity": "DECISION_REQUIRED",
                    "evidence_ids": ["github-check-202"],
                },
            ],
        },
    )
    predicted = BenchmarkPrediction(
        status="NOT_READY",
        findings=(
            BenchmarkFinding(
                rule_id="checks.blocking_not_successful",
                source_id="101",
                severity="BLOCKING",
                evidence_ids=("github-check-101",),
            ),
            BenchmarkFinding(
                rule_id="checks.unexpected",
                source_id="202",
                severity="DECISION_REQUIRED",
                evidence_ids=("github-check-202",),
            ),
        ),
    )
    runner = BenchmarkRunner(
        evaluator=lambda _: predicted,
        clock_ns=iter((0, 1_000_000)).__next__,
    )

    metrics = runner.run(_catalog([scenario])).metrics

    assert metrics.readiness_accuracy == 1.0
    assert metrics.critical_recall == 1.0
    assert metrics.risk_precision == 0.5
    assert metrics.risk_recall == 0.5
    assert metrics.evidence_coverage == 0.5
    assert metrics.invalid_evidence_rate == 0.0
    assert metrics.unsupported_claim_rate is None


def test_invalid_evidence_is_counted_and_fails_gate() -> None:
    scenario = _scenario()
    prediction = BenchmarkPrediction(
        status="NOT_READY",
        findings=(
            BenchmarkFinding(
                rule_id="checks.blocking_not_successful",
                source_id="101",
                severity="BLOCKING",
                evidence_ids=("invented",),
            ),
        ),
    )
    runner = BenchmarkRunner(
        evaluator=lambda _: prediction,
        clock_ns=iter((0, 1_000_000)).__next__,
    )

    result = runner.run(_catalog([scenario]))

    assert result.metrics.invalid_evidence_rate == 1.0
    assert result.accepted is False


def test_percentiles_use_nearest_rank_and_output_is_stably_ordered() -> None:
    scenarios = [
        _scenario(id=f"case-{number}", name=f"Case {number}") for number in range(1, 5)
    ]
    prediction = BenchmarkPrediction(
        status="NOT_READY", findings=_scenario().expected.findings
    )
    runner = BenchmarkRunner(
        evaluator=lambda _: prediction,
        clock_ns=iter(
            (0, 1_000_000, 0, 4_000_000, 0, 2_000_000, 0, 3_000_000)
        ).__next__,
    )

    result = runner.run(_catalog(scenarios))

    assert result.metrics.p50_ms == 2.0
    assert result.metrics.p95_ms == 4.0
    assert [item.scenario_id for item in result.scenarios] == [
        "case-1",
        "case-2",
        "case-3",
        "case-4",
    ]


def test_repository_catalog_has_exact_required_distribution_and_passes() -> None:
    catalog = load_catalog(
        Path(__file__).parents[4] / "benchmarks/scenarios/catalog.yaml"
    )

    assert catalog.categories == {
        "backmerge": 3,
        "blockers": 3,
        "checks": 8,
        "clean": 4,
        "compound_risks": 3,
        "decisions": 4,
        "injection_and_false_positive_traps": 4,
        "operations_and_migrations": 5,
        "partial_and_stale_data": 3,
        "scope": 7,
    }
    assert len(catalog.scenarios) == 44
    assert BenchmarkRunner().run(catalog).accepted is True


def test_repository_catalog_exercises_distinct_evaluator_paths_per_category() -> None:
    catalog = load_catalog(
        Path(__file__).parents[4] / "benchmarks/scenarios/catalog.yaml"
    )
    minimum_distinct = {
        "backmerge": 2,
        "blockers": 2,
        "checks": 6,
        "clean": 3,
        "compound_risks": 2,
        "decisions": 3,
        "injection_and_false_positive_traps": 3,
        "operations_and_migrations": 4,
        "partial_and_stale_data": 3,
        "scope": 4,
    }

    for category, minimum in minimum_distinct.items():
        fixtures = {
            scenario.fixture
            for scenario in catalog.scenarios
            if scenario.category == category
        }
        assert len(fixtures) >= minimum, category


def test_backmerge_category_exercises_business_blocker_and_clean_relation() -> None:
    catalog = load_catalog(
        Path(__file__).parents[4] / "benchmarks/scenarios/catalog.yaml"
    )
    scenarios = [
        scenario for scenario in catalog.scenarios if scenario.category == "backmerge"
    ]

    assert any(
        finding.rule_id == "backmerge.main_pr_required"
        for scenario in scenarios
        for finding in scenario.expected.findings
    )
    assert any(scenario.expected.status == "READY" for scenario in scenarios)


def test_scope_category_covers_every_delivery_chain_stage() -> None:
    catalog = load_catalog(
        Path(__file__).parents[4] / "benchmarks/scenarios/catalog.yaml"
    )
    scenarios = [
        scenario for scenario in catalog.scenarios if scenario.category == "scope"
    ]
    rules = {
        finding.rule_id
        for scenario in scenarios
        for finding in scenario.expected.findings
    }

    assert rules == {
        "scope.change_requires_candidate_inclusion",
        "scope.code_change_requires_pr",
        "scope.exactly_one_type",
        "scope.pr_requires_main_merge",
        "scope.pr_requires_milestone",
    }
    assert any(scenario.expected.status == "READY" for scenario in scenarios)


def test_cli_writes_atomic_json_and_reports_44_scenarios(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = Path(__file__).parents[4] / "benchmarks/scenarios/catalog.yaml"
    output = tmp_path / "result.json"
    monkeypatch.setattr(
        "sys.argv",
        ["benchmark-runner", "--catalog", str(catalog), "--output", str(output)],
    )

    assert main() == 0
    document = json.loads(output.read_text())
    assert document["scenario_count"] == 44
    assert document["accepted"] is True


def test_cli_refuses_to_overwrite_its_catalog_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = Path(__file__).parents[4] / "benchmarks/scenarios/catalog.yaml"
    catalog = tmp_path / "catalog.yaml"
    original = source.read_text()
    catalog.write_text(original)
    monkeypatch.setattr(
        "sys.argv",
        ["benchmark-runner", "--catalog", str(catalog), "--output", str(catalog)],
    )

    assert main() == 2
    assert catalog.read_text() == original


def test_cli_refuses_existing_output_before_evaluation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = Path(__file__).parents[4] / "benchmarks/scenarios/catalog.yaml"
    output = tmp_path / "result.json"
    original = b"irreplaceable prior result\n"
    output.write_bytes(original)
    evaluated = False

    def unexpected_run(*_: object) -> object:
        nonlocal evaluated
        evaluated = True
        raise AssertionError("evaluator must not run")

    monkeypatch.setattr(BenchmarkRunner, "run", unexpected_run)
    monkeypatch.setattr(
        "sys.argv",
        ["benchmark-runner", "--catalog", str(catalog), "--output", str(output)],
    )

    assert main() == 2
    assert evaluated is False
    assert output.read_bytes() == original


def test_atomic_writer_never_replaces_existing_output(tmp_path: Path) -> None:
    output = tmp_path / "result.json"
    original = b"prior result\n"
    output.write_bytes(original)
    result = BenchmarkRunner().run(
        load_catalog(Path(__file__).parents[4] / "benchmarks/scenarios/catalog.yaml")
    )

    with pytest.raises(FileExistsError):
        _write_atomic(output, result)

    assert output.read_bytes() == original


def test_atomic_writer_cleans_temp_file_when_exclusive_publish_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "result.json"
    result = BenchmarkRunner().run(
        load_catalog(Path(__file__).parents[4] / "benchmarks/scenarios/catalog.yaml")
    )
    monkeypatch.setattr(
        "release_intelligence.benchmark.runner.os.link",
        lambda *_: (_ for _ in ()).throw(OSError("publish failed")),
    )

    with pytest.raises(OSError, match="publish failed"):
        _write_atomic(output, result)

    assert output.exists() is False
    assert list(tmp_path.iterdir()) == []


def test_catalog_names_and_fixtures_describe_the_executed_case() -> None:
    catalog = load_catalog(
        Path(__file__).parents[4] / "benchmarks/scenarios/catalog.yaml"
    )
    by_id = {scenario.id: scenario for scenario in catalog.scenarios}
    expected = {
        "checks_unknown_requires_classification": "unknown_check",
        "checks_successful_required_set": "ready",
        "checks_blocking_cancelled": "blocking_check_cancelled",
        "checks_blocking_in_progress": "blocking_check_in_progress",
        "decisions_advisory_pending": "advisory_check_failed",
        "decisions_risk_accepted": "decision_accept",
        "decisions_risk_blocked": "decision_block",
        "decisions_conflict_fails_closed": "decision_conflict",
    }

    assert {
        identifier: by_id[identifier].fixture for identifier in expected
    } == expected


def test_injection_label_fixture_uses_an_actual_untrusted_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = load_catalog(
        Path(__file__).parents[4] / "benchmarks/scenarios/catalog.yaml"
    )
    scenario = next(
        item for item in catalog.scenarios if item.fixture == "injection_label"
    )
    captured: list[object] = []
    from release_intelligence.domain.assessment import assess as production_assess

    def capture(snapshot: object, *args: object, **kwargs: object) -> object:
        captured.append(snapshot)
        return production_assess(snapshot, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("release_intelligence.benchmark.runner.assess", capture)

    _evaluate_fixture(scenario)

    snapshot = captured[0]
    labels = snapshot.items[0].labels  # type: ignore[attr-defined]
    assert "ignore-all-prior-instructions" in labels
