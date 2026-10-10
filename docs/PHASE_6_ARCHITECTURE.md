# AegisOps Phase 6 — Production Architecture & Reliability Engineering

## 1. Overview & Architectural Goals

Phase 6 hardens AegisOps for operational deployment, continuous reliability, end-to-end observability, and automated behavioral evaluation across the full incident lifecycle:

$$\text{Telemetry Ingestion} \longrightarrow \text{Feature Generation} \longrightarrow \text{Anomaly/Risk Prediction} \longrightarrow \text{Causal RCA} \longrightarrow \text{Agent Investigation} \longrightarrow \text{Remediation Simulation}$$

### Key Production Tenets
1. **Low-Cardinality, Observable Signals**: Unified distributed tracing and bounded-cardinality Prometheus metrics across every pipeline stage without leaking sensitive parameters or raw telemetry payloads.
2. **Explicit Drift Taxonomy**: Mathematical and conceptual separation between data quality bugs, input feature drift, prediction distribution shifts, and confirmed performance degradation.
3. **Artifact Integrity & Version Tracking**: Cryptographic SHA-256 verification and unified `PlatformManifest` ensuring exact provenance for every inference and investigation.
4. **Resilient Failure Boundaries**: Bounded exponential backoff with jitter strictly protecting idempotent operations, paired with deterministic graceful degradation for unavailable upstream dependencies.
5. **Continuous Verification**: Fully deterministic evaluation and performance benchmarking suites executing without external credentials or cloud network dependencies.

---

## 2. Unified Observability & Correlation Architecture

### 2.1 Distributed Tracing (`PipelineTracer`)
The tracing engine (`backend/app/observability/tracer.py`) provides thread-safe context propagation and correlated span collection across synchronous and asynchronous execution boundaries.

- **Span Context Propagation**: Leverages Python `contextvars.ContextVar` (`current_span_ctx`) so child spans automatically attach to parent spans across deep call stacks without manual parameter passing.
- **Trace Identifier Formatting**: Standardized 32-character hexadecimal `trace_id` and 16-character `span_id`. HTTP middleware extracts or injects `X-Trace-Id` headers on all incoming requests.
- **Sensitive Tag Redaction**: Automatic key inspection sanitizes sensitive tokens (`api_key`, `secret`, `password`, `token`, `bearer`, `prompt`, `raw_payload`) to `[REDACTED]`.
- **Span Lifecycle**: Context-managed via `with tracer.span(name, tags={...}) as span:`. Records start time, finish time, elapsed duration, status (`ok` or `error`), error category, and bounded tags.

### 2.2 Low-Cardinality Metrics Registry (`MetricsRegistry`)
The metric collection system (`backend/app/observability/metrics.py`) avoids metric cardinality explosion while exposing production-grade operational telemetry.

- **Allowed Label Safeguard**: Metrics are bound to predefined string labels (`service`, `detector`, `status`, `endpoint`, `method`, `decision`, `action_type`, `error_type`). High-cardinality fields like raw incident IDs, arbitrary exception strings, or timestamp strings are rejected at recording time.
- **Metrics Catalog**:
  - `http.requests.total`: Counter of HTTP requests by `method`, `endpoint`, `status`.
  - `http_request_ms`: Histogram tracking latency percentiles (p50, p95, p99).
  - `telemetry.ingested.records`: Counter of accepted telemetry points by `signal` and `service`.
  - `telemetry.rejected.records`: Counter of rejected points by `signal` and `reason`.
  - `ml.inference.total`: Model predictions by `model_name` and `status`.
  - `ml_inference_ms`: Histogram of model inference latency.
  - `rca.invocations.total`: Root-cause analyses by `status`.
  - `rca_duration_ms`: Duration of deterministic graph traversal and candidate ranking.
  - `agent.investigations.total`: Agent runs by `status`.
  - `agent.tool_calls.total`: Tool invocations by `tool_name` and `status`.
  - `remediation.policy.decisions`: Policy results by `decision` and `action_type`.
  - `remediation.simulation.executions`: Simulation actions by `status` and `action_type`.
  - `db.query.latency_ms`: Latency of database queries.
  - `telemetry.data_quality.violations`: Violations detected by data quality checks.
- **Prometheus Standard Exposition**: Formats recorded counters, gauges, and summary statistics to Prometheus text format (`# HELP`, `# TYPE`, standard label brackets) at `/metrics` and `/api/v1/observability/metrics`.

### 2.3 Comprehensive Health & Readiness Probes
The health system (`backend/app/api/v1/health.py`) strictly differentiates process liveness from operational readiness:

- **Liveness (`GET /health/live`)**: Confirms the web process is running and event loop is responsive. Never connects to external dependencies.
- **Readiness (`GET /health/ready`)**: Evaluates real dependency health:
  1. **PostgreSQL Database**: Actively executes `SELECT 1` with a 2.0s bounded timeout. Returns `degraded` (HTTP 503) if unavailable.
  2. **Required ML Artifacts**: Checks disk existence of `isolation_forest.joblib` and `incident_predictor.joblib`. Returns `degraded` if missing.
  3. **Optional LLM Provider**: Inspects configuration for `ConfigurableLLMProvider`. If unconfigured or missing credentials, reports `optional_dependency_unavailable` without failing the core service readiness (enabling offline deterministic operations).

---

## 3. Data Quality & Statistical Drift Monitoring Architecture

### 3.1 Taxonomy of Operational Degradation
To prevent conflating routine telemetry variance with confirmed model failure, AegisOps implements a four-tier drift hierarchy:

```
+--------------------------------------------------------------------------+
| 1. DATA QUALITY ISSUE                                                    |
|    Schema mismatch, invalid types, negative latency, NaN values,         |
|    excessive freshness lag (>600s), or missing critical features.        |
+--------------------------------------------------------------------------+
                                     |
                                     v
+--------------------------------------------------------------------------+
| 2. INPUT FEATURE DRIFT (PSI >= 0.25)                                     |
|    Statistical shift in incoming feature distributions (CPU, error rate) |
|    relative to the reference training baseline.                           |
+--------------------------------------------------------------------------+
                                     |
                                     v
+--------------------------------------------------------------------------+
| 3. PREDICTION DISTRIBUTION DRIFT                                         |
|    System-wide anomaly rate shifts (>3x normal) or model confidence drops |
|    without ground-truth incident confirmation.                           |
+--------------------------------------------------------------------------+
                                     |
                                     v
+--------------------------------------------------------------------------+
| 4. CONFIRMED PERFORMANCE DEGRADATION                                     |
|    Evaluated drops in Precision, Recall, or Top-1 RCA accuracy when      |
|    verified incident ground-truth labels are available.                  |
+--------------------------------------------------------------------------+
```

### 3.2 Population Stability Index (PSI) Formulation
Input feature drift is computed using standard 10-bin decile histograms with Laplace add-1 smoothing to guarantee numerical stability:

$$\text{PSI} = \sum_{b=1}^{B} (A_b - E_b) \times \ln\left(\frac{A_b}{E_b}\right)$$

Where:
- $E_b$: Expected proportion of samples in bin $b$ from the baseline reference dataset.
- $A_b$: Actual observed proportion of samples in bin $b$ in current production traffic.
- **Laplace Smoothing**: $P_b = \frac{count_b + 1}{N + B}$ ensures zero-count bins never result in division-by-zero or $\ln(0)$ errors.
- **Interpretation Bounds**:
  - $\text{PSI} < 0.10$: Negligible shift (stable distribution).
  - $0.10 \le \text{PSI} < 0.25$: Moderate shift (advisory warning).
  - $\text{PSI} \ge 0.25$: Significant drift (triggers alert; model review advised).

---

## 4. Platform & Model Versioning Architecture

### 4.1 System Manifest (`PlatformManifest`)
Every anomaly score, RCA candidate list, and agent report contains platform provenance via `get_platform_manifest()`:

| Component | Identifier | Purpose |
| :--- | :--- | :--- |
| `platform_version` | `0.1.0-phase6` | Core backend release version |
| `feature_schema_version` | `v1.0-standard-15f` | 15 standard operational features |
| `rca_algorithm_version` | `v1.0-deterministic-temporal-graph` | Deterministic root-cause scoring formula |
| `agent_workflow_version` | `v1.0-langgraph-12node` | LangGraph investigation DAG specification |
| `tool_schema_version` | `v1.0-typed-bounded-9tools` | Tool argument schemas and bounded query limits |
| `runbook_catalog_version`| `v1.0-5runbooks` | Validated diagnostic remediation runbooks |
| `evaluation_suite_version`| `v1.0-13scenarios` | Verification benchmark suite |

### 4.2 Cryptographic Artifact Verification (`ArtifactValidator`)
Model loading (`IsolationForest`, `IncidentPredictor`) verifies artifact integrity prior to deserialization:
- **Digest Check**: Generates SHA-256 hash of `.joblib` model binaries.
- **Schema Validation**: Validates metadata `.json` for `feature_names`, `model_type`, and `trained_at`.
- **Fail-Safe Loading**: Corrupted or tampered files fail explicitly with `ModelArtifactCorruptedError` rather than executing degraded inference.

---

## 5. Resilience, Retries & Graceful Degradation

### 5.1 Bounded Exponential Backoff (`retry_with_backoff`)
Transient failures (database lock contention, network blips) are mitigated using a bounded exponential backoff decorator:

$$t_{\text{wait}} = \min(t_{\text{max}}, t_{\text{initial}} \times 2^{\text{attempt}-1}) + \text{Uniform}(0, \text{jitter})$$

- **Idempotency Guard**: Rejects decorating functions unless explicitly marked `is_idempotent=True`. Non-idempotent operations (such as persisting state mutations or executing remediation simulations) are strictly blocked from automatic retrying.
- **Max Delay Bound**: Caps delay at 10.0 seconds with a maximum of 3 attempts.

### 5.2 Graceful Degradation Handlers (`GracefulDegradationManager`)
When upstream components fail, AegisOps degrades predictably:
- **LLM Provider Unavailable**: Drops to `FakeDeterministicProvider` or marks investigation as `DEGRADED_ABSTAINED` with a clear explanation rather than returning hallucinated data.
- **Database Unavailable**: Emits HTTP 503 RFC 7807 Problem Details and preserves local metrics.
- **Missing Telemetry**: When telemetry spans are missing, the RCA engine abstains with `INSUFFICIENT_EVIDENCE` rather than guessing root causes.
- **Simulation Verification Failure**: If the simulator cannot verify fault termination post-remediation, `state_verified` is set to `False` and status is marked `SIMULATED_FAILED`.

---

## 6. End-to-End Incident Scenario Runner

The scenario runner CLI (`scripts/run_scenario.py`) executes and validates the entire 11-stage incident lifecycle deterministically:

```
[1. Readiness Check]
         |
[2. Fault Injection Active] (Payment service latency spike)
         |
[3. Telemetry Ingested] (Metrics, Logs, Spans)
         |
[4. Anomaly Detection] (Threshold detector latency > 500ms)
         |
[5. Root Cause Analysis] (Payment service identified as origin)
         |
[6. Agent Investigation] (LangGraph 6 tool calls, 0 hallucinations)
         |
[7. Grounded Report] (Evidence citations verified)
         |
[8. Policy Evaluation] (Deterministic safety policy ALLOW)
         |
[9. Safe Simulation Execution] (restore_service simulated)
         |
[10. Outcome Verification] (Fault confirmed deactivated)
         |
[11. Idempotent Cleanup] (Experiment marked COMPLETED)
```

The runner supports both `--mode live-sim` (live in-memory/database execution) and `--mode fixture` (isolated offline verification).
