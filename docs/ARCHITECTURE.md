# AegisOps — System Architecture & Design

## 1. Architectural Overview & Design Philosophy

**AegisOps** is an autonomous IT Operations Intelligence platform designed around the principles of **defense-in-depth, strict evidence grounding, bounded agent autonomy, and zero-downtime resilience**.

The architecture decouples mission-critical operational telemetry detection from cognitive AI reasoning:
- **Fast Path (Deterministic & ML Detection)**: Ingests metrics, traces, and logs; performs validation, streaming statistical anomaly checks, and topological root cause ranking. Operates entirely without LLM calls, ensuring SLA-critical alerting never suffers from model downtime or latency spikes.
- **Cognitive Path (Agentic AI & Remediation)**: Triggers stateful LangGraph workflows on confirmed incidents to investigate contextual telemetry, gather historical runbooks, formulate hypotheses, and propose remediations through an audited policy gateway.

```
       +-------------------------------------------------------------+
       |                  Distributed Infrastructure                 |
       |     Microservices, Databases, Kubernetes, Cloud Resources   |
       +------------------------------+------------------------------+
                                      | Metrics, Logs, Traces
                                      v
                        +----------------------------+
                        |  Telemetry Ingestion Layer |
                        | (Validation & Normalization)|
                        +--------------+-------------+
                                       |
                   +-------------------+-------------------+
                   |                                       |
                   v [Fast Path: Zero-LLM]                 v [Auditing]
       +-----------------------+              +-----------------------+
       | Detection & Baseline  |              |  PostgreSQL Store     |
       | - Z-Score, EWMA, MAD  |              |  - Telemetry Points   |
       | - Isolation Forest ML |              |  - Audit Log Ledger   |
       +-----------+-----------+              +-----------------------+
                   | Anomalies                               ^
                   v                                         |
       +-----------------------+                             |
       | Correlation & Topology|                             |
       | - Service Graph Match |                             |
       | - Root Cause Ranking  |                             |
       +-----------+-----------+                             |
                   | Incidents & Events                      |
                   v                                         |
+-----------------------------------------------------+      |
| Cognitive Path: Agentic AI Investigation (LangGraph)|      |
|  - Stateful Investigation Machine                   |      |
|  - Registered Read-Only Tools                       |      |
|  - Runbook Retrieval (RAG)                          |      |
|  - Grounded Evidence Verification                   |      |
+--------------------------+--------------------------+      |
                           | Recommendations                 |
                           v                                 |
+-----------------------------------------------------+      |
| Remediation Policy Gateway & Simulator              |      |
|  - Allowlist Verification                           |      |
|  - Operator Approval Workflow                       +------+
|  - Sandboxed Dry-Run Execution                      |
|  - Rollback Plan Assertion                          |
+-----------------------------------------------------+
```

---

## 2. Component Directory & Module Responsibilities

The codebase adopts a modular monorepo layout, strictly separating concerns:

### 2.1 Backend Modules (`backend/app/`)

| Module Path | Core Responsibilities | Dependencies & Invariants |
| :--- | :--- | :--- |
| `core/` | Application-wide settings (`config.py`), structured logging (`logging.py`), security tokens, and global custom exceptions (`exceptions.py`). | Zero dependencies on higher-level domain modules. Must load and validate environment variables at startup. |
| `schemas/` | Pydantic v2 data models for request/response payloads, telemetry schemas, error structures, and incident representations. | Pure data modeling and serialization; no database IO or external network calls. |
| `db/` | SQLAlchemy engine initialization, session lifecycle management (`session.py`), and base declarative models (`base.py`). | Manages asynchronous/synchronous database connection pools and health checks. |
| `api/` | HTTP route controllers and dependencies (`api/v1/`). Sub-routers handle `/health`, `/system`, `/telemetry`, `/incidents`, `/remediation`. | Responsible only for HTTP parameter parsing, calling domain services, and status code translation. |
| `ingestion/` | Telemetry intake pipelines: metric, log, trace parsing, schema validation, timestamp monotonic verification, and buffer queues. | Sanitizes all external inputs before passing to downstream engines. |
| `detection/` | Statistical baseline engines (EWMA, Z-Score, MAD) and ML anomaly detectors (Isolation Forest, One-Class SVM). | Deterministic, CPU-bound numeric algorithms. Must never perform blocking network calls. |
| `correlation/` | Service dependency graph modeling, topology traversal, temporal anomaly clustering, and root-cause ranking algorithms. | Correlates anomalies across dependent nodes in the directed acyclic graph (DAG). |
| `forecasting/` | Time-series regression, trend analysis, and predictive SLO breach estimation. | Uses historical telemetry to estimate time-to-exhaustion (burn rate). |
| `agent/` | Stateful LangGraph investigation agent, prompt templates, and strictly bounded read-only investigation tools. | Sandboxed tool execution; cannot run arbitrary code or unbounded SQL queries. |
| `remediation/` | Remediation action definitions, policy rule engine, approval state machine, and simulated action execution with rollback logic. | Enforces strict separation of recommendation vs. approval vs. execution. |
| `observability/`| Prometheus metrics collector, OpenTelemetry instrumentation hooks, and distributed tracing spans. | Standardizes internal telemetry export. |

---

## 3. Data Flow & Telemetry Lifecycle

```
[External Producer]
       |
       | POST /api/v1/telemetry/ingest (Batch JSON)
       v
+-------------------------------------------------------------+
| 1. API Boundary (backend/app/api/v1/telemetry.py)           |
|    - Request authentication & rate limit check              |
|    - Pydantic schema validation (TelemetryBatchRequest)     |
+------------------------------+------------------------------+
                               | Validated Payloads
                               v
+-------------------------------------------------------------+
| 2. Ingestion Service (backend/app/ingestion/)               |
|    - Timestamp normalization (ISO 8601 UTC)                 |
|    - Metadata enrichment (environment, tenant, host)        |
|    - Push to in-memory buffer / persistence queue           |
+------------------------------+------------------------------+
                               | Normalized Stream
                               v
+-------------------------------------------------------------+
| 3. Statistical Baseline & Detection (backend/app/detection/)|
|    - Fast check: Rolling EWMA & Z-Score anomaly score       |
|    - ML check: Isolation Forest multi-variate score         |
|    - Emission: AnomalyEvent if threshold exceeded           |
+------------------------------+------------------------------+
                               | Anomaly Events
                               v
+-------------------------------------------------------------+
| 4. Correlation & Topology (backend/app/correlation/)        |
|    - Map anomalies to Service Dependency Graph              |
|    - Cross-correlate related log errors and trace spans     |
|    - Calculate patient-zero root cause likelihood score     |
|    - Trigger Incident Lifecycle (New / Open Incident)       |
+------------------------------+------------------------------+
                               | Active Incident
                               v
+-------------------------------------------------------------+
| 5. Agentic AI Investigation (backend/app/agent/)            |
|    - LangGraph initializes state with Incident Context      |
|    - Executes tool queries: fetch_metrics, search_logs      |
|    - Synthesizes findings with runbook recommendations      |
|    - Formulates RemediationProposal                         |
+------------------------------+------------------------------+
                               | Remediation Proposal
                               v
+-------------------------------------------------------------+
| 6. Remediation Policy Gateway (backend/app/remediation/)    |
|    - Verifies action is on safe allowlist                   |
|    - Validates parameter bounds (e.g. scale max = 10)       |
|    - Awaits human operator authorization                    |
|    - Executes in Simulated Sandbox Environment              |
|    - Asserts post-execution health & logs rollback snapshot |
+-------------------------------------------------------------+
```

---

## 4. API Boundaries & Error Response Contract

All AegisOps APIs adhere to the **RFC 7807 (Problem Details for HTTP APIs)** specification for error handling. Every error response provides deterministic machine-readable fields alongside human-actionable messages:

```json
{
  "type": "https://aegisops.dev/errors/validation-error",
  "title": "Unprocessable Entity",
  "status": 422,
  "detail": "Field 'timestamp' must be a valid UTC timestamp in ISO-8601 format.",
  "instance": "/api/v1/telemetry/metrics",
  "code": "INVALID_TIMESTAMP",
  "timestamp": "2026-10-09T18:15:00.000000Z",
  "request_id": "req_01h8a9bcdef12345"
}
```

### Key API Boundaries

| Endpoint | Method | Purpose | Authentication | Error Responses |
| :--- | :--- | :--- | :--- | :--- |
| `/health/live` | GET | Process liveness check for Kubernetes/systemd | Public | 500 if process hung |
| `/health/ready` | GET | Validates database and external dependency readiness | Public | 503 if DB disconnected |
| `/api/v1/system/info` | GET | Reports runtime version, environment, commit SHA | Public (Sanitized) | 500 on internal failure |
| `/api/v1/telemetry/*` | POST | Ingests metrics, logs, traces, topology updates | API Key / Token | 400, 422, 500 |
| `/api/v1/incidents/*` | GET/POST | Lists, queries, and manages incident workflows | Bearer JWT / API Key | 401, 403, 404, 500 |
| `/api/v1/agent/*` | POST | Triggers investigation, reviews agent evidence | Bearer JWT | 401, 403, 422, 500 |
| `/api/v1/remediation/*`| POST | Approves, simulates, and records remediation runs | RBAC (Operator+) | 401, 403, 409, 500 |

---

## 5. Failure Modes & Graceful Degradation Strategies

AegisOps is engineered to withstand failure in any of its constituent dependencies:

| Failure Scenario | Impact | Built-in Architectural Mitigation |
| :--- | :--- | :--- |
| **LLM Provider Downtime / Rate-Limiting** | Agent investigations cannot run | Ingestion, statistical anomaly detection, rule-based root cause analysis, and manual remediation run unaffected. Incident is marked with `investigation_status="DEGRADED_MANUAL"`. |
| **PostgreSQL Database Connectivity Loss** | Ingestion writes and state queries fail | Health check `/health/ready` returns HTTP 503; in-memory buffer holds high-priority telemetry up to memory threshold; automated reconnect with exponential backoff and jitter. |
| **Telemetry Ingestion Flood** | CPU and memory starvation risk | Ingestion endpoint applies request payload size limits (max 10MB per batch), backpressure rate-limiting, and async threadpool offloading. |
| **Agent Tool Execution Timeout** | Infinite loop or stalled tool | Strict per-tool execution timeout (5.0s maximum); circuit breakers terminate tools exceeding retry thresholds; agent gracefully summarizes partial telemetry. |
| **Simulated Remediation Execution Failure** | Sandbox returns unexpected state | Automatic rollback simulation executed; incident flagged as `REMEDIATION_FAILED`; alert dispatched to on-call team with diagnostic snapshot. |

---

## 6. Security Controls & Sandboxing

AegisOps treats all incoming telemetry and external data as **untrusted**:

1. **Input Sanitization & Injection Defense**:
   - Telemetry strings are bounded and sanitized to prevent Log4j-style template injection or prompt injection attacks.
   - Pydantic models reject undeclared attributes (`extra = "forbid"` on critical inputs).
2. **Restricted Agent Execution Environment**:
   - The LangGraph investigation agent is strictly forbidden from executing arbitrary shell commands, arbitrary Python code, or arbitrary SQL queries.
   - All tools are explicitly typed Python callables with pre-compiled parameterized queries.
3. **Remediation Policy Gateway & Allowlisting**:
   - Only pre-registered, digitally signed remediation templates are executable (e.g. `RestartPod`, `ScaleDeployment`, `RollbackDeployment`, `FlushRedisCache`).
   - Every action requires parameter boundary validation (e.g. replica count cannot exceed `MAX_REPLICAS=10`).
   - Destructive actions require cryptographic approval tokens signed by authorized human operators.
4. **Credential Isolation & Secret Masking**:
   - Database credentials, API tokens, and secrets are strictly retrieved from environment variables via Pydantic `SecretStr`.
   - Logging interceptors scrub passwords, authorization headers, and token patterns from application logs and error details.
5. **Immutable Audit Ledger**:
   - Every incident state change, model inference, tool execution, and remediation simulation creates an append-only audit event in PostgreSQL.
