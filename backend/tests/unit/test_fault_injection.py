"""Unit tests for controlled fault injection framework and safety controls."""

import pytest

from app.core.exceptions import ValidationError
from app.ingestion.fault_injection import (
    SAFETY_ALLOWLISTED_SERVICES,
    SCENARIO_CATALOG,
    FaultInjectionEngine,
)
from app.ingestion.generator import DeterministicTelemetryGenerator


def test_safety_boundary_rejects_arbitrary_services() -> None:
    """Fault injection must strictly reject services not in safety allowlist."""
    engine = FaultInjectionEngine()
    with pytest.raises(ValidationError) as exc:
        engine.activate_fault(
            experiment_id="exp_test_01",
            fault_type="latency_spike",
            target_service="external-payment-gateway.com",
            duration_seconds=30,
        )
    assert "prohibited" in str(exc.value)


def test_safety_boundary_duration_limits() -> None:
    """Experiment duration must remain strictly bounded between 5s and 300s."""
    engine = FaultInjectionEngine()
    # Below 5s
    with pytest.raises(ValidationError):
        engine.activate_fault(
            experiment_id="exp_test_02",
            fault_type="latency_spike",
            target_service="payment-service",
            duration_seconds=3,
        )
    # Above 300s
    with pytest.raises(ValidationError):
        engine.activate_fault(
            experiment_id="exp_test_03",
            fault_type="latency_spike",
            target_service="payment-service",
            duration_seconds=500,
        )


def test_concurrent_conflict_prevention_on_same_service() -> None:
    """Engine must prevent two active experiments targeting the same service simultaneously."""
    engine = FaultInjectionEngine()
    engine.activate_fault(
        experiment_id="exp_first",
        fault_type="latency_spike",
        target_service="payment-service",
        duration_seconds=60,
    )

    with pytest.raises(ValidationError) as exc:
        engine.activate_fault(
            experiment_id="exp_second",
            fault_type="error_surge",
            target_service="payment-service",
            duration_seconds=60,
        )
    assert "already has an active fault" in str(exc.value)


def test_fault_deactivation_and_cleanup() -> None:
    """Deactivating a fault must mark it inactive and record cleanup verification."""
    engine = FaultInjectionEngine()
    state = engine.activate_fault(
        experiment_id="exp_clean",
        fault_type="latency_spike",
        target_service="order-service",
        duration_seconds=30,
    )
    assert state.is_active is True
    assert state.cleanup_verified is False

    cleaned = engine.deactivate_fault("exp_clean")
    assert cleaned.is_active is False
    assert cleaned.cleanup_verified is True
    assert engine.get_active_fault_for_service("order-service") is None


def test_scenario_catalog_integrity() -> None:
    """All 3 required fault scenarios must be defined with observable signals and allowlisted services."""
    assert "latency_spike" in SCENARIO_CATALOG
    assert "error_surge" in SCENARIO_CATALOG
    assert "service_timeout" in SCENARIO_CATALOG

    for scen in SCENARIO_CATALOG.values():
        assert scen.target_service in SAFETY_ALLOWLISTED_SERVICES
        assert len(scen.expected_observable_signals) > 0
        assert scen.initiating_service in SAFETY_ALLOWLISTED_SERVICES


def test_deterministic_generator_reproducibility() -> None:
    """Generator with the same seed must produce identical telemetry signals."""
    gen1 = DeterministicTelemetryGenerator(seed=12345)
    batch1 = gen1.generate_normal_traffic(count=5)

    gen2 = DeterministicTelemetryGenerator(seed=12345)
    batch2 = gen2.generate_normal_traffic(count=5)

    assert len(batch1.metrics) == len(batch2.metrics)
    assert len(batch1.spans) == len(batch2.spans)
    assert len(batch1.logs) == len(batch2.logs)

    # Verify identical values
    for m1, m2 in zip(batch1.metrics, batch2.metrics, strict=True):
        assert m1.value == m2.value
        assert m1.metric_name == m2.metric_name


def test_fault_traffic_generation_symptoms() -> None:
    """Generating fault traffic must produce measurable errors and latency differences."""
    gen = DeterministicTelemetryGenerator(seed=999)

    normal_batch = gen.generate_normal_traffic(count=5)
    fault_batch = gen.generate_fault_traffic(
        fault_type="latency_spike",
        experiment_id="exp_test_fault",
        count=5,
        custom_params={"added_latency_ms": 800.0},
    )

    avg_normal_order_duration = sum(t.order_duration_ms for t in normal_batch.transactions) / 5
    avg_fault_order_duration = sum(t.order_duration_ms for t in fault_batch.transactions) / 5

    # Fault batch should be substantially slower (> 700ms slower)
    assert avg_fault_order_duration > avg_normal_order_duration + 500.0
