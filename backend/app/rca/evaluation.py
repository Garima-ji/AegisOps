"""Root-cause analysis evaluation benchmark framework."""

import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.rca.correlator import AnomalyCorrelator
from app.rca.ranker import DeterministicRootCauseRanker
from app.rca.schemas import AnomalyEvent, CorrelatedIncident, RankedCandidate
from app.topology.models import TopologyGraph


@dataclass
class IncidentEvaluationSample:
    """Incident benchmark sample containing observed telemetry events and isolated ground truth."""

    incident_id: str
    scenario_name: str
    ground_truth_initiating_service: str
    symptom_services: list[str]
    anomaly_events: list[AnomalyEvent]
    topology: TopologyGraph
    trace_spans_summary: dict[str, dict[str, Any]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelEvaluationMetrics:
    """Evaluation metrics for a ranking algorithm."""

    model_name: str
    total_evaluated: int
    top1_accuracy: float
    top3_accuracy: float
    mean_reciprocal_rank: float
    abstention_rate: float
    mean_diagnosis_latency_sec: float | None
    median_diagnosis_latency_sec: float | None
    scenario_breakdown: dict[str, dict[str, float]]
    topology_coverage_breakdown: dict[str, dict[str, float]]


@dataclass
class RootCauseBenchmarkReport:
    """Complete evaluation report comparing the deterministic engine against baselines."""

    total_incidents: int
    eligible_incidents: int
    excluded_incidents: int
    exclusion_reasons: dict[str, int]
    deterministic_ranker_metrics: ModelEvaluationMetrics
    earliest_anomaly_baseline_metrics: ModelEvaluationMetrics
    detailed_incident_results: list[dict[str, Any]]
    generated_at: str


class EarliestAnomalyRanker:
    """Simple baseline that ranks candidate root causes purely by earliest anomaly onset timestamp."""

    def rank_incident(self, incident: CorrelatedIncident) -> list[RankedCandidate]:
        """Rank candidates solely by onset time ascending."""
        if not incident.affected_services or not incident.events:
            return []

        onsets: dict[str, datetime] = {}
        for ev in incident.events:
            if ev.timestamp is not None:
                if ev.service_name not in onsets or ev.timestamp < onsets[ev.service_name]:
                    onsets[ev.service_name] = ev.timestamp

        sorted_services = sorted(
            incident.affected_services,
            key=lambda s: (onsets.get(s, datetime.max), s),
        )

        candidates: list[RankedCandidate] = []
        for rank_idx, svc in enumerate(sorted_services, start=1):
            onset = onsets.get(svc)
            # Naive score decaying linearly with rank
            score = max(0.0, 1.0 - (rank_idx - 1) * 0.2)
            candidates.append(
                RankedCandidate(
                    service_name=svc,
                    rank=rank_idx,
                    score=round(score, 4),
                    score_breakdown={"temporal_rank_score": score},
                    evidence={  # type: ignore[arg-type]
                        "candidate_service": svc,
                        "anomaly_onset": onset,
                        "relative_delay_sec": None,
                        "is_innermost_callee": False,
                        "failing_span_count": 0,
                        "downstream_symptoms": [],
                        "upstream_callers": [],
                        "anomaly_severity": "UNKNOWN",
                        "mean_anomaly_score": 0.5,
                        "detector_count": 1,
                        "positive_indicators": ["Earliest anomaly arrival rank"],
                        "contradictory_indicators": [],
                        "uncertainty_reasons": ["Baseline lacks topology and trace awareness"],
                    },
                    explanation=f"Naive temporal rank #{rank_idx} based solely on arrival time",
                )
            )
        return candidates


class RootCauseEvaluator:
    """Evaluates root-cause ranking algorithms against valid fault injection ground truth."""

    def __init__(self) -> None:
        self.deterministic_ranker = DeterministicRootCauseRanker()
        self.earliest_baseline = EarliestAnomalyRanker()
        self.correlator = AnomalyCorrelator()

    def evaluate_samples(
        self,
        samples: Sequence[IncidentEvaluationSample],
    ) -> RootCauseBenchmarkReport:
        """Run comprehensive benchmark across incident samples with ground-truth isolation."""
        eligible_samples: list[IncidentEvaluationSample] = []
        exclusion_reasons: dict[str, int] = {}

        for sample in samples:
            if not sample.ground_truth_initiating_service:
                exclusion_reasons["missing_ground_truth"] = (
                    exclusion_reasons.get("missing_ground_truth", 0) + 1
                )
                continue
            if not sample.anomaly_events:
                exclusion_reasons["no_anomaly_events"] = (
                    exclusion_reasons.get("no_anomaly_events", 0) + 1
                )
                continue
            eligible_samples.append(sample)

        total_incidents = len(samples)
        eligible_count = len(eligible_samples)
        excluded_count = total_incidents - eligible_count

        if eligible_count == 0:
            empty_metrics = ModelEvaluationMetrics(
                model_name="N/A",
                total_evaluated=0,
                top1_accuracy=0.0,
                top3_accuracy=0.0,
                mean_reciprocal_rank=0.0,
                abstention_rate=0.0,
                mean_diagnosis_latency_sec=None,
                median_diagnosis_latency_sec=None,
                scenario_breakdown={},
                topology_coverage_breakdown={},
            )
            return RootCauseBenchmarkReport(
                total_incidents=total_incidents,
                eligible_incidents=0,
                excluded_incidents=excluded_count,
                exclusion_reasons=exclusion_reasons,
                deterministic_ranker_metrics=empty_metrics,
                earliest_anomaly_baseline_metrics=empty_metrics,
                detailed_incident_results=[],
                generated_at=datetime.now().isoformat(),
            )

        det_results: list[dict[str, Any]] = []
        baseline_results: list[dict[str, Any]] = []

        for sample in eligible_samples:
            # Correlate anomalies
            clusters = self.correlator.correlate(
                events=sample.anomaly_events,
                topology=sample.topology,
            )
            target_cluster = (
                clusters[0]
                if clusters
                else CorrelatedIncident(
                    incident_id=sample.incident_id,
                    start_time=sample.anomaly_events[0].timestamp,
                    end_time=sample.anomaly_events[-1].timestamp,
                    duration_seconds=0.0,
                    affected_services=list({e.service_name for e in sample.anomaly_events}),
                    events=sample.anomaly_events,
                )
            )

            # 1. Deterministic ranker
            det_candidates = self.deterministic_ranker.rank_incident(
                incident=target_cluster,
                topology=sample.topology,
                trace_spans_summary=sample.trace_spans_summary,
            )

            det_outcome = self._score_ranking(
                sample=sample,
                candidates=det_candidates,
                model_name="deterministic_ranker",
            )
            det_results.append(det_outcome)

            # 2. Baseline ranker
            base_candidates = self.earliest_baseline.rank_incident(incident=target_cluster)
            base_outcome = self._score_ranking(
                sample=sample,
                candidates=base_candidates,
                model_name="earliest_anomaly_baseline",
            )
            baseline_results.append(base_outcome)

        det_metrics = self._aggregate_metrics("Deterministic Multi-Factor Ranker", det_results)
        base_metrics = self._aggregate_metrics("Earliest Anomaly Baseline", baseline_results)

        # Merge detailed per-incident results for inspection
        detailed: list[dict[str, Any]] = []
        for d, b in zip(det_results, baseline_results, strict=True):
            detailed.append(
                {
                    "incident_id": d["incident_id"],
                    "scenario": d["scenario"],
                    "ground_truth_service": d["ground_truth_service"],
                    "in_topology": d["in_topology"],
                    "deterministic_top1": d["top_predicted"],
                    "deterministic_rank": d["ground_truth_rank"],
                    "deterministic_hit_top1": d["hit_top1"],
                    "deterministic_hit_top3": d["hit_top3"],
                    "baseline_top1": b["top_predicted"],
                    "baseline_rank": b["ground_truth_rank"],
                    "baseline_hit_top1": b["hit_top1"],
                }
            )

        return RootCauseBenchmarkReport(
            total_incidents=total_incidents,
            eligible_incidents=eligible_count,
            excluded_incidents=excluded_count,
            exclusion_reasons=exclusion_reasons,
            deterministic_ranker_metrics=det_metrics,
            earliest_anomaly_baseline_metrics=base_metrics,
            detailed_incident_results=detailed,
            generated_at=datetime.now().isoformat(),
        )

    def _score_ranking(
        self,
        sample: IncidentEvaluationSample,
        candidates: list[RankedCandidate],
        model_name: str,
    ) -> dict[str, Any]:
        """Score candidate list against ground truth for one incident."""
        gt_service = sample.ground_truth_initiating_service
        in_topology = bool(sample.topology and gt_service in sample.topology.nodes)

        top_predicted = candidates[0].service_name if candidates else None
        top_score = candidates[0].score if candidates else 0.0

        # Check abstention
        abstained = (
            not candidates or top_score < self.deterministic_ranker.ABSTENTION_THRESHOLD
            if model_name == "deterministic_ranker"
            else not candidates
        )

        gt_rank: int | None = None
        for cand in candidates:
            if cand.service_name == gt_service:
                gt_rank = cand.rank
                break

        hit_top1 = bool(gt_rank == 1 and not abstained)
        hit_top3 = bool(gt_rank is not None and gt_rank <= 3 and not abstained)
        reciprocal_rank = 1.0 / gt_rank if (gt_rank is not None and not abstained) else 0.0

        # Diagnosis latency: earliest anomaly arrival in incident vs candidate onset
        latency_sec: float | None = None
        if candidates and candidates[0].evidence.anomaly_onset and sample.anomaly_events:
            earliest_ev = min(e.timestamp for e in sample.anomaly_events)
            latency_sec = max(
                0.0, (candidates[0].evidence.anomaly_onset - earliest_ev).total_seconds()
            )

        return {
            "incident_id": sample.incident_id,
            "scenario": sample.scenario_name,
            "ground_truth_service": gt_service,
            "in_topology": in_topology,
            "top_predicted": top_predicted,
            "top_score": top_score,
            "ground_truth_rank": gt_rank,
            "hit_top1": hit_top1,
            "hit_top3": hit_top3,
            "reciprocal_rank": reciprocal_rank,
            "abstained": abstained,
            "latency_sec": latency_sec,
        }

    def _aggregate_metrics(
        self,
        model_name: str,
        results: list[dict[str, Any]],
    ) -> ModelEvaluationMetrics:
        """Aggregate per-incident scores into summary benchmark metrics."""
        n = len(results)
        if n == 0:
            return ModelEvaluationMetrics(
                model_name=model_name,
                total_evaluated=0,
                top1_accuracy=0.0,
                top3_accuracy=0.0,
                mean_reciprocal_rank=0.0,
                abstention_rate=0.0,
                mean_diagnosis_latency_sec=None,
                median_diagnosis_latency_sec=None,
                scenario_breakdown={},
                topology_coverage_breakdown={},
            )

        top1_acc = sum(1 for r in results if r["hit_top1"]) / n
        top3_acc = sum(1 for r in results if r["hit_top3"]) / n
        mrr = sum(r["reciprocal_rank"] for r in results) / n
        abstentions = sum(1 for r in results if r["abstained"]) / n

        latencies = [r["latency_sec"] for r in results if r["latency_sec"] is not None]
        mean_lat = statistics.mean(latencies) if latencies else None
        med_lat = statistics.median(latencies) if latencies else None

        # Scenario breakdown
        scenarios: dict[str, list[dict[str, Any]]] = {}
        for r in results:
            scenarios.setdefault(r["scenario"], []).append(r)

        scenario_breakdown: dict[str, dict[str, float]] = {}
        for sc_name, sc_items in scenarios.items():
            sc_n = len(sc_items)
            scenario_breakdown[sc_name] = {
                "count": float(sc_n),
                "top1_accuracy": round(sum(1 for r in sc_items if r["hit_top1"]) / sc_n, 4),
                "top3_accuracy": round(sum(1 for r in sc_items if r["hit_top3"]) / sc_n, 4),
                "mrr": round(sum(r["reciprocal_rank"] for r in sc_items) / sc_n, 4),
            }

        # Topology coverage breakdown (in topology vs missing from topology)
        topo_groups: dict[str, list[dict[str, Any]]] = {
            "in_topology": [r for r in results if r["in_topology"]],
            "missing_from_topology": [r for r in results if not r["in_topology"]],
        }
        topo_breakdown: dict[str, dict[str, float]] = {}
        for tg_name, tg_items in topo_groups.items():
            tg_n = len(tg_items)
            if tg_n > 0:
                topo_breakdown[tg_name] = {
                    "count": float(tg_n),
                    "top1_accuracy": round(sum(1 for r in tg_items if r["hit_top1"]) / tg_n, 4),
                    "top3_accuracy": round(sum(1 for r in tg_items if r["hit_top3"]) / tg_n, 4),
                    "mrr": round(sum(r["reciprocal_rank"] for r in tg_items) / tg_n, 4),
                }

        return ModelEvaluationMetrics(
            model_name=model_name,
            total_evaluated=n,
            top1_accuracy=round(top1_acc, 4),
            top3_accuracy=round(top3_acc, 4),
            mean_reciprocal_rank=round(mrr, 4),
            abstention_rate=round(abstentions, 4),
            mean_diagnosis_latency_sec=round(mean_lat, 2) if mean_lat is not None else None,
            median_diagnosis_latency_sec=round(med_lat, 2) if med_lat is not None else None,
            scenario_breakdown=scenario_breakdown,
            topology_coverage_breakdown=topo_breakdown,
        )
