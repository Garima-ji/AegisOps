# AegisOps Telemetry Data Contracts & Schema Specification

This document details the normalized operational telemetry models, validation constraints, and database persistence mapping implemented in **Phase 2** of AegisOps.

---

## 1. Architectural Philosophy

AegisOps ingests operational telemetry across three core pillars: **Numeric Metrics**, **Structured Logs**, and **Distributed Traces**. Telemetry is validated, normalized, associated with service identities, and persisted in PostgreSQL to form the ground-truth data substrate for Phase 3 anomaly detection models and root-cause ranking engines.

### Normalization Guarantees:
- **Strict UTC Timestamps**: All timestamps are converted to timezone-aware UTC (`datetime.timezone.utc`). Future timestamps exceeding a 5-minute tolerance window are rejected.
- **Consistent Metric Units**: Durations and latencies are normalized to milliseconds (`ms`). Counts are integers/floats with unit `count`. Utilization is normalized to `percent` (0.0 to 100.0).
- **Correlation Preservation**: Standard OpenTelemetry-compatible 32-hex character `trace_id` and 16-hex character `span_id` / `parent_span_id` are preserved and indexed across metrics, logs, and spans.
- **Untrusted Log Ingestion**: Log messages are treated as unescaped, untrusted data. Binary and null bytes (`\x00`) are stripped, and strings are capped at 4096 characters to prevent denial-of-service.
- **Bounded Dimensions**: Attributes are capped at a maximum of 32 key-value pairs, key length <= 64 characters, and value length <= 512 characters.

---

## 2. Telemetry Category Schemas

### A. Metrics (`MetricPoint`)

Represents numeric time-series observations from microservices, system collectors, or runtime runtimes.

| Field | Type | Required | Description | Constraints |
| :--- | :--- | :--- | :--- | :--- |
| `timestamp` | `datetime` (UTC) | Yes | Observation timestamp | Normalized to UTC, <= now + 5 min |
| `service_name` | `str` | Yes | Microservice identifier | 2-64 chars, regex `^[a-zA-Z0-9_\-\.]{2,64}$` |
| `environment` | `str` | Yes (default `production`) | Deployment environment | Max 32 chars |
| `metric_name` | `str` | Yes | Standard dot/underscore metric key | 2-128 chars |
| `value` | `float` | Yes | Numeric scalar observation | Finite number (NaN and Inf rejected) |
| `unit` | `str` | Yes (default `count`) | Dimensional unit | `ms`, `count`, `percent`, `bytes`, `ratio` |
| `attributes` | `dict[str, Any]` | No | Bounded contextual dimensions | Max 32 items, keys <= 64, values <= 512 |
| `trace_id` | `str` | No | Optional trace correlation ID | Exactly 32 hexadecimal characters |
| `ingested_at` | `datetime` (UTC) | Generated | Pipeline recording timestamp | Server-assigned |

#### Standard Metric Conventions:
- Request duration: `http.server.request.duration_ms` (`unit: ms`)
- Request volume: `http.server.requests.count` (`unit: count`)
- Error count: `http.server.errors.count` (`unit: count`)
- CPU utilization: `system.cpu.utilization` (`unit: percent`)
- Memory utilization: `system.memory.utilization` (`unit: percent`)

---

### B. Structured Logs (`LogRecordSchema`)

Represents discrete structured events emitted by applications and platform components.

| Field | Type | Required | Description | Constraints |
| :--- | :--- | :--- | :--- | :--- |
| `timestamp` | `datetime` (UTC) | Yes | Event timestamp | Normalized to UTC |
| `service_name` | `str` | Yes | Emitting service identifier | 2-64 chars |
| `environment` | `str` | Yes (default `production`) | Environment | Max 32 chars |
| `severity` | `str` | Yes (default `INFO`) | Standard log severity | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `message` | `str` | Yes | Log event text | Max 4096 chars, null bytes stripped |
| `attributes` | `dict[str, Any]` | No | Structured log context | Max 32 items, bounded |
| `trace_id` | `str` | No | Distributed trace ID | 32 hex characters |
| `span_id` | `str` | No | Span ID within trace | 16 hex characters |
| `exception_type` | `str` | No | Exception class name | Max 128 chars |
| `error_class` | `str` | No | High-level categorization | Max 64 chars (e.g. `DatabaseError`, `TimeoutError`) |
| `ingested_at` | `datetime` (UTC) | Generated | Server ingestion timestamp | Server-assigned |

---

### C. Distributed Traces (`SpanRecordSchema`)

Represents individual span segments in an OpenTelemetry-compatible distributed waterfall.

| Field | Type | Required | Description | Constraints |
| :--- | :--- | :--- | :--- | :--- |
| `trace_id` | `str` | Yes | Global trace identifier | Exactly 32 hexadecimal characters |
| `span_id` | `str` | Yes | Segment identifier | Exactly 16 hexadecimal characters |
| `parent_span_id` | `str` | No | Parent segment identifier | 16 hex chars, `None` for root spans |
| `service_name` | `str` | Yes | Service executing operation | 2-64 chars |
| `operation_name` | `str` | Yes | Span operation / endpoint | 1-128 chars (e.g. `POST /orders`) |
| `start_time` | `datetime` (UTC) | Yes | Span start timestamp | UTC timestamp |
| `end_time` | `datetime` (UTC) | Yes | Span completion timestamp | UTC timestamp, must be >= `start_time` |
| `duration_ms` | `float` | Computed | Span execution elapsed time | Computed if omitted: `(end - start) * 1000` |
| `status_code` | `str` | Yes (default `UNSET`) | Span outcome status | `OK`, `ERROR`, `UNSET` |
| `status_message` | `str` | No | Diagnostic error message | Max 512 chars |
| `attributes` | `dict[str, Any]` | No | Span dimensions and tags | Max 32 items, bounded |
| `ingested_at` | `datetime` (UTC) | Generated | Pipeline recording timestamp | Server-assigned |

---

### D. Service Registry (`ServiceSchema`)

Represents discovered microservices participating in the AegisOps observability mesh.

| Field | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `name` | `str` | Unique per environment | Unique service identifier |
| `environment` | `str` | Default `production` | Deployment environment |
| `version` | `str` | Nullable, max 64 chars | Active deployed artifact version |
| `status` | `str` | `healthy`, `degraded`, `unknown` | Operational registration health |
| `first_seen` | `datetime` (UTC) | Immutable initial signal | Earliest signal recorded |
| `last_seen` | `datetime` (UTC) | Updated on telemetry arrival | Most recent signal recorded |
| `metadata` | `dict[str, Any]` | JSON dictionary | Service tags, dependencies, runtime |

---

## 3. Database Persistence Mapping

### Table Relationships & Indexes

```
 +-------------------------------------------------------+
 |                  service_registry                     |
 +-------------------------------------------------------+
 | id: int (PK)                                          |
 | name: varchar(64)          [UQ: name, environment]   |
 | environment: varchar(32)                              |
 | version: varchar(64)                                  |
 | status: varchar(32)                                   |
 | first_seen: timestamptz                               |
 | last_seen: timestamptz                                |
 | metadata_json: jsonb                                  |
 +-------------------------------------------------------+
                            |
           (referenced via service_name)
                            |
 +-------------------------------------------------------+
 |                 metric_observations                   |
 +-------------------------------------------------------+
 | id: bigint (PK)                                       |
 | timestamp: timestamptz                                |
 | service_name: varchar(64)                             |
 | environment: varchar(32)                              |
 | metric_name: varchar(128)                             |
 | value: double precision                               |
 | unit: varchar(32)                                     |
 | attributes: jsonb                                     |
 | trace_id: varchar(32)                                 |
 | ingested_at: timestamptz                              |
 |                                                       |
 | Indexes:                                              |
 |  - ix_metrics_service_time (service_name, timestamp)  |
 |  - ix_metrics_name_time (metric_name, timestamp)      |
 |  - ix_metrics_trace_id (trace_id)                     |
 +-------------------------------------------------------+

 +-------------------------------------------------------+
 |                   structured_logs                     |
 +-------------------------------------------------------+
 | id: bigint (PK)                                       |
 | timestamp: timestamptz                                |
 | service_name: varchar(64)                             |
 | environment: varchar(32)                              |
 | severity: varchar(16)                                 |
 | message: text (max 4096)                              |
 | attributes: jsonb                                     |
 | trace_id: varchar(32)                                 |
 | span_id: varchar(16)                                  |
 | exception_type: varchar(128)                          |
 | error_class: varchar(64)                              |
 | ingested_at: timestamptz                              |
 |                                                       |
 | Indexes:                                              |
 |  - ix_logs_service_time (service_name, timestamp)     |
 |  - ix_logs_severity_time (severity, timestamp)        |
 |  - ix_logs_trace_id (trace_id)                        |
 +-------------------------------------------------------+

 +-------------------------------------------------------+
 |                     trace_spans                       |
 +-------------------------------------------------------+
 | id: bigint (PK)                                       |
 | trace_id: varchar(32)                                 |
 | span_id: varchar(16)                                  |
 | parent_span_id: varchar(16)                           |
 | service_name: varchar(64)                             |
 | operation_name: varchar(128)                          |
 | start_time: timestamptz                               |
 | end_time: timestamptz                                 |
 | duration_ms: double precision                         |
 | status_code: varchar(16)                              |
 | status_message: varchar(512)                          |
 | attributes: jsonb                                     |
 | ingested_at: timestamptz                              |
 |                                                       |
 | Indexes:                                              |
 |  - uq_trace_span (trace_id, span_id) [UNIQUE]         |
 |  - ix_spans_trace_id (trace_id)                       |
 |  - ix_spans_service_time (service_name, start_time)    |
 +-------------------------------------------------------+
```

---

## 4. Ingestion Batch Validation Policy

- **Envelope Bounds**: Ingestion payloads (`/metrics`, `/logs`, `/traces`) accept batches of 1 to 1000 items.
- **Partial Failure Handling**: Batches with partial validation failures persist all valid records inside an atomic database transaction. Rejected items are returned with index, field name, and actionable diagnostic explanation.
- **Service Auto-Registration**: Upon ingesting any metric, log, or span, the originating `service_name` is idempotently upserted into `service_registry` without generating duplicate entries.
