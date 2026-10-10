# AegisOps — Project Plan

## 1. Executive Summary & Project Objectives

**AegisOps** is an enterprise-grade, production-oriented AI-powered IT Operations Intelligence (AIOps) platform. Modern microservice and cloud-native architectures generate high volumes of telemetry—metrics, structured logs, distributed traces, and dynamic topology graphs. Operators face alert fatigue, cascading failure storms, and delayed mean time to resolution (MTTR).

AegisOps addresses these challenges through a unified, hybrid architecture combining:
1. **Deterministic & Statistical Baselines**: Low-latency anomaly detection and metric thresholding that operate with zero LLM dependency.
2. **Machine Learning Anomaly Detection & Correlation**: Unsupervised and semi-supervised ML algorithms to detect multi-dimensional anomalies, cluster correlated telemetry, and rank candidate root causes across service graphs.
3. **Forecasting & Predictive Intelligence**: Early-warning models predicting SLO breaches and capacity exhaustion before service degradation impacts users.
4. **Agentic AI Incident Investigation**: Stateful, multi-step investigation agents governed by LangGraph and strict policy guardrails, utilizing constrained tools to inspect telemetry, run queries, retrieve verified runbooks, and formulate evidence-grounded incident summaries.
5. **Remediation Policy Gateway & Execution Simulation**: Safe, auditable remediation workflows featuring separation of powers (Recommendation -> Policy Validation -> Approval -> Sandbox/Simulated Execution -> Rollback).

### Core Objectives
- **High Availability & Fault Tolerance**: Core ingestion, detection, and basic alerting remain 100% operational even during total LLM provider outages.
- **Strict Evidence Grounding**: The system eliminates hallucinations by requiring citations and raw telemetry references for every agent claim.
- **Safety by Design**: No arbitrary code execution, raw SQL execution, or direct destructive actions on production environments. All remediations are allowlisted, policy-checked, and simulated by default.
- **Extensible Modularity**: Modular monorepo design enabling clear separation of concerns across API routing, business logic, ML inference/training, persistence, and observability.

---

## 2. Project Scope

### In-Scope
- **Telemetry Ingestion & Processing**: High-throughput ingestion of application metrics (Prometheus format / OpenTelemetry metrics), structured JSON logs, distributed traces (OpenTelemetry / Jaeger), and service dependency graphs.
- **Data Validation & Normalization**: Strict Pydantic schemas validating incoming telemetry payloads, enforcing timestamp monotonicity, sanitizing untrusted inputs, and discarding or dead-lettering invalid payloads.
- **Deterministic & Statistical Baselines**: Dynamic rolling statistics (Z-score, EWMA, MAD - Median Absolute Deviation, quantile baselines) providing instant alerting without warm-up delays or external dependencies.
- **ML Anomaly Detection**: Unsupervised models (Isolation Forest, One-Class SVM, autoencoders / PCA where appropriate) trained on historic baselines to detect anomalous telemetry patterns.
- **Event Correlation & Root Cause Ranking**: Topological graph traversal combined with temporal clustering to pinpoint patient-zero services in cascading failure chains.
- **Predictive Risk & Forecasting**: Statistical and ML-driven forecasting (time-series regression, trend analysis) predicting time-to-SLO-exhaustion.
- **Stateful Agentic Investigation**: Structured tool-using investigation agent executing discrete investigation plans, interrogating telemetry databases, and synthesizing findings.
- **Remediation Gateway**: Policy-based rule engine that validates proposed remediation actions against strict safety criteria, supports simulated execution, and tracks rollback plans.
- **Auditability & Observability**: Complete immutable audit logging of model inferences, agent tool calls, approval decisions, and simulated action results.

### Out-of-Scope (Deferred to Future Phases / Explicit Non-Goals)
- **Live Production Mutation**: AegisOps will not execute destructive or unsandboxed actions on live production clusters (e.g., destructive cloud API calls, direct node terminations). Execution is sandboxed/simulated with verified rollback paths.
- **Arbitrary LLM Code Generation**: No unbounded shell interpreters or dynamic Python/SQL execution agents.
- **Full Autonomous Closed-Loop Self-Healing**: Automated remediation without human-in-the-loop approval is strictly restricted; production remediations require explicit policy gating and operator sign-off.

---

## 3. System Architecture & High-Level Components

```
+-----------------------------------------------------------------------------------+
|                                  Clients & Consumers                              |
|   (Web UI - Next.js/ECharts, Monitoring Alerts, Webhook Receivers, CLI, CI/CD)    |
+------------------------------------------+----------------------------------------+
                                           | HTTPS / JSON API
                                           v
+-----------------------------------------------------------------------------------+
|                               AegisOps FastAPI Core                               |
|  +-------------------------+  +--------------------------+  +-------------------+ |
|  |     API Routes (v1)     |  |   Auth & Policy Gateway  |  | Health & Metrics  | |
|  | /telemetry, /incidents, |  |   RBAC, Token Auth,      |  | /health/live      | |
|  | /remediation, /system   |  |   Action Approvals       |  | /health/ready     | |
|  +------------+------------+  +------------+-------------+  +---------+---------+ |
|               |                            |                          |           |
|  +------------v----------------------------v--------------------------v---------+ |
|  |                               Core Domain Services                            | |
|  |  +-------------------+  +-----------------------+  +----------------------+  | |
|  |  | Ingestion Engine  |  |  Statistical Baseline |  | Anomaly Detection ML |  | |
|  |  | Validation & Norm |  |  Z-score, EWMA, MAD   |  | Isolation Forest     |  | |
|  |  +---------+---------+  +-----------+-----------+  +----------+-----------+  | |
|  |            |                        |                         |              | |
|  |  +---------v------------------------v-------------------------v-----------+  | |
|  |  | Correlation & Graph Topology Engine | Incident & Forecasting Engine     |  | |
|  |  | Root Cause Analysis & Ranking      | SLO Risk & Trend Extrapolation    |  | |
|  |  +----------------------------------+--------------------------------------+  | |
|  |                                     |                                        | |
|  |  +----------------------------------v--------------------------------------+  | |
|  |  | Agentic AI Investigation Service (LangGraph, Sandboxed Investigation)    |  | |
|  |  | Tools: TelemetryReader, LogSearch, TraceLookup, RunbookRAG              |  | |
|  |  +----------------------------------+--------------------------------------+  | |
|  |                                     |                                        | |
|  |  +----------------------------------v--------------------------------------+  | |
|  |  | Remediation Policy Gateway & Simulation Engine                          |  | |
|  |  | Allowlist Gating, Action Simulation, Rollback Verification               |  | |
|  +--+----------------------------------+--------------------------------------+--+ |
+----------------------------------------|------------------------------------------+
                                         |
         +-------------------------------+-------------------------------+
         |                               |                               |
         v                               v                               v
+------------------+           +-------------------+           +-------------------+
|    PostgreSQL    |           |   Telemetry Store |           |    Model Cache    |
| (Events, Audits, |           | (Timescale/Prom/  |           | (Joblib Artifacts,|
|  Incidents, RBAC)|           |  Loki / Jaeger)   |           |  Evaluation Logs) |
+------------------+           +-------------------+           +-------------------+
```

---

## 4. Technology Choices & Justification

| Layer / Concern | Technology Selection | Justification & Architectural Trade-offs |
| :--- | :--- | :--- |
| **Language Runtime** | **Python 3.12** | Superior performance improvements over older Pythons, modern typing features (`type` statements, `typing.Self`), robust ML and data science ecosystem. |
| **Web Framework** | **FastAPI + Starlette** | High-performance asynchronous HTTP handling, native Pydantic v2 data validation, automatic OpenAPI documentation, clean dependency injection. |
| **Data Validation** | **Pydantic v2** | Rust-backed speed for parsing high-frequency telemetry, rigorous schema enforcement, custom field sanitizers. |
| **Database & ORM** | **PostgreSQL 16+ / SQLAlchemy 2.0 / Alembic** | Battle-tested relational consistency for incident audit trails, foreign key constraints, JSONB support for semi-structured telemetry, asynchronous SQLAlchemy sessions, deterministic schema migrations. |
| **Statistical & ML** | **NumPy, Pandas, Scikit-learn** | Highly optimized C/Fortran numeric routines, deterministic baselines, Isolation Forest, PCA, standard evaluation metrics (Precision, Recall, ROC-AUC, F1). |
| **Agent Framework** | **LangGraph** (Phased) | Explicit directed acyclic/cyclic state machines for agent execution; provides predictable transitions, checkpointing, and human-in-the-loop pauses compared to opaque loop frameworks. |
| **Observability** | **OpenTelemetry, Prometheus, Loki** | Industry-standard vendor-neutral instrumentation. Built-in `/health/live` and `/health/ready` endpoints with database connection validation. |
| **Testing & Quality** | **Pytest, HTTPX, Ruff, Mypy** | Comprehensive unit/integration testing with async support (`pytest-asyncio`), sub-second linting and formatting via Ruff, static type safety via Mypy. |
| **Containerization** | **Docker & Multi-Stage Dockerfile** | Consistent build artifacts, lightweight production runtime images, reproducible local development via Docker Compose. |
| **CI/CD** | **GitHub Actions** | Automated validation pipeline executing formatting checks, lints, type checks, and unit tests on every commit and pull request. |

---

## 5. Implementation Milestones

- **Milestone 1 (Current Phase 1): Project Foundation & Architecture**
  - Establish modular repository architecture and packaging configuration (`pyproject.toml`).
  - Implement validated settings management via `pydantic-settings`.
  - Build structured logging with correlation IDs and custom exception handlers.
  - Create FastAPI application with `/health/live`, `/health/ready`, and `/api/v1/system/info`.
  - Configure PostgreSQL database connectivity with connection pool testing and Alembic migration harness.
  - Setup Dockerfile, Docker Compose, and CI pipeline (GitHub Actions).
  - Deliver comprehensive unit and integration test suite with >90% coverage on core modules.

- **Milestone 2 (Phase 2): Telemetry Ingestion, Validation & Normalization**
  - Metric, log, trace, and topology ingestion APIs (`/api/v1/telemetry/*`).
  - Ingestion buffer, validation rules, timestamp normalization, and dead-letter handling.
  - In-memory and PostgreSQL persistence for telemetry points.

- **Milestone 3 (Phase 3): Statistical Baselines & ML Anomaly Detection**
  - Rolling Z-score, EWMA, MAD baselines for fast zero-LLM anomaly detection.
  - Unsupervised Scikit-learn models (Isolation Forest) with offline training and online scoring.
  - Anomaly lifecycle management and event generation.

- **Milestone 4 (Phase 4): Topology Graph & Root Cause Correlation**
  - Service dependency graph modeling and traversal.
  - Temporal event clustering and root cause ranking algorithms.

- **Milestone 5 (Phase 5): Incident Management & Risk Forecasting**
  - Incident grouping, severity calculation, and lifecycle states (Triggered, Investigating, Remediating, Resolved).
  - Metric trend regression and SLO breach risk estimation.

- **Milestone 6 (Phase 6): Controlled Agentic Investigation (LangGraph)**
  - Investigation state graph with explicit tool registration and boundary validation.
  - Evidence collection, runbook retrieval (RAG), and deterministic incident reporting.

- **Milestone 7 (Phase 7): Remediation Policy Gateway & Execution Simulation**
  - Safety policy engine, action validation, simulation sandbox, rollback verification.
  - Human-in-the-loop approval workflows.

- **Milestone 8 (Phase 8): Operator Dashboard & Frontend**
  - Next.js application with TypeScript and Apache ECharts visualizing topologies, alerts, incidents, and audit logs.

---

## 6. Acceptance Criteria for Phase 1

1. **Repository Layout**: Clean modular structure separating app, core, api, schemas, db, and tests.
2. **Configuration**: Typed settings loading from `.env` or system environment, with strict validation for required fields, log levels, and database connection strings.
3. **Application Endpoints**:
   - `GET /health/live`: Returns `{"status": "alive", "timestamp": "..."}` with HTTP 200.
   - `GET /health/ready`: Checks database connectivity and returns HTTP 200 when ready, HTTP 503 when degraded.
   - `GET /api/v1/system/info`: Returns sanitized application metadata (version, environment, commit/tag, feature flags) with no leaked secrets.
4. **Error Handling**: Standardized RFC 7807 compliant JSON error responses for 400, 404, 422, and 500 status codes.
5. **Database & Migrations**: Configured SQLAlchemy 2.0 engine, Alembic environment, baseline migration creating audit/system tables, and verified migration execution.
6. **Testing**: Automated Pytest suite testing healthy endpoints, degraded readiness, invalid configuration parsing, structured logging, and migration connectivity.
7. **Quality Assurance**: Ruff linting and formatting configured with zero errors; Mypy static typing passed.
8. **DevOps & Containers**: Production multi-stage `Dockerfile`, `docker-compose.yml` defining backend and PostgreSQL services with health checks, and GitHub Actions CI workflow.

---

## 7. Risk Management & Mitigations

| Identified Risk | Severity | Probability | Mitigation Strategy |
| :--- | :--- | :--- | :--- |
| **LLM Provider Outage / Latency** | High | Medium | Complete decoupling of detection, baselining, and alerting from LLM. Agent investigation is strictly an enhancement layer. |
| **Hallucinated Remediation Actions** | Critical | Low | Strict allowlist of remediation actions; zero arbitrary shell/code execution; policy gateway validation; dry-run simulation required before approval. |
| **Telemetry Ingestion Spikes** | High | High | Bounded ingestion queues, batching, structured dead-letter logging, and connection pooling with backpressure. |
| **Database Migration Drifts** | Medium | Low | Explicit Alembic migrations versioned in Git; CI tests verify fresh migrations against clean database containers. |
| **Secret Leaks in Metadata/Logs** | High | Low | Automated Pydantic SecretStr masks; structured logging filters for authorization headers and connection passwords. |

---

## 8. Deferred Features

The following capabilities are explicitly deferred until subsequent implementation phases:
- Agentic multi-step tool execution (Phase 6).
- Unsupervised ML training pipelines and model artifact persistence (Phase 3).
- Real-time WebSocket streaming of telemetry to UI (Phase 8).
- Multi-tenant enterprise SSO (SAML/OIDC) integration (Future roadmap).
- Distributed tracing exporter to remote Jaeger/Loki collectors (Phase 2 & Monitoring phase).
