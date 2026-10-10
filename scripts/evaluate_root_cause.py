"""Root-cause analysis evaluation and benchmarking CLI script (Phase 4).

Evaluates the multi-factor deterministic root-cause ranker against Phase 2 fault-injection ground truth,
comparing against the naive earliest-anomaly baseline.

CLI Options:
  --data-source synthetic|db  (default: synthetic)
  --output-json PATH          (default: ml/artifacts/root_cause_evaluation_report.json)
  --output-markdown PATH      (default: docs/PHASE_4_EVALUATION_REPORT.md)
"""

import argparse
import json
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# Ensure backend app is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from sqlalchemy import select

from app.db.models.experiment import ExperimentModel
from app.db.models.ml import AnomalyDetectionModel
from app.db.models.telemetry import TraceSpanModel
from app.db.session import check_database_connection, get_session_factory
from app.ingestion.generator import DeterministicTelemetryGenerator
from app.ml.baselines import RollingMadAnomalyDetector, ThresholdAnomalyDetector
from app.ml.features import extract_window_features
from app.rca.evaluation import (
    IncidentEvaluationSample,
    RootCauseBenchmarkReport,
    RootCauseEvaluator,
)
from app.rca.schemas import AnomalyEvent
from app.topology.extractor import build_topology_from_spans
from app.topology.models import TopologyGraph, _to_utc
from app.topology.service import TopologyService

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("evaluate_root_cause")


def generate_synthetic_benchmark_samples() -> list[IncidentEvaluationSample]:
    """Generate reproducible multi-scenario incident samples with ground truth."""
    generator = DeterministicTelemetryGenerator(seed=42)
    start_time = datetime(2026, 10, 10, 0, 0, 0, tzinfo=UTC)

    # 1. Generate normal background traffic to build baseline topology
    norm_batch = generator.generate_normal_traffic(
        count=150, start_time=start_time, interval_seconds=2.0
    )
    topo_graph = build_topology_from_spans(
        spans=norm_batch.traces,
        time_window_start=start_time,
        time_window_end=start_time + timedelta(seconds=300),
        version="benchmark-topo-v1",
    )

    threshold_detector = ThresholdAnomalyDetector()
    rolling_mad_detector = RollingMadAnomalyDetector()

    samples: list[IncidentEvaluationSample] = []

    # Scenarios to generate and evaluate
    scenarios_to_run = [
        ("latency_spike", "payment-service", ["order-service"], {"latency_ms": 750.0}, 400),
        ("error_surge", "payment-service", ["order-service"], {"error_rate": 0.70}, 800),
        ("service_timeout", "payment-service", ["order-service"], {"timeout_ms": 3500.0}, 1200),
    ]

    for sc_type, initiating_svc, symptoms, custom_params, offset_sec in scenarios_to_run:
        fault_start = start_time + timedelta(seconds=offset_sec)
        fault_batch = generator.generate_fault_traffic(
            fault_type=sc_type,  # type: ignore[arg-type]
            experiment_id=f"exp-{sc_type}-bench",
            count=40,
            start_time=fault_start,
            interval_seconds=2.0,
            custom_params=custom_params,
        )

        # Detect anomalies across services in this fault window
        fault_end = fault_start + timedelta(seconds=80)
        anomaly_events: list[AnomalyEvent] = []

        all_tx_services = set()
        for tx in fault_batch.transactions:
            for sp in tx.spans:
                all_tx_services.add(sp.service_name)

        # Extract features and detect anomalies for each service
        for svc in sorted(all_tx_services):
            svc_metrics = [m for m in fault_batch.metrics if m.service_name == svc]
            svc_logs = [lg for lg in fault_batch.logs if lg.service_name == svc]
            svc_spans = [s for s in fault_batch.traces if s.service_name == svc]

            features = extract_window_features(
                service_name=svc,
                window_end=fault_end,
                window_seconds=80.0,
                metrics=svc_metrics,
                logs=svc_logs,
                spans=svc_spans,
            )

            # Evaluate with threshold detector
            res_th = threshold_detector.detect(
                features=features, service_name=svc, timestamp=fault_end
            )
            if res_th.is_anomaly:
                # Associate earliest span timestamp as onset
                svc_onset = min((s.start_time for s in svc_spans), default=fault_start)
                anomaly_events.append(
                    AnomalyEvent(
                        service_name=svc,
                        timestamp=_to_utc(svc_onset),
                        detector_name="threshold",
                        anomaly_score=res_th.anomaly_score,
                        severity=res_th.severity,
                        triggering_features=res_th.triggering_features,
                        explanation=res_th.explanation,
                        trace_id=svc_spans[0].trace_id if svc_spans else None,
                    )
                )

            # Also test rolling MAD
            res_mad = rolling_mad_detector.detect(
                features=features, service_name=svc, timestamp=fault_end
            )

            if res_mad.is_anomaly:
                svc_onset = min((s.start_time for s in svc_spans), default=fault_start)
                anomaly_events.append(
                    AnomalyEvent(
                        service_name=svc,
                        timestamp=_to_utc(svc_onset),
                        detector_name="rolling_mad",
                        anomaly_score=res_mad.anomaly_score,
                        severity=res_mad.severity,
                        triggering_features=res_mad.triggering_features,
                        explanation=res_mad.explanation,
                        trace_id=svc_spans[0].trace_id if svc_spans else None,
                    )
                )

        # Trace error span summary
        trace_summary: dict[str, dict[str, Any]] = {}
        error_spans = [s for s in fault_batch.traces if getattr(s, "status_code", "") == "ERROR"]
        by_trace: dict[str, list[Any]] = {}
        for esp in error_spans:
            by_trace.setdefault(esp.trace_id, []).append(esp)

        for tr_id, spans_in_tr in by_trace.items():
            parent_ids = {s.parent_span_id for s in spans_in_tr if s.parent_span_id}
            for s in spans_in_tr:
                stats = trace_summary.setdefault(
                    s.service_name,
                    {"error_count": 0, "is_leaf_error": False, "traces": set()},
                )
                stats["error_count"] += 1
                stats["traces"].add(tr_id)
                if s.span_id not in parent_ids:
                    stats["is_leaf_error"] = True

        sample = IncidentEvaluationSample(
            incident_id=f"bench-inc-{sc_type}",
            scenario_name=sc_type,
            ground_truth_initiating_service=initiating_svc,
            symptom_services=symptoms,
            anomaly_events=anomaly_events,
            topology=topo_graph,
            trace_spans_summary=trace_summary,
            metadata={"custom_params": custom_params},
        )
        samples.append(sample)

    return samples


def run_database_evaluation() -> list[IncidentEvaluationSample]:
    """Retrieve completed fault experiments and observed anomalies from PostgreSQL."""
    is_healthy, msg, _ = check_database_connection()
    if not is_healthy:
        raise RuntimeError(f"Database connection check failed: {msg}")

    session_factory = get_session_factory()
    with session_factory() as session:
        # Query completed experiments with unambiguous ground truth
        stmt = (
            select(ExperimentModel)
            .where(
                ExperimentModel.status == "COMPLETED",
                ExperimentModel.initiating_service.isnot(None),
            )
            .order_by(ExperimentModel.start_time.asc())
        )
        experiments = list(session.execute(stmt).scalars().all())

        if not experiments:
            logger.warning(
                "No completed fault injection experiments with ground truth found in database."
            )
            return []

        # Get latest topology
        topo_model = TopologyService.get_latest_topology(session)
        topo_graph = TopologyGraph()
        if topo_model:
            for s in topo_model.services_json or []:
                topo_graph.add_service(s)
            edges = TopologyService.get_edges_for_snapshot(session, topo_model.id)
            for e in edges:
                topo_graph.add_edge(
                    caller=e.caller_service,
                    downstream=e.downstream_service,
                    call_count=e.call_count,
                    error_count=e.error_count,
                    error_rate=e.error_rate,
                )

        samples: list[IncidentEvaluationSample] = []
        for exp in experiments:
            if not exp.end_time:
                continue

            # Query anomalies in window
            anom_stmt = (
                select(AnomalyDetectionModel)
                .where(
                    AnomalyDetectionModel.timestamp >= exp.start_time,
                    AnomalyDetectionModel.timestamp <= exp.end_time,
                    AnomalyDetectionModel.is_anomaly.is_(True),
                )
                .order_by(AnomalyDetectionModel.timestamp.asc())
            )
            anom_rows = list(session.execute(anom_stmt).scalars().all())

            events = [
                AnomalyEvent(
                    service_name=r.service_name,
                    timestamp=_to_utc(r.timestamp),
                    detector_name=r.detector_name,
                    anomaly_score=r.anomaly_score,
                    severity=r.severity,  # type: ignore[arg-type]
                    triggering_features=r.triggering_features or [],
                    explanation=r.explanation,
                    trace_id=r.trace_id,
                )
                for r in anom_rows
            ]

            # Query error spans
            span_stmt = select(TraceSpanModel).where(
                TraceSpanModel.start_time >= exp.start_time,
                TraceSpanModel.start_time <= exp.end_time,
                TraceSpanModel.status_code == "ERROR",
            )
            error_spans = list(session.execute(span_stmt).scalars().all())
            trace_summary: dict[str, dict[str, Any]] = {}
            by_trace: dict[str, list[TraceSpanModel]] = {}
            for sp in error_spans:
                by_trace.setdefault(sp.trace_id, []).append(sp)

            for tr_id, spans_in_tr in by_trace.items():
                parent_ids = {s.parent_span_id for s in spans_in_tr if s.parent_span_id}
                for s in spans_in_tr:
                    stats = trace_summary.setdefault(
                        s.service_name,
                        {"error_count": 0, "is_leaf_error": False, "traces": set()},
                    )
                    stats["error_count"] += 1
                    stats["traces"].add(tr_id)
                    if s.span_id not in parent_ids:
                        stats["is_leaf_error"] = True

            sample = IncidentEvaluationSample(
                incident_id=exp.id,
                scenario_name=exp.scenario_name,
                ground_truth_initiating_service=exp.initiating_service,
                symptom_services=exp.symptom_services or [],
                anomaly_events=events,
                topology=topo_graph,
                trace_spans_summary=trace_summary,
            )
            samples.append(sample)

        return samples


def render_markdown_report(report: RootCauseBenchmarkReport, data_source: str) -> str:
    """Render human-readable markdown evaluation report."""
    det = report.deterministic_ranker_metrics
    base = report.earliest_anomaly_baseline_metrics

    lines: list[str] = [
        "# AegisOps — Phase 4 Root-Cause Analysis Evaluation Report",
        "",
        f"**Generated At**: {report.generated_at}  ",
        f"**Data Source Mode**: `{data_source}`  ",
        f"**Total Incidents Evaluated**: {report.total_incidents}  ",
        f"**Eligible Incidents**: {report.eligible_incidents}  ",
        f"**Excluded Incidents**: {report.excluded_incidents}  ",
        "",
    ]

    if report.exclusion_reasons:
        lines.append("### Exclusion Reasons")
        for reason, cnt in report.exclusion_reasons.items():
            lines.append(f"- **{reason}**: {cnt}")
        lines.append("")

    lines.extend(
        [
            "## 1. Executive Summary & Benchmark Comparison",
            "",
            "| Metric | Multi-Factor Deterministic Ranker | Earliest Anomaly Baseline | Absolute Delta |",
            "| :--- | :--- | :--- | :--- |",
            f"| **Top-1 Accuracy** | **{det.top1_accuracy * 100:.1f}%** | {base.top1_accuracy * 100:.1f}% | {(det.top1_accuracy - base.top1_accuracy) * 100:+.1f}% |",
            f"| **Top-3 Accuracy** | **{det.top3_accuracy * 100:.1f}%** | {base.top3_accuracy * 100:.1f}% | {(det.top3_accuracy - base.top3_accuracy) * 100:+.1f}% |",
            f"| **Mean Reciprocal Rank (MRR)** | **{det.mean_reciprocal_rank:.4f}** | {base.mean_reciprocal_rank:.4f} | {det.mean_reciprocal_rank - base.mean_reciprocal_rank:+.4f} |",
            f"| **Abstention Rate** | **{det.abstention_rate * 100:.1f}%** | {base.abstention_rate * 100:.1f}% | {(det.abstention_rate - base.abstention_rate) * 100:+.1f}% |",
            f"| **Mean Diagnosis Latency** | {det.mean_diagnosis_latency_sec if det.mean_diagnosis_latency_sec is not None else 'N/A'}s | {base.mean_diagnosis_latency_sec if base.mean_diagnosis_latency_sec is not None else 'N/A'}s | - |",
            "",
            "## 2. Accuracy by Fault Scenario",
            "",
            "| Fault Scenario | Incidents | Deterministic Top-1 | Deterministic Top-3 | Baseline Top-1 |",
            "| :--- | :--- | :--- | :--- | :--- |",
        ]
    )

    for sc_name, sc_data in det.scenario_breakdown.items():
        base_sc = base.scenario_breakdown.get(sc_name, {})
        base_top1 = f"{base_sc.get('top1_accuracy', 0.0) * 100:.1f}%"
        lines.append(
            f"| `{sc_name}` | {int(sc_data['count'])} | {sc_data['top1_accuracy'] * 100:.1f}% | "
            f"{sc_data['top3_accuracy'] * 100:.1f}% | {base_top1} |"
        )

    lines.extend(
        [
            "",
            "## 3. Topology Coverage Breakdown",
            "",
            "| Coverage Category | Incidents | Top-1 Accuracy | Top-3 Accuracy | MRR |",
            "| :--- | :--- | :--- | :--- | :--- |",
        ]
    )

    for cov_name, cov_data in det.topology_coverage_breakdown.items():
        lines.append(
            f"| `{cov_name}` | {int(cov_data['count'])} | {cov_data['top1_accuracy'] * 100:.1f}% | "
            f"{cov_data['top3_accuracy'] * 100:.1f}% | {cov_data['mrr']:.4f} |"
        )

    lines.extend(
        [
            "",
            "## 4. Per-Incident Ranking Breakdown",
            "",
            "| Incident ID | Scenario | Ground Truth Service | In Topology | Deterministic Top-1 | Rank | Correct? | Baseline Top-1 |",
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
        ]
    )

    for item in report.detailed_incident_results:
        hit_mark = "✅ Yes" if item["deterministic_hit_top1"] else "❌ No"
        lines.append(
            f"| `{item['incident_id']}` | `{item['scenario']}` | `{item['ground_truth_service']}` | "
            f"{item['in_topology']} | `{item['deterministic_top1']}` | #{item['deterministic_rank']} | "
            f"{hit_mark} | `{item['baseline_top1']}` |"
        )

    lines.extend(
        [
            "",
            "## 5. Methodological Rigor & Ground-Truth Isolation",
            "",
            "- **Zero Information Leakage**: Ground-truth initiating services and fault scenario labels are strictly segregated from ranking features and inference.",
            "- **Deterministic Reproducibility**: Ranking uses deterministic scoring ($S = 0.35 \\cdot S_{\\text{origin}} + 0.30 \\cdot S_{\\text{temp}} + 0.20 \\cdot S_{\\text{impact}} + 0.15 \\cdot S_{\\text{sev}}$) with stable tie-breaking.",
            "- **Honest Abstention**: When candidate scores fall below 0.20 or no operational anomalies are present, the subsystem outputs `INSUFFICIENT_EVIDENCE` without guessing.",
        ]
    )

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate AegisOps Root-Cause Ranking Baseline")
    parser.add_argument(
        "--data-source",
        choices=["synthetic", "db"],
        default="synthetic",
        help="Evaluation data source mode (default: synthetic)",
    )
    parser.add_argument(
        "--output-json",
        default="ml/artifacts/root_cause_evaluation_report.json",
        help="Path to output machine-readable JSON evaluation report",
    )
    parser.add_argument(
        "--output-markdown",
        default="docs/PHASE_4_EVALUATION_REPORT.md",
        help="Path to output human-readable markdown evaluation report",
    )
    args = parser.parse_args()

    logger.info("Initializing Root-Cause Evaluation Pipeline [data-source=%s]...", args.data_source)

    if args.data_source == "synthetic":
        samples = generate_synthetic_benchmark_samples()
    else:
        samples = run_database_evaluation()

    logger.info("Collected %d incident evaluation sample(s).", len(samples))

    evaluator = RootCauseEvaluator()
    report = evaluator.evaluate_samples(samples)

    logger.info(
        "Evaluation Complete. Eligible: %d, Top-1 Accuracy: %.2f%%, Top-3 Accuracy: %.2f%%, MRR: %.4f",
        report.eligible_incidents,
        report.deterministic_ranker_metrics.top1_accuracy * 100,
        report.deterministic_ranker_metrics.top3_accuracy * 100,
        report.deterministic_ranker_metrics.mean_reciprocal_rank,
    )

    # Save machine-readable JSON
    json_path = Path(args.output_json)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    report_dict = {
        "generated_at": report.generated_at,
        "data_source": args.data_source,
        "total_incidents": report.total_incidents,
        "eligible_incidents": report.eligible_incidents,
        "excluded_incidents": report.excluded_incidents,
        "exclusion_reasons": report.exclusion_reasons,
        "deterministic_ranker_metrics": {
            "model_name": report.deterministic_ranker_metrics.model_name,
            "total_evaluated": report.deterministic_ranker_metrics.total_evaluated,
            "top1_accuracy": report.deterministic_ranker_metrics.top1_accuracy,
            "top3_accuracy": report.deterministic_ranker_metrics.top3_accuracy,
            "mean_reciprocal_rank": report.deterministic_ranker_metrics.mean_reciprocal_rank,
            "abstention_rate": report.deterministic_ranker_metrics.abstention_rate,
            "mean_diagnosis_latency_sec": report.deterministic_ranker_metrics.mean_diagnosis_latency_sec,
            "median_diagnosis_latency_sec": report.deterministic_ranker_metrics.median_diagnosis_latency_sec,
            "scenario_breakdown": report.deterministic_ranker_metrics.scenario_breakdown,
            "topology_coverage_breakdown": report.deterministic_ranker_metrics.topology_coverage_breakdown,
        },
        "earliest_anomaly_baseline_metrics": {
            "model_name": report.earliest_anomaly_baseline_metrics.model_name,
            "total_evaluated": report.earliest_anomaly_baseline_metrics.total_evaluated,
            "top1_accuracy": report.earliest_anomaly_baseline_metrics.top1_accuracy,
            "top3_accuracy": report.earliest_anomaly_baseline_metrics.top3_accuracy,
            "mean_reciprocal_rank": report.earliest_anomaly_baseline_metrics.mean_reciprocal_rank,
            "abstention_rate": report.earliest_anomaly_baseline_metrics.abstention_rate,
            "scenario_breakdown": report.earliest_anomaly_baseline_metrics.scenario_breakdown,
        },
        "detailed_incident_results": report.detailed_incident_results,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report_dict, f, indent=2)
    logger.info("Saved JSON report to %s", json_path)

    # Save markdown report
    md_content = render_markdown_report(report, args.data_source)
    md_path = Path(args.output_markdown)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    logger.info("Saved Markdown report to %s", md_path)


if __name__ == "__main__":
    main()
