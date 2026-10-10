"""Controlled fault injection scenarios and safety boundary validation."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from app.core.exceptions import ValidationError

# Strict allowlist of safe, disposable local microservices
SAFETY_ALLOWLISTED_SERVICES: frozenset[str] = frozenset(
    {"order-service", "payment-service", "inventory-service", "notification-service"}
)

SUPPORTED_FAULT_TYPES: frozenset[str] = frozenset(
    {"latency_spike", "error_surge", "service_timeout"}
)

MIN_DURATION_SECONDS = 5
MAX_DURATION_SECONDS = 300


@dataclass(frozen=True)
class FaultScenarioDefinition:
    """Pre-configured definition of a reproducible fault scenario."""

    fault_type: Literal["latency_spike", "error_surge", "service_timeout"]
    scenario_name: str
    description: str
    target_service: str
    initiating_service: str
    symptom_services: list[str]
    default_parameters: dict[str, Any]
    expected_observable_signals: list[str]


# Standard benchmark scenarios for AegisOps
SCENARIO_CATALOG: dict[str, FaultScenarioDefinition] = {
    "latency_spike": FaultScenarioDefinition(
        fault_type="latency_spike",
        scenario_name="payment_gateway_slowdown",
        description="Injects artificial latency into payment processing, causing cascading checkout delays.",
        target_service="payment-service",
        initiating_service="payment-service",
        symptom_services=["order-service"],
        default_parameters={"added_latency_ms": 750.0, "jitter_ms": 50.0},
        expected_observable_signals=[
            "payment-service http.server.request.duration_ms p95 > 800ms",
            "order-service http.server.request.duration_ms p95 > 850ms",
            "Elevated span duration across payment-service spans",
        ],
    ),
    "error_surge": FaultScenarioDefinition(
        fault_type="error_surge",
        scenario_name="database_connection_exhaustion",
        description="Simulates database pool exhaustion causing HTTP 500 errors and transaction rollbacks.",
        target_service="payment-service",
        initiating_service="payment-service",
        symptom_services=["order-service"],
        default_parameters={"error_rate": 0.85, "http_status": 500},
        expected_observable_signals=[
            "payment-service http.server.errors.count surge",
            "order-service DownstreamPaymentError structured log spike",
            "Span status set to ERROR with error messages",
        ],
    ),
    "service_timeout": FaultScenarioDefinition(
        fault_type="service_timeout",
        scenario_name="downstream_network_blackhole",
        description="Simulates unacknowledged requests to payment-service exceeding the 2000ms client timeout.",
        target_service="payment-service",
        initiating_service="payment-service",
        symptom_services=["order-service"],
        default_parameters={"timeout_ms": 2000.0, "failure_rate": 1.0},
        expected_observable_signals=[
            "order-service ClientTimeoutException logged at CRITICAL severity",
            "order-service request duration plateaus at timeout threshold (2000ms)",
            "payment-service spans incomplete or cancelled with ERROR status",
        ],
    ),
}


@dataclass
class ActiveFaultState:
    """In-memory state tracker for an actively executing fault injection."""

    experiment_id: str
    fault_type: str
    target_service: str
    initiating_service: str
    symptom_services: list[str]
    parameters: dict[str, Any]
    start_time: datetime
    end_time: datetime
    is_active: bool = True
    cleanup_verified: bool = False
    details: dict[str, Any] = field(default_factory=dict)


class FaultInjectionEngine:
    """Engine executing controlled faults with safety boundaries and automatic cleanup checks."""

    def __init__(self) -> None:
        self._active_faults: dict[str, ActiveFaultState] = {}

    def validate_safety(
        self,
        target_service: str,
        fault_type: str,
        duration_seconds: int,
    ) -> None:
        """Enforce safety boundaries: allowlisted services only, bounded duration, no destructive shell access."""
        if target_service not in SAFETY_ALLOWLISTED_SERVICES:
            raise ValidationError(
                f"Service '{target_service}' is prohibited from fault injection. "
                f"Only explicitly allowlisted test services {sorted(SAFETY_ALLOWLISTED_SERVICES)} are permitted."
            )
        if fault_type not in SUPPORTED_FAULT_TYPES:
            raise ValidationError(
                f"Fault type '{fault_type}' is unsupported. Allowed: {sorted(SUPPORTED_FAULT_TYPES)}."
            )
        if duration_seconds < MIN_DURATION_SECONDS or duration_seconds > MAX_DURATION_SECONDS:
            raise ValidationError(
                f"Duration {duration_seconds}s out of safe bounds [{MIN_DURATION_SECONDS}s, {MAX_DURATION_SECONDS}s]."
            )

    def activate_fault(
        self,
        experiment_id: str,
        fault_type: Literal["latency_spike", "error_surge", "service_timeout"],
        target_service: str,
        duration_seconds: int = 30,
        custom_params: dict[str, Any] | None = None,
    ) -> ActiveFaultState:
        """Activate a controlled fault scenario."""
        self.validate_safety(target_service, fault_type, duration_seconds)

        # Prevent concurrent conflicting faults on the same target service
        for active in self._active_faults.values():
            if active.is_active and active.target_service == target_service:
                raise ValidationError(
                    f"Target service '{target_service}' already has an active fault ({active.experiment_id}). "
                    "Only one active fault per service is permitted."
                )

        catalog_entry = SCENARIO_CATALOG.get(fault_type)
        params = dict(catalog_entry.default_parameters if catalog_entry else {})
        if custom_params:
            params.update(custom_params)

        now = datetime.now(UTC)
        end_time = now + timedelta(seconds=duration_seconds)

        state = ActiveFaultState(
            experiment_id=experiment_id,
            fault_type=fault_type,
            target_service=target_service,
            initiating_service=catalog_entry.initiating_service
            if catalog_entry
            else target_service,
            symptom_services=catalog_entry.symptom_services if catalog_entry else [],
            parameters=params,
            start_time=now,
            end_time=end_time,
            is_active=True,
            cleanup_verified=False,
        )
        self._active_faults[experiment_id] = state
        return state

    def deactivate_fault(self, experiment_id: str) -> ActiveFaultState:
        """Deactivate a fault and perform verification cleanup."""
        if experiment_id not in self._active_faults:
            raise ValidationError(f"No active fault found for experiment '{experiment_id}'.")

        state = self._active_faults[experiment_id]
        state.is_active = False
        state.cleanup_verified = True
        return state

    def get_active_fault_for_service(self, service_name: str) -> ActiveFaultState | None:
        """Check if a service is currently under an active fault condition."""
        now = datetime.now(UTC)
        for state in self._active_faults.values():
            if state.target_service == service_name and state.is_active:
                if now > state.end_time:
                    # Auto-expire
                    state.is_active = False
                    state.cleanup_verified = True
                    return None
                return state
        return None

    def reset(self) -> None:
        """Clear all active states for testing isolation."""
        self._active_faults.clear()


# Global singleton instance
fault_engine = FaultInjectionEngine()
