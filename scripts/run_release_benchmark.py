"""Release Candidate Validation & Fixed Benchmark CLI for AegisOps (Phase 7).

Executes all 14 fixed operational scenarios across offline fixture or controlled
live simulation modes and emits structured JSON and Markdown reports.

Usage:
  python scripts/run_release_benchmark.py --mode fixture
  python scripts/run_release_benchmark.py --mode live-sim --summary
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Ensure backend package in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.benchmarks.runner import BenchmarkRunner  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("run_release_benchmark")


def main() -> int:
    """CLI entrypoint for running the Phase 7 release benchmark."""
    parser = argparse.ArgumentParser(
        description="AegisOps Release Candidate 14-Scenario Validation Harness"
    )
    parser.add_argument(
        "--mode",
        choices=["fixture", "live-sim"],
        default="fixture",
        help="Benchmark execution mode (default: fixture)",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print concise summary scorecard only",
    )
    parser.add_argument(
        "--json-out",
        type=str,
        default=str(PROJECT_ROOT / "ml" / "artifacts" / "release_benchmark_report.json"),
        help="Output path for JSON report",
    )
    parser.add_argument(
        "--md-out",
        type=str,
        default=str(PROJECT_ROOT / "docs" / "PHASE_7_EVALUATION.md"),
        help="Output path for Markdown evaluation report",
    )

    args = parser.parse_args()

    print("=" * 80)
    print(f"AEGISOPS PHASE 7 RELEASE CANDIDATE BENCHMARK HARNESS [Mode: {args.mode}]")
    print("=" * 80)

    runner = BenchmarkRunner(mode=args.mode)
    report = runner.run_all()

    # Save reports
    json_path = Path(args.json_out)
    md_path = Path(args.md_out)
    runner.save_reports(report, json_output_path=json_path, md_output_path=md_path)

    # Display CLI Scorecard
    summary = report["summary"]
    metrics = report["metrics"]

    print("\n" + "=" * 80)
    print("RELEASE BENCHMARK RESULTS SCORECARD")
    print("=" * 80)
    print(f"Overall Status        : {summary['overall_status']}")
    print(f"Scenarios Evaluated   : {summary['total_scenarios']}")
    print(f"Passed Scenarios      : {summary['passed_scenarios']}")
    print(f"Failed Scenarios      : {summary['failed_scenarios']}")
    print(f"Pass Rate             : {summary['pass_rate_percentage']}%")
    print(f"Benchmark Duration    : {summary['total_duration_seconds']}s")
    print("-" * 80)
    print(
        f"Detection & RCA Top-1 : {metrics['detection_and_rca']['top1_accuracy_percentage']}% ({metrics['detection_and_rca']['top1_matches']}/{metrics['detection_and_rca']['eligible_diagnosis_scenarios']})"
    )
    print(
        f"Correct Abstention    : {metrics['detection_and_rca']['correct_abstention_percentage']}% ({metrics['detection_and_rca']['abstention_scenarios_total']}/{metrics['detection_and_rca']['abstention_scenarios_total']})"
    )
    print(f"Mean Reciprocal Rank  : {metrics['detection_and_rca']['mean_reciprocal_rank']}")
    print(f"Citation Validity     : {metrics['agent_quality']['citation_validity_percentage']}%")
    print(
        f"Policy Denied Executed: {metrics['remediation_safety']['policy_denied_actions_executed']} (Asserted == 0)"
    )
    print(
        f"Unapproved Executed   : {metrics['remediation_safety']['unapproved_actions_executed']} (Asserted == 0)"
    )
    print(
        f"Live Mutations Attempt: {metrics['remediation_safety']['production_remediation_attempts']} (Asserted == 0)"
    )
    print(
        f"Scenario p50 Duration : {metrics['performance_and_reliability']['p50_scenario_duration_seconds']}s"
    )
    print(
        f"Scenario p95 Duration : {metrics['performance_and_reliability']['p95_scenario_duration_seconds']}s"
    )
    print("=" * 80)

    if not args.summary:
        print("\nSCENARIO-BY-SCENARIO STATUS:")
        print("-" * 80)
        for s in report["scenarios"]:
            rc_info = s.get("root_cause_assessment") or "Abstained / N/A"
            print(
                f"[{s['status']:6s}] {s['scenario_id']} : {s['scenario_name']:45s} ({s['elapsed_time_seconds']:.3f}s) -> {rc_info}"
            )
        print("=" * 80)

    print("\nArtifacts generated:")
    print(f" - JSON Report : {json_path}")
    print(f" - Markdown Doc: {md_path}\n")

    return 0 if summary["overall_status"] == "ALL_GATES_PASSED" else 1


if __name__ == "__main__":
    sys.exit(main())
