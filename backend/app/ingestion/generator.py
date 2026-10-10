"""Deterministic telemetry workload generator for local development and CI testing."""

import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy.orm import Session

from app.ingestion.fault_injection import SCENARIO_CATALOG, ActiveFaultState
from app.ingestion.sample_app import InstrumentedSampleApp, TransactionResult
from app.schemas.telemetry import (
    IngestionResponse,
    LogRecordSchema,
    MetricPoint,
    SpanRecordSchema,
)
from app.services.telemetry_service import TelemetryService


@dataclass
class TelemetryBatch:
    """Aggregated batch containing all three operational signals generated from workloads."""

    metrics: list[MetricPoint]
    logs: list[LogRecordSchema]
    traces: list[SpanRecordSchema]
    transactions: list[TransactionResult]
    source: str = "sample_app"

    @property
    def total_records(self) -> int:
        return len(self.metrics) + len(self.logs) + len(self.traces)

    @property
    def spans(self) -> list[SpanRecordSchema]:
        """Alias for traces."""
        return self.traces


class DeterministicTelemetryGenerator:
    """Generator providing reproducible telemetry batches for development and test suites."""

    def __init__(
        self,
        environment: str = "production",
        seed: int = 42,
    ) -> None:
        self.environment = environment
        self.seed = seed
        self.sample_app = InstrumentedSampleApp(environment=environment, seed=seed)
        self.rng = random.Random(seed)

    def generate_normal_traffic(
        self,
        count: int = 10,
        start_time: datetime | None = None,
        interval_seconds: float = 1.0,
    ) -> TelemetryBatch:
        """Generate N normal inter-service order transactions with standard variance."""
        if start_time is None:
            # Set to slightly in the past so all points are strictly non-future
            start_time = datetime.now(UTC) - timedelta(seconds=count * interval_seconds + 5)

        all_metrics: list[MetricPoint] = []
        all_logs: list[LogRecordSchema] = []
        all_traces: list[SpanRecordSchema] = []
        transactions: list[TransactionResult] = []

        cur_time = start_time
        for _ in range(count):
            res = self.sample_app.execute_order_transaction(timestamp=cur_time)
            transactions.append(res)
            all_metrics.extend(res.metrics)
            all_logs.extend(res.logs)
            all_traces.extend(res.spans)
            cur_time += timedelta(seconds=interval_seconds)

        return TelemetryBatch(
            metrics=all_metrics,
            logs=all_logs,
            traces=all_traces,
            transactions=transactions,
            source="sample_app_normal",
        )

    def generate_fault_traffic(
        self,
        fault_type: Literal["latency_spike", "error_surge", "service_timeout"],
        experiment_id: str,
        count: int = 10,
        start_time: datetime | None = None,
        interval_seconds: float = 1.0,
        custom_params: dict[str, object] | None = None,
    ) -> TelemetryBatch:
        """Generate N transactions under a controlled active fault condition."""
        if start_time is None:
            start_time = datetime.now(UTC) - timedelta(seconds=count * interval_seconds + 5)

        catalog_entry = SCENARIO_CATALOG.get(fault_type)
        params: dict[str, object] = dict(catalog_entry.default_parameters if catalog_entry else {})
        if custom_params:
            params.update(custom_params)

        forced_fault = ActiveFaultState(
            experiment_id=experiment_id,
            fault_type=fault_type,
            target_service="payment-service",
            initiating_service="payment-service",
            symptom_services=["order-service"],
            parameters=params,
            start_time=start_time,
            end_time=start_time + timedelta(seconds=count * interval_seconds + 60),
            is_active=True,
        )

        all_metrics: list[MetricPoint] = []
        all_logs: list[LogRecordSchema] = []
        all_traces: list[SpanRecordSchema] = []
        transactions: list[TransactionResult] = []

        cur_time = start_time
        for _ in range(count):
            res = self.sample_app.execute_order_transaction(
                timestamp=cur_time, forced_fault=forced_fault
            )
            transactions.append(res)
            all_metrics.extend(res.metrics)
            all_logs.extend(res.logs)
            all_traces.extend(res.spans)
            cur_time += timedelta(seconds=interval_seconds)

        return TelemetryBatch(
            metrics=all_metrics,
            logs=all_logs,
            traces=all_traces,
            transactions=transactions,
            source=f"sample_app_fault_{fault_type}",
        )

    @staticmethod
    def ingest_batch_to_database(
        session: Session, batch: TelemetryBatch
    ) -> dict[str, IngestionResponse]:
        """Convenience method to ingest an entire generated batch into PostgreSQL."""
        metrics_resp = TelemetryService.ingest_metrics(session=session, items=batch.metrics)
        logs_resp = TelemetryService.ingest_logs(session=session, items=batch.logs)
        traces_resp = TelemetryService.ingest_traces(session=session, items=batch.traces)
        return {
            "metrics": metrics_resp,
            "logs": logs_resp,
            "traces": traces_resp,
        }
