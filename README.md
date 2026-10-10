# AegisOps

**AegisOps** is a production-oriented, AI-powered IT operations intelligence (AIOps) platform engineered to ingest telemetry, detect multi-dimensional anomalies, correlate cross-service incidents, and orchestrate safe, policy-gated investigations and remediations.

[![CI](https://github.com/aegisops/aegisops/actions/workflows/ci.yml/badge.svg)](https://github.com/aegisops/aegisops/actions/workflows/ci.yml)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/release/python-3120/)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Type Checked: mypy](https://img.shields.io/badge/type%20checked-mypy-blue.svg)](https://mypy-lang.org/)
[![Test Coverage: 92%](https://img.shields.io/badge/coverage-92%25-brightgreen.svg)](docs/PHASE_2_TEST_REPORT.md)

---

## 1. Architectural Philosophy & Planned Workflow

Modern distributed cloud environments produce massive volumes of metrics, structured logs, and distributed traces. AegisOps unifies this operational data into a deterministic, AI-assisted lifecycle:

```
[Telemetry Ingestion] -> [Validation & Normalization] -> [PostgreSQL Telemetry Persistence]
         |
         v
[Statistical & ML Anomaly Detection (Zero-LLM Fast Path - Phase 3)]
         |
         v
[Topology Graph Correlation & Patient-Zero Root Cause Ranking (Phase 4)]
         |
         v
[Predictive SLO Risk & Incident State Machine (Phase 5)]
         |
         v
[Controlled Stateful Agentic Investigation (LangGraph - Phase 6)]
         |
         v
[Remediation Policy Gateway & Safe Simulation (Phase 7)]
         |
         v
[Immutable Audit Ledger & Operator Dashboard (Phase 8)]
```

### Key Principles
- **LLM Decoupled Fast Path**: Ingestion, statistical baseline anomaly detection, and basic alerting operate 100% offline without any dependency on external LLM availability.
- **Strict Evidence Grounding**: The investigation agent is strictly forbidden from making ungrounded claims; every finding must cite raw telemetry timestamps, log IDs, or metric spikes.
- **Safety by Design**: Zero arbitrary code execution or raw SQL generation. Remediation actions and fault-injection experiments are strictly allowlisted, parameter-checked, and safely terminated.

---

## 2. Features by Phase

- **Phase 1: Project Foundation & Architecture (Completed)**
  - Modular monorepo architecture (`backend`, `ml`, `docs`, `telemetry`).
  - Typed settings management (`pydantic-settings`) with strict validation.
  - Structured JSON logging with request correlation IDs and context propagation.
  - Standardized RFC 7807 problem details error handling.
  - Liveness (`GET /health/live`), readiness (`GET /health/ready`), and metadata (`GET /api/v1/system/info`) endpoints.
  - PostgreSQL integration via SQLAlchemy 2.0 with connection pooling and health checking.
  - Alembic database migration workflow.
  - Docker & Docker Compose configuration.
  - GitHub Actions CI workflow for linting, type-checking, migrations, and tests.

- **Phase 2: Telemetry Ingestion, Persistence & Controlled Fault Injection (Completed)**
  - **Bounded Ingestion Pipelines**: Pydantic v2 validated batch APIs for metrics (`POST /api/v1/telemetry/metrics`), structured logs (`POST /api/v1/telemetry/logs`), and distributed trace spans (`POST /api/v1/telemetry/traces`).
  - **OpenTelemetry Normalization**: UTC timestamp enforcement, standard metric units (`ms`, `count`, `percent`), bounded dimensions (max 32 attributes), 32-hex `trace_id`, and 16-hex `span_id` preservation.
  - **Time-Bounded Telemetry Queries**: Paginated queries with composite database indexes for metrics, logs, spans, and hierarchical waterfall trees (`GET /api/v1/telemetry/traces/{trace_id}`).
  - **Service Registry**: Discovered microservice inventory with idempotent auto-upsert and health tracking (`GET /api/v1/services`).
  - **Cooperating Sample Microservices**: Lightweight `order-service` -> `payment-service` workflow emitting correlated inter-service traces, metrics, and structured logs.
  - **Controlled Fault Injection Framework**: Strictly bounded local scenarios (`latency_spike`, `error_surge`, `service_timeout`) restricted to allowlisted services with zero arbitrary shell execution.
  - **Isolated Ground-Truth Labels**: Stored in `fault_labels` to provide clean evaluation labels for future ML models without feature leakage (`GET /api/v1/experiments/{id}/labels`).
  - **Pipeline Observability**: Low-cardinality in-memory operational metrics and batch duration tracking (`GET /api/v1/telemetry/pipeline-metrics`).

- **Phase 3: ML Detection, Incident Prediction & Evaluation (Completed)**
  - **Leakage-Safe Feature Engineering**: 15 canonical operational metrics (`STANDARD_FEATURE_NAMES`) windowed chronologically with strict $t \le T$ future observation cutoff.
  - **Deterministic Statistical Baselines**: Configurable Threshold anomaly detector and non-parametric Rolling MAD with Modified Z-score and zero-variance flatline protection.
  - **Unsupervised Isolation Forest**: Production-grade Scikit-learn Isolation Forest with median imputation, standard scaling, calibrated $[0.0, 1.0]$ anomaly scoring, and top contributing feature attribution.
  - **Supervised Incident-Risk Prediction**: Forward-looking probability estimation ($H = 300\,\text{s}$) with embargo buffer ($E = 60\,\text{s}$) and automatic safety guard suppressing training when positive labels $< 5$.
  - **Model Registry & Persistence**: Database persistence (`ml_models`, `anomaly_detections`), joblib serialization, and versioned JSON evaluation benchmarking (`ml/artifacts/evaluation_report.json`).
  - **Typed Inference APIs**: Versioned endpoints for running detection, querying historical anomalies, inspecting registered models, and predicting incident risk.
  - **Automated CLI Pipelines**: Data readiness auditing (`scripts/audit_telemetry_data.py`) and training/evaluation pipeline (`scripts/train_models.py`).

- **Phase 4: Service Topology, Incident Correlation & Root-Cause Ranking (Completed)**
  - **Distributed Trace Graph Extraction**: Directed service dependency graph (`caller -> downstream`) reconstructed dynamically from parent-child span trees with cycle safety and depth bounding.
  - **Versioned Topology Persistence**: Database persistence (`topology_snapshots`, `topology_edges`) with edge metrics (call counts, error rates, p50/p95 latency percentiles).
  - **Sliding-Window Anomaly Correlation**: Multi-service incident clustering grouping temporal detections ($\le 180\,\text{s}$) with topological sub-graph partitioning.
  - **Deterministic Multi-Factor RCA Ranker**: Transparent scoring baseline combining call/trace leaf origin ($S_{\text{origin}}$), temporal onset precedence ($S_{\text{temp}}$), cascade reach ($S_{\text{impact}}$), and anomaly severity ($S_{\text{sev}}$).
  - **Machine-Readable Explanations**: Fact-grounded evidence structures (`CandidateEvidence`) reporting leaf error callee proof, caller impact, positive indicators, and uncertainty factors without LLM dependency.
  - **Ground-Truth Segregated Benchmark**: Evaluation harness (`scripts/evaluate_root_cause.py`) measuring Top-1/Top-3 accuracy, MRR, diagnosis latency, and abstention against Phase 2 fault injections.
  - **Topology & RCA REST APIs**: `POST /api/v1/topology/rebuild`, `GET /api/v1/topology`, `GET /api/v1/topology/services/{name}/dependencies`, `POST /api/v1/incidents/root-cause/analyze`, `GET /api/v1/incidents/root-cause/{id}`.

- **Phase 5: Agentic Incident Investigation, Grounded Diagnosis & Safe Remediation (Completed)**
  - **LangGraph State Machine**: Bounded 12-node orchestration graph (`initialize` -> `gather` -> `assess` -> `correlate` -> `runbook` -> `validate` -> `recommend` -> `policy` -> `simulate` -> `report` -> `finalize`).
  - **Typed Bounded Tool Layer**: Parameter-validated, rate-bounded tools for anomaly querying, metrics aggregation, log retrieval, trace waterfall inspection, topology traversal, RCA candidate queries, and local runbooks.
  - **Grounded Evidence & Citation Validation**: Immutable evidence record IDs (`ev-{type}-{service}-{hash}`) with strict verification preventing hallucinated assertions.
  - **Adversarial Telemetry Defense**: Structured JSON container encapsulation ensuring telemetry log strings or trace attributes cannot hijack prompts or execute arbitrary code.
  - **Deterministic Safety Policy Engine**: Code-level allowlists (`order-service`, `payment-service`, `inventory-service`, `notification-service`) and approval requirements (`ALLOW`, `DENY`, `REQUIRES_APPROVAL`).
  - **Safe Simulation Boundary**: Zero live production mutations; simulated remediations execute strictly against `FaultInjectionEngine` with pre- and post-state verification.
  - **Database Persistence & Audit Ledger**: Migration `005_investigations` with `investigations`, `investigation_events`, and `remediation_proposals` tables.
  - **Investigation APIs**: `POST /api/v1/investigations`, `GET /api/v1/investigations/{id}`, `GET /api/v1/investigations/{id}/report`, `GET /api/v1/investigations/{id}/events`, `/remediation/dry-run`, `/remediation/approve`, `/remediation/execute-simulation`.
  - **Reproducible Evaluation Benchmark**: 13 operational test scenarios evaluated via `scripts/evaluate_investigations.py` with 100% pass rate.

- **Phase 6: Production Hardening, Observability, Continuous Evaluation & Deployment (Completed)**
  - **Unified Distributed Tracing**: Thread-safe `PipelineTracer` with `contextvars` context propagation, span hierarchy, timing, and automated sensitive credential redaction.
  - **Low-Cardinality Prometheus Metrics**: In-memory registry with strict label validation (`service`, `status`, `method`, `endpoint`) and percentile latency distributions exposed at `/metrics` and `/api/v1/observability/metrics`.
  - **Reliable Liveness & Readiness Probes**: Strict separation between process liveness (`/health/live`) and dependency readiness (`/health/ready`), inspecting PostgreSQL, required ML artifacts, and optional LLM providers.
  - **Data Quality & Statistical Drift Monitoring**: Lightweight, testable `DriftMonitor` implementing a four-tier operational taxonomy (`DATA_QUALITY_ISSUE`, `INPUT_FEATURE_DRIFT`, `PREDICTION_DRIFT`, `CONFIRMED_PERFORMANCE_DEGRADATION`) and Population Stability Index (PSI) with Laplace add-1 smoothing.
  - **Platform & Model Versioning Manifest**: Standardized `PlatformManifest` and `ArtifactValidator` checking cryptographic SHA-256 digests and schema compatibility at `GET /system/manifest`.
  - **Resilience & Graceful Degradation**: Bounded exponential backoff with jitter (`retry_with_backoff`) strictly rejecting non-idempotent operations, and deterministic fallback handlers.
  - **End-to-End Incident Scenario Runner**: CLI runner (`scripts/run_scenario.py`) executing all 11 stages of the incident lifecycle from fault injection to outcome verification in < 1.0s.
  - **Repeatable Performance Benchmarking Suite**: Automated suite (`scripts/run_benchmarks.py`) measuring p50/p95/p99 latency and throughput (ML inference: 206k RPS, RCA: 172.5 RPS, LangGraph: 58.9 RPS).
  - **Continuous Evaluation Pipeline**: Unified evaluation harness (`scripts/evaluate_continuous.py`) reporting ML metrics, RCA accuracy, agent safety compliance, and drift rules.

---

## 3. Directory Structure

```
AegisOps/
├── backend/
│   ├── app/
│   │   ├── api/             # HTTP route controllers
│   │   │   └── v1/          # Health, System, Telemetry, Services, Experiments, ML, RCA, Investigations, Observability
│   │   ├── core/            # Config, logging, exceptions, versioning, resilience
│   │   ├── db/              # SQLAlchemy models, session, engine, declarative base
│   │   │   └── models/      # Telemetry, Experiments, ML, Topology, Investigations
│   │   ├── schemas/         # Pydantic v2 schemas
│   │   ├── services/        # Registry, Telemetry, Experiment services
│   │   ├── ingestion/       # Telemetry generator, sample app, fault injection engine
│   │   ├── ml/              # Features, Detectors, Drift monitoring
│   │   ├── rca/             # Causal topology & deterministic ranker
│   │   ├── agent/           # LangGraph investigation agent, tools, safety policy, simulator
│   │   ├── observability/   # Pipeline tracer, low-cardinality Prometheus metrics
│   │   └── main.py          # FastAPI application factory & middleware
│   ├── tests/
│   │   ├── unit/            # Unit tests across Phases 1–6
│   │   ├── integration/     # Integration tests across all APIs and lifecycles
│   │   └── conftest.py      # Pytest fixtures & isolated DB configuration
│   └── migrations/          # Alembic database migrations (001–005)
├── docs/                    # Architecture, plans, reviews, and evaluation reports
├── ml/                      # Serialized model artifacts, evaluation reports, benchmark records
├── scripts/                 # CLI utilities (train, audit, evaluate, benchmark, run_scenario)
├── docker-compose.yml       # Local development stack (Backend + PostgreSQL)
├── Dockerfile               # Multi-stage production container build
├── pyproject.toml           # Project dependencies, Ruff, Mypy, and Pytest config
└── README.md
```

---

## 4. API Endpoints Quick Reference

| Method | Endpoint | Description | Phase |
| :--- | :--- | :--- | :--- |
| `GET` | `/health/live` | Process liveness probe | Phase 1 |
| `GET` | `/health/ready` | Full dependency readiness probe (PostgreSQL, ML models) | Phase 1/6 |
| `GET` | `/metrics` | Standard Prometheus metrics exposition | Phase 6 |
| `GET` | `/system/manifest` | Platform provenance and version manifest | Phase 6 |
| `GET` | `/api/v1/observability/metrics` | JSON format operational metrics | Phase 6 |
| `GET` | `/api/v1/observability/spans` | Recent distributed trace spans | Phase 6 |
| `POST` | `/api/v1/telemetry/metrics` | Ingest batch of numeric metrics | Phase 2 |
| `POST` | `/api/v1/telemetry/logs` | Ingest batch of structured logs | Phase 2 |
| `POST` | `/api/v1/telemetry/traces` | Ingest batch of distributed trace spans | Phase 2 |
| `GET` | `/api/v1/telemetry/traces/{trace_id}` | Hierarchical trace waterfall span sequence | Phase 2 |
| `POST` | `/api/v1/experiments` | Launch controlled fault-injection scenario | Phase 2 |
| `POST` | `/api/v1/experiments/{id}/stop` | Safely terminate active experiment & cleanup | Phase 2 |
| `POST` | `/api/v1/ml/anomaly-detection/run` | Execute threshold, MAD, and Isolation Forest detectors | Phase 3 |
| `POST` | `/api/v1/ml/incident-risk/predict` | Predict incident risk probability within forward horizon | Phase 3 |
| `POST` | `/api/v1/topology/rebuild` | Reconstruct dynamic service topology from traces | Phase 4 |
| `POST` | `/api/v1/incidents/root-cause/analyze` | Run deterministic root-cause ranking algorithm | Phase 4 |
| `POST` | `/api/v1/investigations` | Launch LangGraph agentic incident investigation | Phase 5 |
| `GET` | `/api/v1/investigations/{id}/report` | Retrieve grounded incident investigation report | Phase 5 |
| `POST` | `/api/v1/investigations/{id}/remediation/execute-simulation` | Execute policy-gated safe remediation simulation | Phase 5 |

---

## 5. Local Setup & Execution Instructions

### 5.1 Virtual Environment Setup

```bash
# Clone the repository
git clone https://github.com/aegisops/aegisops.git
cd AegisOps

# Create a Python 3.12 virtual environment
python -m venv .venv

# Activate virtual environment
# Windows (PowerShell):
.\.venv\Scripts\Activate.ps1
# Linux / macOS:
source .venv/bin/activate

# Upgrade pip & install package in editable mode with development dependencies
pip install --upgrade pip
pip install -e ".[dev]"
```

### 5.2 Database Migrations

Apply Alembic migrations to set up the complete schema:

```bash
alembic -c backend/alembic.ini upgrade head
alembic -c backend/alembic.ini current
```

### 5.3 Running the Application

```bash
python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
```

The interactive documentation will be available at:
- Swagger UI: `http://localhost:8000/docs`
- Prometheus Metrics: `http://localhost:8000/metrics`
- System Manifest: `http://localhost:8000/system/manifest`

### 5.4 End-to-End Incident Scenario Runner CLI
Execute the complete 11-stage incident lifecycle from fault injection to simulated verification:

```bash
# Live simulation mode
python scripts/run_scenario.py --scenario high_latency --target payment-service --mode live-sim

# Offline fixture mode
python scripts/run_scenario.py --scenario error_spike --target payment-service --mode fixture
```

### 5.5 Continuous Evaluation & Performance Benchmarking
```bash
# 1. Run unified continuous evaluation (ML, RCA, Agent, Drift)
python scripts/evaluate_continuous.py

# 2. Run repeatable performance latency/throughput benchmark
python scripts/run_benchmarks.py
```

---

## 6. Running Tests & Quality Verification

```bash
# 1. Run all unit and integration tests
pytest backend/tests

# 2. Strict static type checking (0 issues across 83 source files)
mypy backend/app

# 3. Code formatting and lint checks (100% clean across 128 workspace files)
ruff check .
ruff format --check .
```

All **173 test cases pass** with 100% strict `mypy` compliance and zero `ruff` errors.

