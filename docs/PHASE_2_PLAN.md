# AegisOps — Phase 2 Plan: Telemetry Ingestion, Persistence & Controlled Fault Injection

## 1. Executive Summary & Objectives

Phase 2 establishes the high-throughput operational telemetry data foundation for AegisOps. The system transitions from a static architectural scaffold to an active ingestion and query platform handling application metrics, structured logs, and distributed trace events.

### Core Objectives
1. **Typed Multi-Signal Ingestion**: Ingest metrics, structured logs, and distributed trace events via validated, bounded batch APIs.
2. **Standardized Normalization**: Enforce UTC timestamps, standard units (e.g., milliseconds for latency, percentages/ratios for utilization), bounded attribute dictionaries, and sanitization of untrusted log strings.
3. **Idempotent Service Registry**: Dynamically discover and maintain service identities, environments, versions, and active/degraded statuses with automatic upserts.
4. **Relational Persistence via PostgreSQL**: Clean SQLAlchemy 2.0 ORM models and Alembic migration `002_telemetry_and_experiments.py` with multi-column composite indexes for time-series range and trace lookups.
5. **Time-Bounded Query APIs**: Strict window validation, mandatory pagination, result caps, and deterministic sorting to eliminate unbounded full-table scans.
6. **Realistic Cooperating Microservices & Generator**: A lightweight local multi-service architecture (`order-service` and `payment-service`) generating real request metrics, inter-service HTTP spans, and correlated structured logs.
7. **Controlled Fault Injection Framework**: Bounded, reproducible fault scenarios (Downstream Latency, HTTP 500 Error Surge, Downstream Timeout/Drop) producing isolated ground-truth labels for downstream ML training (Phase 3).
8. **Ingestion Observability**: Operational counters for accepted/rejected records, ingestion batch latencies, error diagnostics, and active experiment tracking.

---

## 2. Database Schema Design (Alembic Migration 002)

All telemetry tables extend the existing database schema without modifying or dropping the Phase 1 `system_audit_logs` table.

```
+--------------------------------------------------------------------------------------------------+
|                                        Database Schema (v2)                                      |
+--------------------------------------------------------------------------------------------------+
                                                 |
         +---------------------------------------+---------------------------------------+
         |                                       |                                       |
         v                                       v                                       v
+------------------+                   +--------------------+                  +--------------------+
|     services     |                   |   metric_records   |                  |    log_records     |
| id (PK)          |                   | id (PK)            |                  | id (PK)            |
| name (Indexed)   |<-------+          | timestamp (UTC)    |                  | timestamp (UTC)    |
| environment      |        |          | service_name (FK)  |                  | service_name (FK)  |
| version          |        +--------->| environment        |                  | environment        |
| status           |        |          | metric_name        |                  | severity (INFO,ERR)|
| first_seen       |        |          | value (Float)      |                  | message (Text)     |
| last_seen        |        |          | unit (str)         |                  | attributes (JSON)  |
| metadata_json    |        |          | attributes (JSON)  |                  | trace_id (Indexed) |
| UQ(name, env)    |        |          | trace_id (Indexed) |                  | span_id            |
+------------------+        |          | ingested_at        |                  | exception_type     |
                            |          +--------------------+                  +--------------------+
                            |
         +------------------+--------------------+
         |                                       |
         v                                       v
+--------------------+                 +--------------------+                  +--------------------+
|    trace_spans     |                 |    experiments     |                  |    fault_labels    |
| id (PK)            |                 | id (PK, UUID/str)  |<-----------------| id (PK)            |
| trace_id (Indexed) |                 | scenario_name      |                  | experiment_id (FK) |
| span_id (Indexed)  |                 | target_service     |                  | timestamp (UTC)    |
| parent_span_id     |                 | fault_type         |                  | service_name       |
| service_name (FK)  |                 | parameters (JSON)  |                  | is_fault (Boolean) |
| operation_name     |                 | start_time (UTC)   |                  | fault_type         |
| start_time (UTC)   |                 | end_time (UTC)     |                  | severity           |
| end_time (UTC)     |                 | status             |                  | metadata_json      |
| duration_ms (Float)|                 | initiating_service |                  +--------------------+
| status_code (OK/ERR|                 | symptom_services   |
| attributes (JSON)  |                 | cleanup_result     |
+--------------------+                 +--------------------+
```

### Strategic Composite Indexes
- `ix_metrics_query`: `(service_name, metric_name, timestamp DESC)`
- `ix_logs_query`: `(service_name, severity, timestamp DESC)`
- `ix_logs_trace`: `(trace_id, timestamp)`
- `ix_spans_trace`: `(trace_id, start_time ASC)`
- `ix_spans_service_time`: `(service_name, start_time DESC)`
- `ix_labels_exp_time`: `(experiment_id, timestamp ASC)`

---

## 3. Telemetry Signal Contracts (Pydantic v2)

### A. Metrics (`MetricPoint`)
- `timestamp`: UTC ISO-8601 string or datetime.
- `service_name`: Identifier of the reporting microservice (regex: `^[a-zA-Z0-9_\-\.]{2,64}$`).
- `environment`: `production` | `staging` | `development` | `test`.
- `metric_name`: Dot/underscore-separated name (e.g., `http.server.duration_ms`, `system.cpu.utilization`).
- `value`: Numeric float/int. Must not be NaN or Infinity.
- `unit`: Standardized unit string (`ms`, `count`, `percent`, `bytes`, `ratio`).
- `attributes`: Key-value map bounded to max 32 keys, max key length 64, max value length 256.
- `trace_id`: Optional 32-character hex trace correlation ID.

### B. Structured Logs (`LogRecordSchema`)
- `timestamp`: UTC datetime.
- `service_name`: Reporting service.
- `severity`: Standardized enum (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`).
- `message`: Log string sanitized against command/script injection, capped at 4096 characters.
- `attributes`: Structured contextual fields (max 32 keys).
- `trace_id` / `span_id`: Distributed tracing context.
- `exception_type` & `error_class`: Structured error categorization when present.

### C. Distributed Traces (`SpanRecordSchema`)
- `trace_id`: 32-hex character OpenTelemetry-compliant trace ID.
- `span_id`: 16-hex character span ID.
- `parent_span_id`: Optional 16-hex character parent span ID (None for root span).
- `service_name`: Service executing the span.
- `operation_name`: HTTP route, RPC method, or DB operation (e.g. `GET /api/orders/{id}`).
- `start_time` & `end_time`: Monotonic UTC timestamps (`end_time >= start_time`).
- `duration_ms`: Computed or provided span duration in milliseconds.
- `status_code`: `OK` | `ERROR` | `UNSET`.
- `status_message`: Optional description of span errors.
- `attributes`: HTTP method, status code, peer address, database statement.

---

## 4. API Endpoints Contract

### Ingestion Endpoints (Batch, bounded to 1,000 items)
- `POST /api/v1/telemetry/metrics` -> `202 Accepted` or `422 Unprocessable`
- `POST /api/v1/telemetry/logs` -> `202 Accepted` or `422 Unprocessable`
- `POST /api/v1/telemetry/traces` -> `202 Accepted` or `422 Unprocessable`
- Ingestion response returns:
  ```json
  {
    "status": "success",
    "received": 50,
    "accepted": 48,
    "rejected": 2,
    "errors": [
      {"index": 3, "field": "timestamp", "reason": "Future timestamp exceeds tolerance"}
    ],
    "duration_ms": 12.4
  }
  ```

### Query Endpoints (Time-bounded, paginated)
- `GET /api/v1/telemetry/metrics?service=order-service&metric=http.server.duration_ms&start_time=...&end_time=...&limit=100`
- `GET /api/v1/telemetry/logs?service=payment-service&severity=ERROR&start_time=...&end_time=...&limit=100`
- `GET /api/v1/telemetry/traces?service=order-service&min_duration_ms=500&limit=50`
- `GET /api/v1/telemetry/traces/{trace_id}` -> Returns ordered span tree with parent-child hierarchy.

### Service Registry Endpoints
- `GET /api/v1/services` -> Lists all registered services with health, environments, and last heartbeat.
- `GET /api/v1/services/{service_name}` -> Single service detail with recent signal volume summary.

### Fault Injection & Experiment Endpoints
- `POST /api/v1/experiments` -> Triggers an allowlisted, time-bounded scenario on registered test services.
- `GET /api/v1/experiments` -> Lists experiment history.
- `GET /api/v1/experiments/{experiment_id}` -> Detailed status, target, and cleanup result.
- `GET /api/v1/experiments/{experiment_id}/labels` -> Ground-truth timeline labels (`is_fault=True/False`).
- `POST /api/v1/experiments/{experiment_id}/stop` -> Immediate emergency stop and state restoration.

---

## 5. Sample Microservices & Fault Scenarios

### Architecture
To prevent the high memory footprint of the full OpenTelemetry Demo (which requires 20+ containers and 8+ GB RAM), AegisOps implements an instrumented 2-service cooperating architecture:
1. `order-service`: Exposes `/api/orders` (POST, GET). Calculates totals and invokes `payment-service` via HTTP.
2. `payment-service`: Exposes `/api/payments/charge` (POST). Simulates payment gateway authorization.

### Supported Fault Scenarios
1. **Scenario 1: Downstream Latency Spike (`latency_spike`)**
   - Injects artificial sleep (e.g. 800ms) in `payment-service`.
   - Observable symptoms: `order-service` request duration spikes; spans reflect elongated downstream span duration.
   - Ground truth: Initiator is `payment-service`, symptom is `order-service`.
2. **Scenario 2: HTTP 500 Error Surge (`error_surge`)**
   - Injects 70% failure rate in `payment-service` with structured `PaymentGatewayError` logs.
   - Observable symptoms: Error logs in `payment-service`, cascading HTTP 502/500 errors in `order-service`.
   - Ground truth: Initiator is `payment-service`.
3. **Scenario 3: Downstream Timeout & Degradation (`service_timeout`)**
   - Sets payment processing delay beyond `order-service`'s HTTP client timeout (e.g., 2000ms delay vs. 1000ms timeout).
   - Observable symptoms: `order-service` raises `ConnectTimeout` / `ReadTimeout`, span status `ERROR`.
   - Ground truth: Initiator is `payment-service`, symptom is `order-service`.

---

## 6. Implementation Steps

1. **Schemas (`backend/app/schemas/telemetry.py`, `backend/app/schemas/experiment.py`)**:
   Implement Pydantic v2 models with rigorous bounds, normalization validators, and error structures.
2. **Persistence Models (`backend/app/db/models/telemetry.py`, `backend/app/db/models/experiment.py`)**:
   SQLAlchemy 2.0 ORM models with composite indexes.
3. **Alembic Migration (`backend/migrations/versions/002_telemetry_and_experiments.py`)**:
   Deterministic schema upgrade and downgrade scripts.
4. **Ingestion & Registry Service (`backend/app/services/telemetry_service.py`, `backend/app/services/registry_service.py`)**:
   Batch processor, validation error collector, service upsert, and transactional bulk persistence.
5. **API Routers (`backend/app/api/v1/telemetry.py`, `backend/app/api/v1/services.py`, `backend/app/api/v1/experiments.py`)**:
   Mount endpoints and validate query parameters.
6. **Telemetry Generator & Fault Injection Engine (`backend/app/ingestion/generator.py`, `backend/app/ingestion/fault_injection.py`)**:
   Deterministic synthetic and live request generator with safe fault orchestration.
7. **Pipeline Observability (`backend/app/observability/metrics.py`)**:
   Low-cardinality in-memory counters and latency histograms for ingestion.
8. **Automated Test Suite (`backend/tests/`)**:
   Unit tests, integration tests against database, and end-to-end telemetry fault injection lifecycle test.
9. **Documentation Updates**:
   Create `docs/TELEMETRY_SCHEMA.md`, `docs/FAULT_INJECTION.md`, `docs/API.md`, `docs/PHASE_2_TEST_REPORT.md`, update `README.md`.

---

## 7. Acceptance Criteria

- All 26 Phase 1 tests continue to pass without regression.
- New migration `002_telemetry_and_experiments.py` runs cleanly forward and backward on PostgreSQL and SQLite.
- Batch ingestion accepts up to 1,000 records, rejecting malformed items with detailed error reporting.
- Query APIs enforce time ranges and pagination limits (default 100, max 1000).
- Service registry upserts automatically on incoming telemetry.
- All 3 fault scenarios execute predictably with bounded duration and record ground-truth labels.
- Ruff linting and formatting pass with 0 errors.
- Mypy strict type checking passes with 0 issues.
- Overall test coverage remains >= 85%.
