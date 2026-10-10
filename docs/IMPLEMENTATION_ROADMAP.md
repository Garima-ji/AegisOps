# AegisOps — Implementation Roadmap

This roadmap outlines the incremental, testable phases for constructing the **AegisOps** platform. Each phase represents a self-contained, verifiable increment of capability, adhering strictly to test-driven and quality-first engineering principles.

---

## Roadmap Overview

```
+---------------------------------------------------------------------------------+
| Phase 1: Project Foundation & Architecture (Current)                            |
| Modular Layout | Settings | Logging | Health APIs | PostgreSQL | Migrations | CI |
+----------------------------------------+----------------------------------------+
                                         |
+----------------------------------------v----------------------------------------+
| Phase 2: Telemetry Ingestion, Validation & Normalization                        |
| Metrics | Structured Logs | Traces | Service Topologies | Ingestion Buffers     |
+----------------------------------------+----------------------------------------+
                                         |
+----------------------------------------v----------------------------------------+
| Phase 3: Statistical Baselines & ML Anomaly Detection                           |
| Z-score | EWMA | MAD | Scikit-learn Isolation Forest | Baseline Persistence     |
+----------------------------------------+----------------------------------------+
                                         |
+----------------------------------------v----------------------------------------+
| Phase 4: Topology Graph Correlation & Root Cause Ranking                        |
| Service Dependency Graph | Anomaly Clustering | Patient-Zero Root Cause Ranking |
+----------------------------------------+----------------------------------------+
                                         |
+----------------------------------------v----------------------------------------+
| Phase 5: Incident Management & SLO Risk Forecasting                             |
| Incident State Machine | Trend Regression | Burn Rate & SLO Breach Prediction  |
+----------------------------------------+----------------------------------------+
                                         |
+----------------------------------------v----------------------------------------+
| Phase 6: Controlled Agentic AI Investigation (LangGraph)                        |
| Sandboxed Tools | Runbook RAG | Evidence Grounding | LLM Fallback Tolerance     |
+----------------------------------------+----------------------------------------+
                                         |
+----------------------------------------v----------------------------------------+
| Phase 7: Remediation Policy Gateway & Execution Simulation                      |
| Allowlist Engine | Simulation Sandbox | Rollback Assertions | Audit Ledger      |
+----------------------------------------+----------------------------------------+
                                         |
+----------------------------------------v----------------------------------------+
| Phase 8: Full-Stack Observability Dashboard & Production Hardening               |
| Next.js Frontend | Apache ECharts | Prometheus/Loki/Jaeger Export | Load Tests  |
+---------------------------------------------------------------------------------+
```

---

## Phase 1: Project Foundation & Architecture (Active Phase)

### Focus & Objectives
- Establish the modular repository structure, environment configuration, and dependency management.
- Implement robust settings validation, structured JSON logging with request tracing, and standardized RFC 7807 error handling.
- Deliver core health endpoints (`/health/live`, `/health/ready`, `/api/v1/system/info`).
- Initialize PostgreSQL database engine, Alembic migration harness, and baseline system/audit models.
- Set up automated testing (Pytest), code formatting and linting (Ruff), static typing (Mypy), and CI pipeline (GitHub Actions).

### Deliverables
- `pyproject.toml` with pinned, compatible dependencies and tool configs.
- `backend/app/core/config.py` (Pydantic Settings with validation).
- `backend/app/core/logging.py` (Structured JSON logging).
- `backend/app/core/exceptions.py` (RFC 7807 exception models and handlers).
- `backend/app/db/session.py` (SQLAlchemy 2.0 engine and session factory).
- `backend/app/api/v1/` routers for health and system information.
- `backend/migrations/` (Alembic configuration and baseline migration).
- Automated unit and integration test suite (`tests/`).
- `Dockerfile` & `docker-compose.yml`.
- `.github/workflows/ci.yml`.

### Verification & Testing
- Unit tests validating config defaults, overrides, and invalid value rejections.
- Integration tests asserting `/health/live` returns 200 and `/health/ready` validates database connectivity.
- Ruff and Mypy passing with zero warnings or errors.
- Alembic upgrade/downgrade migration cycle runs cleanly.

---

## Phase 2: Telemetry Ingestion, Validation & Normalization

### Focus & Objectives
- Create high-throughput ingestion endpoints for application metrics, structured logs, distributed trace spans, and service topology graphs.
- Enforce strict Pydantic validation: timestamp monotonicity, schema bounds, and input sanitization.
- Support batching, in-memory buffering, and structured dead-letter logging for malformed telemetry.

### Deliverables
- `backend/app/schemas/telemetry.py` (MetricPoint, LogRecord, SpanRecord, DependencyEdge).
- `backend/app/ingestion/` (Ingestion service, batch validator, sanitization rules).
- `backend/app/api/v1/telemetry.py` (Ingestion endpoints).
- Database tables for telemetry points and dependency graph edges.

### Verification & Testing
- Property-based tests verifying timestamp ordering and validation rules.
- Negative tests asserting malformed payloads are rejected with descriptive 422 errors.
- Throughput benchmarks verifying memory efficiency under high ingest rates.

---

## Phase 3: Statistical Baselines & ML Anomaly Detection

### Focus & Objectives
- Implement deterministic, low-latency statistical baseline detectors: Rolling Z-Score, EWMA (Exponentially Weighted Moving Average), and MAD (Median Absolute Deviation).
- Implement offline ML training and online inference using Scikit-learn (Isolation Forest) for multi-metric anomaly detection.
- Decouple ML training from real-time inference; store model weights, scaler parameters, and evaluation metrics in versioned artifacts.

### Deliverables
- `backend/app/detection/statistical.py` (EWMA, Z-Score, MAD detectors).
- `backend/app/detection/ml.py` (Isolation Forest inference wrapper).
- `ml/training/train_anomaly_detector.py` (Deterministic training script with fixed random seed).
- `ml/evaluation/evaluate_models.py` (Precision, Recall, ROC-AUC benchmarks on benchmark telemetry).
- Model artifact storage and serialization via Joblib.

### Verification & Testing
- Deterministic test cases with synthetic sinusoidal and step-function metric spikes.
- Model reproducibility test verifying identical model hash given identical seed and dataset.
- Zero-LLM verification asserting detection operates fully offline.

---

## Phase 4: Service Topology, Incident Correlation & Root Cause Ranking (Completed)

### Focus & Objectives
- Construct directed service dependency graphs (`caller -> downstream`) from distributed trace parent-child span trees with cycle safety and depth bounding.
- Version and persist topology snapshots (`topology_snapshots`, `topology_edges`) with call counts, error rates, and p50/p95 latency percentiles.
- Implement sliding-window temporal and topological anomaly correlation engine (`AnomalyCorrelator`).
- Implement deterministic multi-factor root-cause ranking baseline ($S = 0.35 S_{\text{origin}} + 0.30 S_{\text{temp}} + 0.20 S_{\text{impact}} + 0.15 S_{\text{sev}}$) with leaf error callee detection and stable tie-breaking.
- Generate machine-readable evidence structures (`CandidateEvidence`) with positive and contradictory indicators and explicit abstention.
- Benchmark ranking accuracy against Phase 2 fault-injection ground truth with strict label segregation (`scripts/evaluate_root_cause.py`).

### Deliverables
- `backend/app/topology/models.py`, `extractor.py`, `service.py`: Graph representation, trace extractor, and snapshot service.
- `backend/app/rca/schemas.py`, `correlator.py`, `ranker.py`, `service.py`, `evaluation.py`: Anomaly clustering, deterministic ranking, persistence, and evaluation benchmark.
- `backend/app/api/v1/topology.py`, `backend/app/api/v1/rca.py`: REST endpoints for topology and incident root-cause analysis.
- `backend/migrations/versions/004_topology_and_root_cause.py`: Additive Alembic database schema migration.
- `scripts/evaluate_root_cause.py`: Reproducible CLI evaluation benchmark comparing multi-factor ranker vs earliest-anomaly baseline.
- `docs/SERVICE_TOPOLOGY.md`, `docs/ROOT_CAUSE_RANKING.md`, `docs/PHASE_4_EVALUATION_REPORT.md`, `docs/PHASE_4_PLAN.md`.

### Verification & Testing
- 122 automated unit and integration tests passing (`pytest`).
- Benchmark evaluation achieving 100.0% Top-1 accuracy vs 0.0% for earliest-anomaly baseline across fault scenarios (`latency_spike`, `error_surge`, `service_timeout`).
- 100% strict type checking (`mypy backend/app`) and linting (`ruff check .`, `ruff format --check .`) clean.


---

## Phase 5: Incident Management & SLO Risk Forecasting

### Focus & Objectives
- Build incident lifecycle state machine (Triggered -> Acknowledged -> Investigating -> Remediating -> Resolved -> Postmortem).
- Implement time-series forecasting (trend regression, burn rate extrapolation) to predict time until SLO budget exhaustion.

### Deliverables
- `backend/app/schemas/incident.py` and `backend/app/db/models/incident.py`.
- `backend/app/forecasting/slo.py` (Error budget burn rate, linear/polynomial regression).
- `backend/app/services/incident_service.py` (Lifecycle management & alerting hooks).

### Verification & Testing
- State machine transition tests verifying illegal state jumps are rejected.
- Forecasting accuracy tests on synthetic degradations with known depletion times.

---

## Phase 6: Controlled Agentic AI Investigation (LangGraph)

### Focus & Objectives
- Build a stateful incident investigation workflow using LangGraph.
- Expose explicitly registered, read-only tools:
  - `TelemetryReaderTool`: Fetches bounded metric ranges.
  - `LogSearchTool`: Queries structured log errors for specific service and time window.
  - `TraceLookupTool`: Retrieves trace waterfall and high-latency spans.
  - `RunbookRAGTool`: Retrieves relevant runbooks based on semantic embeddings.
- Implement strict evidence-grounding constraints: every claim in the generated incident report must cite a concrete telemetry timestamp or log ID.
- Ensure graceful degradation: if LLM API is unavailable, emit structured deterministic summary based on Phase 3 and Phase 4 outputs.

### Deliverables
- `backend/app/agent/graph.py` (LangGraph investigation state machine).
- `backend/app/agent/tools.py` (Constrained tool implementations).
- `backend/app/agent/prompts.py` (System prompts enforcing strict evidence grounding).
- `backend/app/agent/fallback.py` (Deterministic summary generator).

### Verification & Testing
- Mock LLM tests validating state transitions, tool call limits, and error handling.
- Graceful degradation tests asserting report generation when LLM throws network/auth exceptions.
- Prompt injection resistance tests ensuring unescaped log strings do not hijack tool execution.

---

## Phase 7: Remediation Policy Gateway & Execution Simulation

### Focus & Objectives
- Define safe, allowlisted remediation actions:
  - `RestartPod`
  - `ScaleReplicas` (bounded: min 1, max 10)
  - `RollbackDeployment`
  - `FlushCache`
  - `ClearCircuitBreaker`
- Build a policy gateway evaluating action preconditions, parameter bounds, and authorization levels.
- Provide a simulated execution sandbox that models environment responses and tests automated rollback before production verification.
- Maintain an append-only audit ledger recording operator approvals, simulation outputs, and rollback states.

### Deliverables
- `backend/app/remediation/policies.py` (Policy rules and parameter constraints).
- `backend/app/remediation/simulator.py` (Simulated sandbox environment).
- `backend/app/remediation/gateway.py` (Action validation, execution, and rollback engine).
- `backend/app/db/models/audit.py` (Immutable audit logging).

### Verification & Testing
- Security tests ensuring un-allowlisted or out-of-bound actions are rejected.
- Simulation tests verifying automatic rollback execution when post-action checks fail.
- Audit ledger verification ensuring complete event traceability.

---

## Phase 8: Full-Stack Observability Dashboard & Production Hardening

### Focus & Objectives
- Build Next.js 14+ frontend with TypeScript and Apache ECharts.
- Render dynamic service dependency graphs, live telemetry charts, incident investigation timelines, and remediation approval consoles.
- Integrate OpenTelemetry collectors, Prometheus metrics scraping, and Grafana dashboard templates.
- Perform end-to-end load testing, security vulnerability scans, and deployment readiness reviews.

### Deliverables
- `frontend/` (Next.js dashboard).
- `telemetry/` & `monitoring/` (OpenTelemetry and Prometheus/Grafana configs).
- End-to-end integration test suite and load testing scripts (`scripts/load_test.py`).

### Verification & Testing
- Cypress / Playwright frontend user flow testing.
- End-to-end incident lifecycle simulation from telemetry ingestion to simulated remediation.
- Security and penetration assessment.
