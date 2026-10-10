# AegisOps Phase 6 Implementation Plan: Production Hardening, Observability, Continuous Evaluation & Deployment

## 1. Executive Summary & Objective

Phase 6 hardens the AegisOps platform for operational resilience, end-to-end traceability, continuous statistical and agentic evaluation, automated data quality/drift monitoring, and production deployment readiness.

The platform spans 5 mature foundational phases:
- **Phase 1**: FastAPI foundation, structured logging, PostgreSQL connection pooling, health checks, CI.
- **Phase 2**: Telemetry ingestion (metrics, logs, traces), fault injection engine (`FaultInjectionEngine`), and ground truth labeling.
- **Phase 3**: Statistical baselines, Isolation Forest anomaly detector, Gradient Boosting incident predictor, and detection-delay metrics.
- **Phase 4**: Service topology graph, causal correlation, and deterministic root-cause ranking (`RootCauseRanker`).
- **Phase 5**: LangGraph agentic investigation, typed bounded tools, citation-grounded reports, deterministic policy engine (`RemediationPolicyEngine`), safe simulation execution, and audit persistence.

Phase 6 elevates this architecture from an isolated prototype to a hardened, continuously verifiable platform without introducing unnecessary external microservices or breaking existing public contracts.

---

## 2. Baseline Architecture & Current Gaps

### Current State
1. **Observability**: Structured JSON/console logging with `correlation_id_ctx` in `app.core.logging`. In-memory metrics in `app.observability.metrics` tracks basic telemetry ingestion counters and latencies.
   - *Gap*: Traces are stored in DB, but distributed operational tracing of the internal pipeline stages (ingestion → ML → RCA → Agent → Policy → Simulation) is not unified. Metrics lack bounded-cardinality Prometheus exposition for downstream stages (inference latency, RCA latency, investigation step counts, policy rejections, drift alerts, DB latency).
2. **Health Probes**: Liveness (`/health/live`) checks process responsiveness; Readiness (`/health/ready`) checks database connectivity.
   - *Gap*: Readiness does not inspect model artifact integrity or distinguish required vs. optional dependencies (such as offline LLM providers).
3. **Continuous Evaluation**: Disjoint evaluation scripts exist (`scripts/train_models.py`, `scripts/evaluate_root_cause.py`, `scripts/evaluate_investigations.py`).
   - *Gap*: No unified, continuous evaluation harness executing end-to-end regression suites across ML detection, RCA ranking, and Agent investigation with versioned reports.
4. **Data Quality & Drift Monitoring**: Feature schema and sliding window datasets exist in `app.ml.features`.
   - *Gap*: No online monitoring of feature drift, missingness, timestamp gaps, or prediction distribution shifts with explicit separation between data quality issues, input drift, and confirmed performance degradation.
5. **Model & Configuration Versioning**: Model artifacts contain basic metadata dictionaries.
   - *Gap*: No centralized platform manifest tying model versions, feature schemas, detector thresholds, RCA rules, agent prompt versions, runbook versions, and evaluation suites to each investigation.
6. **Reliability & Graceful Degradation**: Error handling is present, but lacks structured backoff/retries for transient database issues, strict query timeouts, and explicit degraded mode responses when optional dependencies fail.
7. **Performance & Benchmarking**: Unit and integration tests verify correctness.
   - *Gap*: No repeatable load and latency benchmarking suite measuring p50, p95, p99 latencies for ingestion, ML inference, RCA, and investigation.
8. **End-to-End Scenario CLI**: Fault injection tests exist in unit tests.
   - *Gap*: No standalone, repeatable CLI tool executing a complete simulated incident lifecycle from fault onset to remediation verification.
9. **CI/CD & Deployment**: GitHub Actions runs lint, type-check, migrations, and pytest.
   - *Gap*: Lacks container build verification, smoke evaluation gates, and configuration validation.

---

## 3. Detailed Phase 6 Implementation Modules

### Module 1: Unified Observability & Correlation (`app.observability`)
- **Distributed Pipeline Tracer (`app.observability.tracer`)**:
  - Implement an in-memory, thread-safe `PipelineTracer` that manages `TraceSpan` contexts.
  - Propagates `correlation_id`, `trace_id`, and `parent_span_id` across:
    1. Ingestion & persistence
    2. Feature generation & quality checks
    3. Anomaly detection & risk prediction
    4. Topology generation & RCA ranking
    5. LangGraph node execution & tool calls
    6. Runbook retrieval
    7. Policy evaluation
    8. Simulation execution & outcome verification
  - Context manager `tracer.span(name, tags={...})` capturing duration, error categories, and status.
  - Safe sanitization: zero secrets, raw prompts, or unbounded strings.
- **Enterprise Metrics Registry (`app.observability.metrics`)**:
  - Expand to include low-cardinality Prometheus-compatible counters, gauges, and histograms.
  - Metrics covered:
    - HTTP request latency & status counts
    - Telemetry ingestion rates, lag, and rejection counts
    - Feature extraction latency and failure count
    - Anomaly counts by detector (`threshold`, `rolling_mad`, `isolation_forest`) and service
    - Prediction latency distribution and model loading failures
    - RCA calculation latency and abstention count
    - Agent investigation duration, step count, and tool execution counts/errors
    - Citation validation failures
    - Remediation policy decisions (`ALLOW`, `DENY`, `APPROVAL_REQUIRED`)
    - Simulation execution status and verification mismatch counts
    - Database connection failures and query durations
    - Data drift and schema deviation alerts
  - Expose `/metrics` endpoint with Prometheus text format and JSON snapshot.
- **Enhanced Readiness Probes (`app.api.v1.health`)**:
  - Expand `/health/ready` to check:
    - PostgreSQL connection pool health
    - Required model artifacts (`isolation_forest.joblib`, `incident_predictor.joblib`)
    - LLM provider status (reported as `optional_provider_available: true/false`, does not fail readiness)
    - Returns HTTP 200 when ready, HTTP 503 with ProblemDetails when critical dependencies fail.

### Module 2: Continuous Evaluation Pipeline (`app.evaluation.continuous`)
- **Unified Evaluation Suite (`scripts/evaluate_continuous.py` & `app.evaluation.continuous`)**:
  - Orchestrates:
    1. **ML Detection & Risk Prediction**: Precision, Recall, F1, false-positive rate, detection delay, ROC-AUC where valid, latency per fault scenario.
    2. **RCA Ranking**: Top-1 accuracy, Top-3 accuracy, Mean Reciprocal Rank (MRR), abstention correctness.
    3. **Agent Investigation**: Citation validity rate, claim grounding, Phase 4 preservation rate, abstention correctness, policy compliance, simulator verification, adversarial resilience.
  - Runs fully offline without live LLM credentials using deterministic fixtures.
  - Produces versioned JSON artifact (`ml/artifacts/continuous_evaluation_report.json`) and Markdown summary (`docs/PHASE_6_EVALUATION.md`).

### Module 3: Data Quality & Drift Monitoring (`app.ml.drift` & `app.telemetry.quality`)
- **Telemetry Quality Monitor**:
  - Checks for incoming timestamp staleness (max allowable skew), missing values, unexpected metric ranges, and schema deviations.
- **Statistical Feature Drift Detector**:
  - Implements lightweight Population Stability Index (PSI) and z-score baseline comparisons against baseline training statistics.
  - Configurable drift thresholds (`warning`: PSI > 0.1, `critical`: PSI > 0.25).
  - Explicit conceptual separation in reports and metrics:
    - `DATA_QUALITY_ANOMALY`: Malformed or missing telemetry inputs.
    - `INPUT_FEATURE_DRIFT`: Shift in feature distribution relative to baseline.
    - `PREDICTION_DRIFT`: Shift in risk prediction or anomaly frequency.
    - `CONFIRMED_PERFORMANCE_DEGRADATION`: Verified reduction in ground-truth metrics (only when labels exist).

### Module 4: Platform Versioning & Manifest (`app.core.versioning`)
- **Centralized System Manifest**:
  - Defines `PlatformManifest` capturing:
    - Platform version (`0.1.0-phase6`)
    - Model artifact hashes, training window, and feature schema version
    - Detector thresholds and rolling window settings
    - RCA graph heuristic version
    - Agent LangGraph workflow version & prompt template version
    - Tool catalog schemas
    - Runbook catalog version
    - Evaluation suite version
  - Embeds manifest identifier in investigation reports and API system responses (`/api/v1/system/info`).
- **Model Artifact Integrity Validator**:
  - Validates artifact checksums, metadata schema compatibility, and model attributes before loading.
  - Explicit errors and fallback handling for corrupted artifacts.

### Module 5: Resilience & Graceful Degradation (`app.core.resilience`)
- **Resilience Engine**:
  - Bounded exponential backoff retry decorator with jitter for transient database queries.
  - Safe execution wrappers with bounded timeouts for external tool queries.
  - Explicit fallback patterns:
    - Missing telemetry → Explicit abstention (`INSUFFICIENT_EVIDENCE`).
    - Offline LLM provider → Automatic deterministic rule-based fake provider.
    - Missing model artifact → Graceful degradation to statistical baselines with degraded readiness warnings.
    - Simulation verification failure → Explicit `SIMULATION_VERIFICATION_FAILED` status, never reported as success.

### Module 6: Repeatable Performance & Load Testing (`scripts/run_benchmarks.py`)
- **Benchmarking Suite**:
  - Measures p50, p95, p99 latencies, throughput (RPS), and error rates across:
    1. Telemetry ingestion batches
    2. Telemetry time-window queries
    3. ML model feature extraction & inference
    4. Topology extraction & RCA ranking
    5. Investigation graph execution
    6. Concurrent read-only API health & system endpoints
  - Configurable concurrency (1, 5, 10 workers) and warm-up cycles.
  - Saves reproducible benchmark results to `ml/artifacts/performance_benchmark_report.json`.

### Module 7: End-to-End Scenario Runner CLI (`scripts/run_scenario.py`)
- **Automated Scenario CLI**:
  - Steps:
    1. Dependency & readiness verification
    2. Fault scenario selection/injection via `FaultInjectionEngine`
    3. Telemetry ingestion into repository
    4. Anomaly detection & risk scoring
    5. Root-cause analysis ranking
    6. LangGraph agent investigation
    7. Grounded report generation
    8. Policy evaluation
    9. Simulation execution
    10. State verification & cleanup
  - Supports `--mode fixture` (fast offline) and `--mode live-sim` (in-memory simulation).
  - Emits JSON run summary and terminal report.

### Module 8: CI/CD & Production Hardening
- **CI Enhancements (`.github/workflows/ci.yml`)**:
  - Adds Ruff formatting check, strict mypy type checking, Alembic migration verification, deterministic evaluation smoke test, and Docker build test.
- **Docker & Compose Hardening**:
  - Ensure unprivileged container execution (`USER aegisops`).
  - Container healthchecks against `/health/ready`.
  - Comprehensive `.env.example` with documented configuration placeholders.

---

## 4. Safety, Security & Non-Negotiables
1. **Zero Production Remediation**: Remediation is strictly restricted to `FaultInjectionEngine` in-memory scenario states.
2. **Untrusted Telemetry Separation**: All logs, traces, and runbooks are encapsulated in JSON containers and never executed as code.
3. **Deterministic Policy Boundary**: Policy decisions remain 100% deterministic code.
4. **Offline Test Execution**: Complete test suite, CI, and evaluation pipeline run without internet or paid API keys.
5. **No Data Leakage / Synthetic Labeling**: Benchmark results are explicitly labeled as synthetic/simulated.

---

## 5. Acceptance Criteria & Definition of Done
- [x] All 149 existing tests pass with 0 regressions.
- [x] Distributed tracing and correlation IDs propagate end-to-end.
- [x] Prometheus metrics endpoint `/metrics` exposes bounded-cardinality counters and latencies.
- [x] Enhanced `/health/ready` validates database and model readiness.
- [x] Data quality and feature drift monitoring tracks schema and distribution shifts.
- [x] Platform manifest and artifact validator ensure reproducible version tracking.
- [x] Resilience wrappers provide bounded retries and explicit graceful degradation.
- [x] Performance benchmarking measures reproducible p50/p95/p99 latencies.
- [x] End-to-end scenario runner (`scripts/run_scenario.py`) executes full lifecycle with cleanup.
- [x] Dockerfile and CI workflow validated.
- [x] Comprehensive documentation in `docs/PHASE_6_*.md`.
