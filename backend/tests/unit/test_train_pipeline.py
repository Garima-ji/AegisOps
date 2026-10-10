"""Unit tests for train_models.py pipeline, database mode queries, data readiness, and safeguards."""

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import joblib
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models.experiment import ExperimentModel
from app.db.models.telemetry import LogRecordModel, MetricRecordModel, TraceSpanModel
from scripts.train_models import (
    DatabaseTrainingError,
    check_production_artifact_safeguard,
    extract_telemetry_from_db,
    run_training_pipeline,
)


@pytest.fixture
def test_db_session_factory():
    """Create in-memory SQLite database engine and session factory with schema initialized."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    yield factory
    engine.dispose()


def test_db_mode_fails_when_db_unavailable() -> None:
    """Verify DatabaseTrainingError is raised with actionable message when database connection fails."""
    with patch(
        "scripts.train_models.check_database_connection",
        return_value=(False, "Connection refused", {"dialect": "postgresql"}),
    ):
        with pytest.raises(DatabaseTrainingError, match="Database connectivity failed"):
            extract_telemetry_from_db()


def test_db_mode_fails_when_telemetry_insufficient(test_db_session_factory) -> None:
    """Verify DatabaseTrainingError is raised with actionable diagnostic when telemetry records are below threshold."""
    with patch(
        "scripts.train_models.check_database_connection",
        return_value=(True, "OK", {"dialect": "sqlite"}),
    ):
        with pytest.raises(DatabaseTrainingError, match="Insufficient database telemetry"):
            extract_telemetry_from_db(
                start_time=datetime(2026, 10, 10, 0, 0, 0, tzinfo=UTC),
                end_time=datetime(2026, 10, 10, 1, 0, 0, tzinfo=UTC),
                session_factory=test_db_session_factory,
            )


def test_db_mode_queries_telemetry_tables(test_db_session_factory) -> None:
    """Verify that extract_telemetry_from_db executes bounded queries across metrics, logs, spans, and experiments."""
    t0 = datetime(2026, 10, 10, 10, 0, 0, tzinfo=UTC)

    # Seed test database with sufficient telemetry points
    with test_db_session_factory() as session:
        # 60 Metric records
        for i in range(60):
            session.add(
                MetricRecordModel(
                    timestamp=t0 + timedelta(seconds=i * 2),
                    service_name="payment-service",
                    environment="production",
                    metric_name="http.server.request.duration_ms",
                    value=150.0 + i,
                    unit="ms",
                )
            )

        # 15 Trace spans
        for i in range(15):
            session.add(
                TraceSpanModel(
                    trace_id=f"trace-{i:04d}",
                    span_id=f"span-{i:04d}",
                    service_name="payment-service",
                    operation_name="/api/pay",
                    start_time=t0 + timedelta(seconds=i * 2),
                    end_time=t0 + timedelta(seconds=i * 2 + 1),
                    duration_ms=100.0,
                    status_code="OK",
                )
            )

        # 10 Log records
        for i in range(10):
            session.add(
                LogRecordModel(
                    timestamp=t0 + timedelta(seconds=i * 2),
                    service_name="payment-service",
                    environment="production",
                    severity="INFO",
                    message="Processed payment transaction",
                )
            )

        # 1 Experiment
        session.add(
            ExperimentModel(
                id="exp-test-001",
                scenario_name="latency_spike",
                target_service="payment-service",
                fault_type="latency_spike",
                start_time=t0 + timedelta(seconds=40),
                end_time=t0 + timedelta(seconds=80),
                status="COMPLETED",
                initiating_service="payment-service",
            )
        )
        session.commit()

    with patch(
        "scripts.train_models.check_database_connection",
        return_value=(True, "OK", {"dialect": "sqlite"}),
    ):
        metrics, logs, spans, faults, meta = extract_telemetry_from_db(
            start_time=t0,
            end_time=t0 + timedelta(seconds=200),
            session_factory=test_db_session_factory,
        )

    assert len(metrics) == 60
    assert len(spans) == 15
    assert len(logs) == 10
    assert len(faults) == 1
    assert faults[0]["experiment_id"] == "exp-test-001"
    assert faults[0]["scenario"] == "latency_spike"
    assert meta["dataset_origin"] == "postgresql_telemetry_lake"
    assert meta["record_counts"]["metrics"] == 60


class _MockProductionModel:
    def __init__(self) -> None:
        self.metadata = {"status": "PRODUCTION", "model_version": "v1.0.0"}


def test_production_artifact_safeguard() -> None:
    """Verify check_production_artifact_safeguard prevents overwriting production-certified artifacts without flag."""
    with tempfile.TemporaryDirectory() as tmpdir:
        artifacts_path = Path(tmpdir)
        dummy_model_path = artifacts_path / "isolation_forest.joblib"

        joblib.dump(_MockProductionModel(), dummy_model_path)

        # Without force_overwrite -> must raise DatabaseTrainingError
        with pytest.raises(DatabaseTrainingError, match="Production Safeguard Violation"):
            check_production_artifact_safeguard(artifacts_path, force_overwrite=False)

        # With force_overwrite -> succeeds
        check_production_artifact_safeguard(artifacts_path, force_overwrite=True)


def test_synthetic_training_run_completes() -> None:
    """Verify explicit --data-source synthetic training execution succeeds."""
    exit_code = run_training_pipeline(data_source="synthetic")
    assert exit_code == 0
