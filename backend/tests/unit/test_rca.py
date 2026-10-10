"""Unit tests for incident anomaly correlation, deterministic root-cause ranking, and evaluation benchmark."""

from datetime import UTC, datetime, timedelta

from app.rca.correlator import AnomalyCorrelator
from app.rca.evaluation import IncidentEvaluationSample, RootCauseEvaluator
from app.rca.ranker import DeterministicRootCauseRanker
from app.rca.schemas import AnomalyEvent, CorrelatedIncident
from app.topology.models import TopologyGraph


def _event(
    svc: str,
    offset_sec: float,
    score: float = 0.85,
    severity: str = "HIGH",
    trace_id: str | None = None,
) -> AnomalyEvent:
    base = datetime(2026, 10, 10, 12, 0, 0, tzinfo=UTC)
    return AnomalyEvent(
        service_name=svc,
        timestamp=base + timedelta(seconds=offset_sec),
        detector_name="threshold",
        anomaly_score=score,
        severity=severity,  # type: ignore[arg-type]
        triggering_features=["error_rate"],
        explanation="Threshold breach detected",
        trace_id=trace_id,
    )


def test_initiating_service_ranked_above_downstream_symptoms() -> None:
    """Verify callee initiating service is ranked #1 above symptom caller."""
    # Topology: order-service calls payment-service
    topo = TopologyGraph()
    topo.add_edge("order-service", "payment-service")

    # payment-service fails first at t=0; order-service fails as symptom at t=5
    e_payment = _event("payment-service", 0.0, score=0.90, severity="CRITICAL", trace_id="tr-1")
    e_order = _event("order-service", 5.0, score=0.75, severity="HIGH", trace_id="tr-1")

    incident = CorrelatedIncident(
        incident_id="inc-1",
        start_time=e_payment.timestamp,
        end_time=e_order.timestamp,
        duration_seconds=5.0,
        affected_services=["payment-service", "order-service"],
        events=[e_payment, e_order],
    )

    trace_summary = {
        "payment-service": {"error_count": 5, "is_leaf_error": True},
        "order-service": {"error_count": 5, "is_leaf_error": False},
    }

    ranker = DeterministicRootCauseRanker()
    ranked = ranker.rank_incident(incident, topology=topo, trace_spans_summary=trace_summary)

    assert len(ranked) == 2
    assert ranked[0].service_name == "payment-service"
    assert ranked[0].rank == 1
    assert ranked[1].service_name == "order-service"
    assert ranked[1].rank == 2
    assert ranked[0].score > ranked[1].score

    # Check evidence
    evidence_payment = ranked[0].evidence
    assert evidence_payment.is_innermost_callee is True
    assert "order-service" in evidence_payment.upstream_callers


def test_downstream_service_failing_later_origin_dominates() -> None:
    """Verify that even if downstream service anomaly is recorded slightly later, leaf origin elevates it."""
    topo = TopologyGraph()
    topo.add_edge("checkout-service", "payment-service")

    # checkout front-facing alert logged at t=0, payment detected at t=2
    e_checkout = _event("checkout-service", 0.0, score=0.60, severity="MEDIUM")
    e_payment = _event("payment-service", 2.0, score=0.95, severity="CRITICAL")

    incident = CorrelatedIncident(
        incident_id="inc-2",
        start_time=e_checkout.timestamp,
        end_time=e_payment.timestamp,
        duration_seconds=2.0,
        affected_services=["checkout-service", "payment-service"],
        events=[e_checkout, e_payment],
    )

    trace_summary = {
        "payment-service": {"error_count": 8, "is_leaf_error": True},
    }

    ranker = DeterministicRootCauseRanker()
    ranked = ranker.rank_incident(incident, topology=topo, trace_spans_summary=trace_summary)

    # payment-service origin (1.0) and severity (CRITICAL) outscore checkout
    assert ranked[0].service_name == "payment-service"


def test_simultaneous_anomalies_deterministic_tie_breaking() -> None:
    """Verify deterministic ranking when anomaly onset and scores are identical."""
    topo = TopologyGraph()
    topo.add_service("alpha-service")
    topo.add_service("beta-service")

    e_alpha = _event("alpha-service", 0.0, score=0.80, severity="HIGH")
    e_beta = _event("beta-service", 0.0, score=0.80, severity="HIGH")

    incident = CorrelatedIncident(
        incident_id="inc-tie",
        start_time=e_alpha.timestamp,
        end_time=e_beta.timestamp,
        duration_seconds=0.0,
        affected_services=["beta-service", "alpha-service"],
        events=[e_beta, e_alpha],
    )

    ranker = DeterministicRootCauseRanker()
    ranked = ranker.rank_incident(incident, topology=topo)

    # Ties broken by lexicographical order: alpha-service precedes beta-service
    assert ranked[0].service_name == "alpha-service"
    assert ranked[1].service_name == "beta-service"


def test_unrelated_anomalies_close_in_time_split_by_topology() -> None:
    """Verify disjoint sub-graphs without shared traces are split into distinct incidents."""
    topo = TopologyGraph()
    # Cluster A: frontend -> checkout
    topo.add_edge("frontend", "checkout")
    # Cluster B: analytics -> warehouse (completely disconnected)
    topo.add_edge("analytics", "warehouse")

    ev1 = _event("frontend", 0.0)
    ev2 = _event("checkout", 2.0)
    ev3 = _event("analytics", 3.0)
    ev4 = _event("warehouse", 4.0)

    correlator = AnomalyCorrelator(default_window_seconds=60.0)
    clusters = correlator.correlate(events=[ev1, ev2, ev3, ev4], topology=topo)

    assert len(clusters) == 2
    # Cluster 1 has frontend & checkout, Cluster 2 has analytics & warehouse
    c1_svcs = set(clusters[0].affected_services)
    c2_svcs = set(clusters[1].affected_services)
    assert c1_svcs == {"frontend", "checkout"} or c1_svcs == {"analytics", "warehouse"}
    assert c2_svcs == {"frontend", "checkout"} or c2_svcs == {"analytics", "warehouse"}


def test_incomplete_topology_graceful_degradation() -> None:
    """Verify ranker functions when topology has no edges or is empty."""
    e1 = _event("service-a", 0.0, score=0.90, severity="CRITICAL")
    e2 = _event("service-b", 10.0, score=0.40, severity="LOW")

    incident = CorrelatedIncident(
        incident_id="inc-empty-topo",
        start_time=e1.timestamp,
        end_time=e2.timestamp,
        duration_seconds=10.0,
        affected_services=["service-a", "service-b"],
        events=[e1, e2],
    )

    ranker = DeterministicRootCauseRanker()
    ranked = ranker.rank_incident(incident, topology=None)

    assert len(ranked) == 2
    assert ranked[0].service_name == "service-a"
    assert "No active topology snapshot available" in ranked[0].evidence.uncertainty_reasons[0]


def test_abstention_on_insufficient_evidence() -> None:
    """Verify abstention when candidate list is empty or scores are below threshold."""
    ranker = DeterministicRootCauseRanker()
    empty_incident = CorrelatedIncident(
        incident_id="inc-empty",
        start_time=datetime(2026, 10, 10, 12, 0, 0, tzinfo=UTC),
        end_time=datetime(2026, 10, 10, 12, 0, 0, tzinfo=UTC),
        duration_seconds=0.0,
        affected_services=[],
        events=[],
    )

    ranked = ranker.rank_incident(empty_incident)
    assert ranked == []


def test_ground_truth_isolation() -> None:
    """Verify candidate evidence and schemas contain no leakage of ground-truth labels."""
    e1 = _event("service-x", 0.0)
    incident = CorrelatedIncident(
        incident_id="inc-leak-test",
        start_time=e1.timestamp,
        end_time=e1.timestamp,
        duration_seconds=0.0,
        affected_services=["service-x"],
        events=[e1],
    )

    ranker = DeterministicRootCauseRanker()
    ranked = ranker.rank_incident(incident)

    c = ranked[0]
    ev_dict = c.evidence.model_dump()

    # Ground truth forbidden keys
    forbidden = [
        "ground_truth",
        "initiating_service",
        "target_service",
        "fault_type",
        "scenario_name",
    ]
    for k in ev_dict:
        for f in forbidden:
            assert f not in k.lower(), (
                f"Forbidden ground truth key '{f}' leaked in evidence key '{k}'"
            )


def test_evaluation_metrics_top1_top3_mrr_calculation() -> None:
    """Verify evaluator computes Top-1, Top-3, and MRR accurately."""
    topo = TopologyGraph()
    topo.add_edge("caller", "callee")

    # Sample 1: callee is ground truth, ranked #1 -> rank 1 (RR = 1.0)
    e1 = _event("callee", 0.0, score=0.90)
    e2 = _event("caller", 5.0, score=0.50)
    sample1 = IncidentEvaluationSample(
        incident_id="inc-1",
        scenario_name="latency_spike",
        ground_truth_initiating_service="callee",
        symptom_services=["caller"],
        anomaly_events=[e1, e2],
        topology=topo,
        trace_spans_summary={"callee": {"is_leaf_error": True, "error_count": 2}},
    )

    evaluator = RootCauseEvaluator()
    report = evaluator.evaluate_samples([sample1])

    assert report.total_incidents == 1
    assert report.eligible_incidents == 1
    assert report.deterministic_ranker_metrics.top1_accuracy == 1.0
    assert report.deterministic_ranker_metrics.top3_accuracy == 1.0
    assert report.deterministic_ranker_metrics.mean_reciprocal_rank == 1.0


def test_evaluation_empty_and_ambiguous_incidents_excluded() -> None:
    """Verify incidents missing ground-truth or with empty anomalies are excluded with recorded reasons."""
    sample_bad = IncidentEvaluationSample(
        incident_id="inc-bad",
        scenario_name="unknown",
        ground_truth_initiating_service="",  # Missing ground truth!
        symptom_services=[],
        anomaly_events=[],
        topology=TopologyGraph(),
    )

    evaluator = RootCauseEvaluator()
    report = evaluator.evaluate_samples([sample_bad])

    assert report.total_incidents == 1
    assert report.eligible_incidents == 0
    assert report.excluded_incidents == 1
    assert report.exclusion_reasons["missing_ground_truth"] == 1
