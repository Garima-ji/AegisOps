"""Instrumented sample microservices with cooperating HTTP workflows.

Simulates order-service and payment-service inter-service communication with:
- Distributed trace propagation (Trace ID, Span ID, Parent Span ID)
- Service-level operational metrics (durations, error counts, request rates)
- Structured logs with contextual correlation
- Measurable fault behaviors (latency spikes, HTTP 500 error surges, timeout aborts)
"""

import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from app.ingestion.fault_injection import ActiveFaultState, fault_engine
from app.schemas.telemetry import LogRecordSchema, MetricPoint, SpanRecordSchema


def _generate_hex_id(length: int, rng: random.Random) -> str:
    """Generate deterministic hexadecimal string of given length."""
    return "".join(rng.choices("0123456789abcdef", k=length))


@dataclass
class TransactionResult:
    """Result of an inter-service transaction including operational telemetry."""

    trace_id: str
    order_id: str
    status: str
    order_duration_ms: float
    payment_duration_ms: float
    metrics: list[MetricPoint]
    logs: list[LogRecordSchema]
    spans: list[SpanRecordSchema]
    is_fault: bool
    fault_type: str | None


class InstrumentedSampleApp:
    """Simulated cooperating microservices: order-service -> payment-service."""

    def __init__(self, environment: str = "production", seed: int = 42) -> None:
        self.environment = environment
        self.rng = random.Random(seed)

    def execute_order_transaction(
        self,
        timestamp: datetime | None = None,
        forced_fault: ActiveFaultState | None = None,
    ) -> TransactionResult:
        """Execute a complete order processing flow across order-service and payment-service."""
        if timestamp is None:
            timestamp = datetime.now(UTC)

        trace_id = _generate_hex_id(32, self.rng)
        order_span_id = _generate_hex_id(16, self.rng)
        payment_span_id = _generate_hex_id(16, self.rng)
        db_span_id = _generate_hex_id(16, self.rng)
        order_id = f"ord_{self.rng.randint(100000, 999999)}"

        # Check for active fault on payment-service
        active_fault = forced_fault or fault_engine.get_active_fault_for_service("payment-service")

        # Baseline latencies
        base_payment_latency = self.rng.uniform(25.0, 55.0)
        base_order_overhead = self.rng.uniform(15.0, 30.0)

        # Fault behaviors
        is_fault = active_fault is not None
        fault_type = active_fault.fault_type if active_fault else None

        payment_error = False
        timeout_error = False
        added_latency = 0.0

        if active_fault:
            if active_fault.fault_type == "latency_spike":
                added_latency = float(active_fault.parameters.get("added_latency_ms", 750.0))
                jitter = float(active_fault.parameters.get("jitter_ms", 30.0))
                added_latency += self.rng.uniform(-jitter, jitter)
            elif active_fault.fault_type == "error_surge":
                error_rate = float(active_fault.parameters.get("error_rate", 0.85))
                if self.rng.random() < error_rate:
                    payment_error = True
            elif active_fault.fault_type == "service_timeout":
                timeout_error = True
                added_latency = float(active_fault.parameters.get("timeout_ms", 2000.0))

        actual_payment_latency = max(5.0, round(base_payment_latency + added_latency, 2))
        actual_order_latency = max(
            actual_payment_latency + 10.0,
            round(actual_payment_latency + base_order_overhead, 2),
        )

        order_start = timestamp
        order_end = order_start + timedelta(milliseconds=actual_order_latency)
        payment_start = order_start + timedelta(milliseconds=base_order_overhead * 0.4)
        payment_end = payment_start + timedelta(milliseconds=actual_payment_latency)
        db_start = payment_start + timedelta(milliseconds=5.0)
        db_end = db_start + timedelta(milliseconds=max(2.0, actual_payment_latency * 0.3))

        metrics: list[MetricPoint] = []
        logs: list[LogRecordSchema] = []
        spans: list[SpanRecordSchema] = []

        # ---------------------------------------------------------------------
        # Spans Construction
        # ---------------------------------------------------------------------
        # Payment DB Span (Grandchild)
        db_status: Literal["OK", "ERROR", "UNSET"] = (
            "ERROR" if (payment_error or timeout_error) else "OK"
        )
        spans.append(
            SpanRecordSchema(
                trace_id=trace_id,
                span_id=db_span_id,
                parent_span_id=payment_span_id,
                service_name="payment-service",
                operation_name="db.query.verify_account",
                start_time=db_start,
                end_time=db_end,
                duration_ms=round((db_end - db_start).total_seconds() * 1000, 2),
                status_code=db_status,
                status_message="Database pool exhaustion error" if payment_error else None,
                attributes={"db.system": "postgresql", "db.name": "payments_db"},
            )
        )

        # Payment Service Span (Child)
        payment_status: Literal["OK", "ERROR", "UNSET"] = (
            "ERROR" if (payment_error or timeout_error) else "OK"
        )
        spans.append(
            SpanRecordSchema(
                trace_id=trace_id,
                span_id=payment_span_id,
                parent_span_id=order_span_id,
                service_name="payment-service",
                operation_name="POST /payments",
                start_time=payment_start,
                end_time=payment_end,
                duration_ms=actual_payment_latency,
                status_code=payment_status,
                status_message=(
                    "Downstream client timeout"
                    if timeout_error
                    else ("HTTP 500 Internal Server Error" if payment_error else None)
                ),
                attributes={
                    "http.method": "POST",
                    "http.status_code": 500 if payment_error else (504 if timeout_error else 200),
                    "experiment_id": active_fault.experiment_id if active_fault else None,
                },
            )
        )

        # Order Service Span (Root)
        order_status: Literal["OK", "ERROR", "UNSET"] = (
            "ERROR" if (payment_error or timeout_error) else "OK"
        )
        spans.append(
            SpanRecordSchema(
                trace_id=trace_id,
                span_id=order_span_id,
                parent_span_id=None,
                service_name="order-service",
                operation_name="POST /orders",
                start_time=order_start,
                end_time=order_end,
                duration_ms=actual_order_latency,
                status_code=order_status,
                status_message=(
                    "ClientTimeoutException: upstream connection timed out"
                    if timeout_error
                    else ("Downstream payment failure" if payment_error else None)
                ),
                attributes={
                    "http.method": "POST",
                    "order.id": order_id,
                    "http.status_code": 502 if payment_error else (504 if timeout_error else 201),
                },
            )
        )

        # ---------------------------------------------------------------------
        # Metrics Construction
        # ---------------------------------------------------------------------
        # Order service latency & count
        metrics.append(
            MetricPoint(
                timestamp=order_end,
                service_name="order-service",
                environment=self.environment,
                metric_name="http.server.request.duration_ms",
                value=actual_order_latency,
                unit="ms",
                trace_id=trace_id,
                attributes={
                    "endpoint": "/orders",
                    "status": "error" if order_status == "ERROR" else "ok",
                },
            )
        )
        metrics.append(
            MetricPoint(
                timestamp=order_end,
                service_name="order-service",
                environment=self.environment,
                metric_name="http.server.requests.count",
                value=1.0,
                unit="count",
                attributes={"endpoint": "/orders"},
            )
        )
        if order_status == "ERROR":
            metrics.append(
                MetricPoint(
                    timestamp=order_end,
                    service_name="order-service",
                    environment=self.environment,
                    metric_name="http.server.errors.count",
                    value=1.0,
                    unit="count",
                    trace_id=trace_id,
                    attributes={"endpoint": "/orders", "reason": "downstream_failure"},
                )
            )

        # Payment service latency & count
        metrics.append(
            MetricPoint(
                timestamp=payment_end,
                service_name="payment-service",
                environment=self.environment,
                metric_name="http.server.request.duration_ms",
                value=actual_payment_latency,
                unit="ms",
                trace_id=trace_id,
                attributes={
                    "endpoint": "/payments",
                    "status": "error" if payment_status == "ERROR" else "ok",
                },
            )
        )
        metrics.append(
            MetricPoint(
                timestamp=payment_end,
                service_name="payment-service",
                environment=self.environment,
                metric_name="http.server.requests.count",
                value=1.0,
                unit="count",
                attributes={"endpoint": "/payments"},
            )
        )
        if payment_status == "ERROR":
            metrics.append(
                MetricPoint(
                    timestamp=payment_end,
                    service_name="payment-service",
                    environment=self.environment,
                    metric_name="http.server.errors.count",
                    value=1.0,
                    unit="count",
                    trace_id=trace_id,
                    attributes={
                        "endpoint": "/payments",
                        "reason": "pool_exhausted" if payment_error else "timeout",
                    },
                )
            )

        # Simulated resource metrics (mathematically bounded between 20% and 85%)
        load_factor = 1.8 if is_fault else 1.0
        cpu_val = min(95.0, round(self.rng.uniform(25.0, 45.0) * load_factor, 1))
        mem_val = min(92.0, round(self.rng.uniform(40.0, 55.0) * load_factor, 1))
        metrics.append(
            MetricPoint(
                timestamp=order_end,
                service_name="payment-service",
                environment=self.environment,
                metric_name="system.cpu.utilization",
                value=cpu_val,
                unit="percent",
                attributes={"host": "payment-pod-1"},
            )
        )
        metrics.append(
            MetricPoint(
                timestamp=order_end,
                service_name="payment-service",
                environment=self.environment,
                metric_name="system.memory.utilization",
                value=mem_val,
                unit="percent",
                attributes={"host": "payment-pod-1"},
            )
        )

        # ---------------------------------------------------------------------
        # Structured Logs Construction
        # ---------------------------------------------------------------------
        # Order service logs
        logs.append(
            LogRecordSchema(
                timestamp=order_start,
                service_name="order-service",
                environment=self.environment,
                severity="INFO",
                message=f"Received order creation request for order '{order_id}'.",
                trace_id=trace_id,
                span_id=order_span_id,
                attributes={"order_id": order_id},
            )
        )

        # Payment service logs
        if payment_error:
            logs.append(
                LogRecordSchema(
                    timestamp=payment_end,
                    service_name="payment-service",
                    environment=self.environment,
                    severity="ERROR",
                    message="DatabaseConnectionError: Connection pool exhausted while processing payment transaction.",
                    trace_id=trace_id,
                    span_id=payment_span_id,
                    exception_type="ConnectionPoolTimeoutError",
                    error_class="DatabaseError",
                    attributes={"order_id": order_id, "pool_size": 20},
                )
            )
            logs.append(
                LogRecordSchema(
                    timestamp=order_end,
                    service_name="order-service",
                    environment=self.environment,
                    severity="ERROR",
                    message=f"DownstreamPaymentError: payment-service returned HTTP 500 for order '{order_id}'.",
                    trace_id=trace_id,
                    span_id=order_span_id,
                    exception_type="DownstreamPaymentError",
                    error_class="ServiceDependencyError",
                    attributes={"order_id": order_id, "downstream": "payment-service"},
                )
            )
        elif timeout_error:
            logs.append(
                LogRecordSchema(
                    timestamp=order_end,
                    service_name="order-service",
                    environment=self.environment,
                    severity="CRITICAL",
                    message=f"ClientTimeoutException: payment-service call exceeded timeout deadline (2000ms) for order '{order_id}'.",
                    trace_id=trace_id,
                    span_id=order_span_id,
                    exception_type="ClientTimeoutException",
                    error_class="TimeoutError",
                    attributes={"order_id": order_id, "timeout_limit_ms": 2000},
                )
            )
        else:
            logs.append(
                LogRecordSchema(
                    timestamp=payment_end,
                    service_name="payment-service",
                    environment=self.environment,
                    severity="INFO",
                    message=f"Payment authorized successfully for order '{order_id}'.",
                    trace_id=trace_id,
                    span_id=payment_span_id,
                    attributes={"order_id": order_id, "payment_status": "APPROVED"},
                )
            )
            logs.append(
                LogRecordSchema(
                    timestamp=order_end,
                    service_name="order-service",
                    environment=self.environment,
                    severity="INFO",
                    message=f"Order '{order_id}' completed and dispatched.",
                    trace_id=trace_id,
                    span_id=order_span_id,
                    attributes={"order_id": order_id, "order_status": "CONFIRMED"},
                )
            )

        status_str = "ERROR" if (payment_error or timeout_error) else "SUCCESS"
        return TransactionResult(
            trace_id=trace_id,
            order_id=order_id,
            status=status_str,
            order_duration_ms=actual_order_latency,
            payment_duration_ms=actual_payment_latency,
            metrics=metrics,
            logs=logs,
            spans=spans,
            is_fault=is_fault,
            fault_type=fault_type,
        )
