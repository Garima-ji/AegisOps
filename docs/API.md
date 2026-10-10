# AegisOps API Reference (v1)

This document provides complete documentation of the AegisOps REST APIs, including request formats, query parameters, response structures, and RFC 7807 error representations.

Interactive OpenAPI documentation is also available locally at:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

---

## 1. Health & System Probes

### `GET /health/live`
- **Description**: Process liveness probe.
- **Response**: `200 OK`
```json
{
  "status": "alive",
  "timestamp": "2026-10-10T02:00:00Z",
  "version": "0.1.0"
}
```

### `GET /health/ready`
- **Description**: Dependency readiness probe validating database connectivity.
- **Response**: `200 OK` (Healthy) or `503 Service Unavailable` (Degraded)
```json
{
  "status": "ready",
  "database": "connected",
  "timestamp": "2026-10-10T02:00:00Z",
  "details": {
    "database": {
      "message": "Database connection healthy",
      "dialect": "postgresql",
      "latency_ms": 1.2
    }
  }
}
```

### `GET /api/v1/system/info`
- **Description**: Sanitized runtime metadata and enabled feature flags.
- **Response**: `200 OK`
```json
{
  "app_name": "AegisOps",
  "version": "0.1.0",
  "environment": "development",
  "debug": true,
  "api_prefix": "/api/v1",
  "features": {
    "enable_metrics": true,
    "enable_background_tasks": true
  }
}
```

---

## 2. Ingestion Endpoints

### `POST /api/v1/telemetry/metrics`
- **Description**: Ingest a bounded batch of metric observations (1 to 1000 items).
- **Request Body**:
```json
{
  "items": [
    {
      "timestamp": "2026-10-10T02:00:00Z",
      "service_name": "order-service",
      "environment": "production",
      "metric_name": "http.server.request.duration_ms",
      "value": 115.4,
      "unit": "ms",
      "attributes": {"endpoint": "/orders"},
      "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736"
    }
  ]
}
```
- **Response**: `200 OK` (`IngestionResponse`)
```json
{
  "status": "success",
  "received": 1,
  "accepted": 1,
  "rejected": 0,
  "errors": [],
  "duration_ms": 5.42
}
```

### `POST /api/v1/telemetry/logs`
- **Description**: Ingest structured application and platform logs (1 to 1000 items).
- **Request Body**:
```json
{
  "items": [
    {
      "timestamp": "2026-10-10T02:00:00Z",
      "service_name": "payment-service",
      "environment": "production",
      "severity": "ERROR",
      "message": "Database pool exhausted while processing payment transaction.",
      "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
      "span_id": "00f067aa0ba902b7",
      "exception_type": "ConnectionPoolTimeoutError",
      "error_class": "DatabaseError",
      "attributes": {"pool_size": 20}
    }
  ]
}
```
- **Response**: `200 OK` (`IngestionResponse`)

### `POST /api/v1/telemetry/traces`
- **Description**: Ingest OpenTelemetry-compatible distributed trace span records (1 to 1000 items).
- **Request Body**:
```json
{
  "items": [
    {
      "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
      "span_id": "00f067aa0ba902b7",
      "parent_span_id": "5fb397be34d23b0f",
      "service_name": "payment-service",
      "operation_name": "POST /payments",
      "start_time": "2026-10-10T02:00:00.000Z",
      "end_time": "2026-10-10T02:00:00.045Z",
      "duration_ms": 45.0,
      "status_code": "OK",
      "attributes": {"http.status_code": 200}
    }
  ]
}
```
- **Response**: `200 OK` (`IngestionResponse`)

---

## 3. Query Endpoints

### `GET /api/v1/telemetry/metrics`
- **Query Parameters**:
  - `service_name`: Filter by microservice identifier.
  - `metric_name`: Filter by metric identifier.
  - `environment`: Deployment environment.
  - `start_time`: Start UTC timestamp (`ISO-8601`).
  - `end_time`: End UTC timestamp (`ISO-8601`).
  - `limit`: Maximum records returned (1 to 1000, default 100).
  - `offset`: Pagination offset (default 0).
- **Response**: `200 OK`
```json
{
  "items": [ ... ],
  "total": 42,
  "limit": 100,
  "offset": 0
}
```

### `GET /api/v1/telemetry/logs`
- **Query Parameters**: `service_name`, `severity`, `environment`, `trace_id`, `start_time`, `end_time`, `limit`, `offset`.
- **Response**: `200 OK`

### `GET /api/v1/telemetry/traces`
- **Query Parameters**: `service_name`, `trace_id`, `min_duration_ms`, `status_code`, `start_time`, `end_time`, `limit`, `offset`.
- **Response**: `200 OK`

### `GET /api/v1/telemetry/traces/{trace_id}`
- **Description**: Returns all spans for a distributed trace ordered in hierarchical sequence (roots first).
- **Response**: `200 OK` (`TraceWaterfallResponse`)
```json
{
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "span_count": 3,
  "spans": [
    {
      "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
      "span_id": "5fb397be34d23b0f",
      "parent_span_id": null,
      "service_name": "order-service",
      "operation_name": "POST /orders",
      "duration_ms": 78.5,
      "status_code": "OK"
    },
    {
      "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
      "span_id": "00f067aa0ba902b7",
      "parent_span_id": "5fb397be34d23b0f",
      "service_name": "payment-service",
      "operation_name": "POST /payments",
      "duration_ms": 45.0,
      "status_code": "OK"
    }
  ]
}
```

### `GET /api/v1/telemetry/pipeline-metrics`
- **Description**: Real-time operational counters and processing latency statistics for the ingestion pipeline.
- **Response**: `200 OK`
```json
{
  "counters": {
    "telemetry.metrics.received_total": 120,
    "telemetry.metrics.accepted_total": 120,
    "telemetry.metrics.rejected_total": 0,
    "telemetry.ingestion.batches_total": 12
  },
  "durations_ms": {
    "metrics_ingestion_ms": {"count": 12, "avg": 3.4, "min": 1.1, "max": 7.8}
  },
  "active_experiments": 0
}
```

---

## 4. Service Registry Endpoints

### `GET /api/v1/services`
- **Query Parameters**: `environment`, `limit`, `offset`.
- **Response**: `200 OK`
```json
{
  "items": [
    {
      "name": "order-service",
      "environment": "production",
      "version": null,
      "status": "healthy",
      "first_seen": "2026-10-10T01:00:00Z",
      "last_seen": "2026-10-10T02:00:00Z",
      "metadata": {}
    }
  ],
  "total": 1
}
```

### `GET /api/v1/services/{service_name}`
- **Description**: Returns detailed registration metadata for a specific microservice.
- **Response**: `200 OK` (`ServiceSchema`) or `404 Not Found`.

---

## 5. Fault Injection & Experiment Endpoints

### `POST /api/v1/experiments`
- **Description**: Launch a bounded fault-injection experiment against an allowlisted service.
- **Request Body**:
```json
{
  "scenario_name": "payment_slowdown_eval",
  "description": "Benchmark latency spike detection",
  "target_service": "payment-service",
  "fault_type": "latency_spike",
  "parameters": {"added_latency_ms": 750.0},
  "duration_seconds": 60,
  "initiating_service": "payment-service",
  "symptom_services": ["order-service"]
}
```
- **Response**: `201 Created` (`ExperimentResponse`)

### `GET /api/v1/experiments`
- **Description**: List historical and active experiments.
- **Response**: `200 OK` (`list[ExperimentResponse]`)

### `GET /api/v1/experiments/{experiment_id}`
- **Response**: `200 OK` (`ExperimentResponse`)

### `GET /api/v1/experiments/{experiment_id}/labels`
- **Description**: Retrieve ground-truth labels for evaluation and ML training.
- **Response**: `200 OK` (`FaultLabelListResponse`)
```json
{
  "experiment_id": "exp_20261010_latency_spike_a1b2c3",
  "labels": [
    {
      "id": 1,
      "experiment_id": "exp_20261010_latency_spike_a1b2c3",
      "timestamp": "2026-10-10T02:00:00Z",
      "service_name": "payment-service",
      "is_fault": true,
      "fault_type": "latency_spike",
      "severity": "CRITICAL",
      "metadata": {"role": "initiator"}
    },
    {
      "id": 2,
      "experiment_id": "exp_20261010_latency_spike_a1b2c3",
      "timestamp": "2026-10-10T02:00:00Z",
      "service_name": "order-service",
      "is_fault": true,
      "fault_type": "latency_spike",
      "severity": "HIGH",
      "metadata": {"role": "symptom"}
    }
  ],
  "total": 2
}
```

### `POST /api/v1/experiments/{experiment_id}/stop`
- **Description**: Safely interrupt an active experiment and trigger cleanup.
- **Response**: `200 OK` (`ExperimentResponse` with `status: "STOPPED"`).

---

## 6. RFC 7807 Error Representations

All error responses adhere to standard RFC 7807 Problem Details:

```json
{
  "type": "https://aegisops.dev/errors/validation-error",
  "title": "Validation Error",
  "status": 422,
  "detail": "Target service 'unknown-host' is not on the safety allowlist for fault injection.",
  "instance": "/api/v1/experiments",
  "code": "VALIDATION_ERROR",
  "request_id": "req_84f93a1c0b2d",
  "errors": []
}
```
