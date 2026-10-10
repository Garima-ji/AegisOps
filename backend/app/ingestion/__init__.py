"""Telemetry ingestion, synthetic workloads, and fault-injection modules."""

from app.ingestion.fault_injection import (
    SAFETY_ALLOWLISTED_SERVICES,
    SCENARIO_CATALOG,
    ActiveFaultState,
    FaultInjectionEngine,
    FaultScenarioDefinition,
    fault_engine,
)
from app.ingestion.generator import (
    DeterministicTelemetryGenerator,
    TelemetryBatch,
)
from app.ingestion.sample_app import (
    InstrumentedSampleApp,
    TransactionResult,
)

__all__ = [
    "SAFETY_ALLOWLISTED_SERVICES",
    "SCENARIO_CATALOG",
    "FaultScenarioDefinition",
    "ActiveFaultState",
    "FaultInjectionEngine",
    "fault_engine",
    "InstrumentedSampleApp",
    "TransactionResult",
    "DeterministicTelemetryGenerator",
    "TelemetryBatch",
]
