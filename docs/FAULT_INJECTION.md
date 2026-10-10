# AegisOps Controlled Fault Injection Framework

This document details the design, safety boundaries, reproducible benchmark scenarios, and ground-truth labeling architecture implemented in **Phase 2** of AegisOps.

---

## 1. Safety Boundaries & Guardrails

The fault injection framework is explicitly designed for local development and test environments to generate controlled training and evaluation data for future ML models (Phase 3).

### Strict Safety Constraints:
1. **Allowlisted Test Services Only**: Faults can only target explicitly allowlisted local services (`order-service`, `payment-service`, `inventory-service`, `notification-service`). Any attempt to target external hosts, IP addresses, or production systems is rejected with a `422 Unprocessable Entity` error.
2. **No Shell / Host Disruption**: Faults are executed at the application and synthetic workload level. No arbitrary shell commands, destructive resource exhaustion, or host shutdowns are ever executed.
3. **Strictly Bounded Duration**: Experiments enforce minimum duration of 5 seconds and maximum duration of 300 seconds (5 minutes). Active experiments automatically expire upon reaching their scheduled end time.
4. **Single Active Fault Per Service**: The framework strictly prevents concurrent overlapping faults from targeting the same service, ensuring unambiguous causal attribution.
5. **Safe Termination**: Active experiments can be manually stopped early via `POST /api/v1/experiments/{id}/stop`, which executes clean state restoration.
6. **Separation from Features**: Ground-truth labels are stored in an isolated table (`fault_labels`) to prevent data leakage into ML model training features.

---

## 2. Standard Benchmark Scenarios

### Scenario 1: `latency_spike` (Payment Gateway Slowdown)
- **Target Service**: `payment-service`
- **Initiating Service (Patient Zero)**: `payment-service`
- **Symptom Services**: `order-service`
- **Mechanism**: Injects artificial delay (+750ms with +/-50ms jitter) into payment processing.
- **Expected Observable Signals**:
  - `payment-service`: `http.server.request.duration_ms` p95 elevates from ~45ms to >800ms.
  - `order-service`: Cascading delay as the order handler awaits the payment response; overall request duration exceeds 850ms.
  - Distributed trace waterfall shows root `order-service` span duration elevated, with the child `payment-service` span consuming >90% of the wall-clock time.

### Scenario 2: `error_surge` (Database Connection Pool Exhaustion)
- **Target Service**: `payment-service`
- **Initiating Service**: `payment-service`
- **Symptom Services**: `order-service`
- **Mechanism**: Injects high failure rate (85%) returning HTTP 500 `DatabaseConnectionError`.
- **Expected Observable Signals**:
  - `payment-service`: Surge in `http.server.errors.count` metric and `ERROR` level logs with `ConnectionPoolTimeoutError` stack traces.
  - `order-service`: Emits `DownstreamPaymentError` structured logs and sets root span status to `ERROR`.
  - Span status for `POST /payments` and `db.query.verify_account` set to `ERROR`.

### Scenario 3: `service_timeout` (Downstream Network Blackhole)
- **Target Service**: `payment-service`
- **Initiating Service**: `payment-service`
- **Symptom Services**: `order-service`
- **Mechanism**: Simulates unacknowledged calls that exceed the 2000ms client timeout deadline.
- **Expected Observable Signals**:
  - `order-service`: `ClientTimeoutException` logged at `CRITICAL` severity with attributes `timeout_limit_ms: 2000`.
  - Latency plateaus sharply at the 2000ms timeout boundary.
  - Trace spans cancelled with `ERROR` status.

---

## 3. Ground-Truth Experiment Labels (`fault_labels`)

Every experiment generates ground-truth labels during its active window, providing clean supervision for ML evaluation in Phase 3.

### Schema:
```sql
CREATE TABLE fault_labels (
    id SERIAL PRIMARY KEY,
    experiment_id VARCHAR(64) REFERENCES experiments(id),
    timestamp TIMESTAMPTZ NOT NULL,
    service_name VARCHAR(64) NOT NULL,
    is_fault BOOLEAN NOT NULL,
    fault_type VARCHAR(64) NOT NULL,
    severity VARCHAR(16) NOT NULL,  -- CRITICAL (initiator), HIGH (symptom)
    metadata_json JSONB
);
```

### Role Separation:
- **Initiator (Patient Zero)**: The service where the fault was directly injected (`severity: "CRITICAL"`, `role: "initiator"`).
- **Symptom Service**: Downstream dependent services impacted by cascading failures (`severity: "HIGH"`, `role: "symptom"`).

---

## 4. API Usage Examples

### 1. Launch Controlled Experiment
```bash
curl -X POST http://localhost:8000/api/v1/experiments \
  -H "Content-Type: application/json" \
  -d '{
    "scenario_name": "payment_slowdown_benchmark",
    "description": "Evaluate p95 latency anomaly detector",
    "target_service": "payment-service",
    "fault_type": "latency_spike",
    "parameters": {"added_latency_ms": 750.0, "jitter_ms": 30.0},
    "duration_seconds": 60,
    "initiating_service": "payment-service",
    "symptom_services": ["order-service"]
  }'
```

### 2. Retrieve Ground-Truth Labels for Evaluation
```bash
curl -X GET http://localhost:8000/api/v1/experiments/exp_20261010_latency_spike_a1b2c3/labels
```

### 3. Safely Stop Experiment Early
```bash
curl -X POST http://localhost:8000/api/v1/experiments/exp_20261010_latency_spike_a1b2c3/stop
```

---

## 5. Deterministic Workload Generator

AegisOps provides `DeterministicTelemetryGenerator` to generate reproducible synthetic workloads:

```python
from app.ingestion.generator import DeterministicTelemetryGenerator

# Reproducible normal traffic
gen = DeterministicTelemetryGenerator(seed=42)
normal_batch = gen.generate_normal_traffic(count=10)

# Reproducible fault traffic
fault_batch = gen.generate_fault_traffic(
    fault_type="latency_spike",
    experiment_id="exp_demo_01",
    count=10,
    custom_params={"added_latency_ms": 800.0},
)
```
