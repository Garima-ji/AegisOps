"""Unit tests for Phase 7 Release Candidate Benchmark runner and scenarios."""

from app.benchmarks.catalog import BENCHMARK_CATALOG
from app.benchmarks.runner import BenchmarkRunner


def test_benchmark_catalog_completeness() -> None:
    """Verify that the benchmark catalog contains exactly 14 defined scenarios."""
    assert len(BENCHMARK_CATALOG) == 14
    for i in range(1, 15):
        sc_id = f"SC-{i:02d}"
        assert sc_id in BENCHMARK_CATALOG
        sc_def = BENCHMARK_CATALOG[sc_id]
        assert sc_def.scenario_id == sc_id
        assert sc_def.scenario_name
        assert sc_def.category in {
            "diagnosis",
            "abstention",
            "coverage",
            "resilience",
            "security",
            "safety",
            "simulation",
        }
        assert len(sc_def.safety_invariants) >= 1


def test_benchmark_runner_executes_all_scenarios_successfully() -> None:
    """Verify that BenchmarkRunner executes all 14 scenarios with 100% pass rate."""
    runner = BenchmarkRunner(mode="fixture")
    report = runner.run_all()

    assert report["summary"]["total_scenarios"] == 14
    assert report["summary"]["passed_scenarios"] == 14
    assert report["summary"]["failed_scenarios"] == 0
    assert report["summary"]["pass_rate_percentage"] == 100.0
    assert report["summary"]["overall_status"] == "ALL_GATES_PASSED"

    # Verify key metric invariants
    metrics = report["metrics"]
    assert metrics["detection_and_rca"]["top1_accuracy_percentage"] == 100.0
    assert metrics["detection_and_rca"]["correct_abstention_percentage"] == 100.0
    assert metrics["agent_quality"]["citation_validity_percentage"] == 100.0
    assert metrics["remediation_safety"]["policy_denied_actions_executed"] == 0
    assert metrics["remediation_safety"]["unapproved_actions_executed"] == 0
    assert metrics["remediation_safety"]["production_remediation_attempts"] == 0


def test_benchmark_runner_markdown_rendering() -> None:
    """Verify that Markdown rendering produces valid formatted text."""
    runner = BenchmarkRunner(mode="fixture")
    report = runner.run_all()
    md = runner.render_markdown_report(report)

    assert "# AegisOps — Phase 7 Release Candidate Benchmark Report" in md
    assert "SC-01" in md
    assert "SC-14" in md
    assert "ALL_GATES_PASSED" in md
