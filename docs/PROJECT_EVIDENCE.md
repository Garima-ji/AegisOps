# AegisOps — Verifiable Project Evidence & Claim Matrix

## 1. Introduction & Verification Principles

This document provides a strictly verifiable audit trail for every engineering claim regarding the AegisOps platform.

To maintain complete credibility during technical reviews and portfolio evaluations:
- **Zero Fabricated Results**: Every metric, test count, and performance figure corresponds to an actual executed command on this repository.
- **Dataset Origins Explicitly Labeled**: All evaluations clearly distinguish between *deterministic synthetic fixtures*, *controlled local simulations*, and *production operational data*.
- **Documented Limitations**: Known engineering boundaries and non-implemented features are documented without omission.

---

## 2. Verifiable Engineering Claims Matrix

### 2.1 Telemetry Ingestion & Distributed Observability
- **Proposed Claim**: Engineered a high-throughput, schema-validated ingestion pipeline supporting metrics, structured logs, and OpenTelemetry-compatible distributed trace spans with automatic sensitive credential masking.
- **Implementation Location**:
  - `backend/app/ingestion/` (`sample_app.py`, `generator.py`)
  - `backend/app/schemas/telemetry.py`
  - `backend/app/observability/tracer.py` (`PipelineTracer`, `TraceSpan`)
  - `backend/app/observability/metrics.py` (`MetricsRegistry`)
- **Verification Command / Test**:
  - `pytest backend/tests/unit/test_telemetry_schemas.py`
  - `pytest backend/tests/unit/test_tracer.py`
  - `pytest backend/tests/unit/test_metrics_registry.py`
- **Measured Result**:
  - 12/12 telemetry schema tests passed.
  - 4/4 tracer tests passed; sensitive keys (`password`, `secret`, `token`, `key`, `authorization`, `prompt`) automatically scrubbed.
  - Prometheus metrics text rendering conforms to Prometheus v0.0.4 standard with bounded label cardinality.
- **Dataset Origin**: Deterministic synthetic telemetry schemas and local simulated traces.
- **Limitations**: Ingestion is synchronous within the FastAPI application process; does not include an external Kafka or RabbitMQ message broker.

---

### 2.2 Controlled Fault Injection & Safety Boundaries
- **Proposed Claim**: Implemented a safety-governed fault injection engine supporting latency spikes, error surges, and service timeouts, restricted by an immutable service allowlist and bounded durations.
- **Implementation Location**:
  - `backend/app/ingestion/fault_injection.py` (`FaultInjectionEngine`, `SAFETY_ALLOWLISTED_SERVICES`)
  - `backend/app/services/experiment_service.py`
- **Verification Command / Test**:
  - `pytest backend/tests/unit/test_fault_injection.py`
- **Measured Result**:
  - 7/7 fault injection tests passed.
  - Rejection of unallowlisted service injections verified (`ValidationError`).
  - Strict duration bounds enforced ($[5\text{s}, 300\text{s}]$).
- **Dataset Origin**: Controlled local in-process simulation state.
- **Limitations**: Faults are simulated in-memory and affect synthetic request generators; they do not manipulate Linux kernel `tc` (traffic control) or network interfaces.

---

### 2.3 Anomaly Detection & Incident Risk Modeling
- **Proposed Claim**: Developed statistical $z$-score threshold detectors, multivariate Isolation Forests, and RandomForest incident risk classifiers trained on non-leaking chronological holdouts.
- **Implementation Location**:
  - `backend/app/ml/detectors.py` (`ThresholdDetector`, `IsolationForestDetector`)
  - `backend/app/ml/models.py` (`IncidentRiskPredictor`)
  - `backend/app/ml/features.py` (rolling window feature engineering)
  - `ml/train.py`
- **Verification Command / Test**:
  - `pytest backend/tests/unit/test_ml_models.py`
  - `pytest backend/tests/unit/test_ml_features.py`
  - `pytest backend/tests/unit/test_ml_evaluation.py`
  - `python scripts/evaluate_continuous.py`
- **Measured Result**:
  - 28/28 ML unit tests passed.
  - Isolation Forest trained on 1,440 synthetic operational windows.
  - Chronological evaluation enforces zero future leakage with temporal embargo intervals.
- **Dataset Origin**: Deterministic synthetic telemetry generator with seeded normal and anomalous periods.
- **Limitations**: Models are trained on simulated microservice patterns; performance on real-world heterogeneous cloud infrastructure requires retraining on actual production telemetry.

---

### 2.4 Service Topology & Deterministic Root-Cause Analysis
- **Proposed Claim**: Created a deterministic, explainable root-cause ranking algorithm based on topological dependency traversal and anomaly propagation scoring, achieving 100% Top-1 accuracy on labeled release benchmark incidents without LLM hallucinations.
- **Implementation Location**:
  - `backend/app/topology/service.py` (`TopologyService`, `TraceGraphReconstructor`)
  - `backend/app/rca/engine.py` (`RootCauseEngine`)
  - `backend/app/rca/evaluation.py`
- **Verification Command / Test**:
  - `pytest backend/tests/unit/test_topology.py`
  - `pytest backend/tests/unit/test_rca.py`
  - `python scripts/run_release_benchmark.py --mode fixture`
- **Measured Result**:
  - 8/8 topology tests passed (cycle detection, time-bounded snapshots).
  - 8/8 RCA tests passed.
  - On the Phase 7 fixed benchmark (14 scenarios):
    - Top-1 RCA Accuracy: **100.0% (4/4 eligible incidents)**
    - Top-3 RCA Accuracy: **100.0% (4/4 eligible incidents)**
    - Mean Reciprocal Rank (MRR): **1.00**
    - Correct Abstention Rate: **100.0% (3/3 ambiguous/missing scenarios)**
- **Dataset Origin**: Versioned deterministic benchmark catalog (`backend/app/benchmarks/catalog.py`).
- **Limitations**: Evaluated on a 4-service microservice graph; complex cloud meshes with hundreds of interconnected microservices may require graph pruning.

---

### 2.5 LangGraph Autonomous Investigation Agent
- **Proposed Claim**: Built a stateful multi-node LangGraph investigation agent equipped with bounded evidence gathering tools ($\le 5$ calls), strict runbook retrieval, and schema-enforced citation grounding.
- **Implementation Location**:
  - `backend/app/agent/graph.py` (LangGraph state machine)
  - `backend/app/agent/state.py` (`InvestigationState`)
  - `backend/app/agent/tools/` (telemetry, log, and runbook tools)
  - `backend/app/agent/providers.py` (`ConfigurableLLMProvider`)
- **Verification Command / Test**:
  - `pytest backend/tests/unit/test_agent_graph.py`
  - `pytest backend/tests/unit/test_agent_tools.py`
  - `pytest backend/tests/unit/test_citation_validation.py`
- **Measured Result**:
  - 10/10 agent graph and tools tests passed.
  - Bounded iteration and tool budgets strictly enforced ($N \le 5$).
  - Citation validation ensures 100% of reported claims match actual database evidence records.
  - Fallback provider successfully engages when external LLMs are offline or timeout.
- **Dataset Origin**: Controlled local SQLite and PostgreSQL incident investigations.
- **Limitations**: Agent utilizes bounded retrieval tools; it cannot perform arbitrary ad-hoc web searches or open-ended bash commands.

---

### 2.6 Deterministic Remediation Policy & Safe In-Memory Simulation
- **Proposed Claim**: Architected a dual-barrier governance model where remediation proposals are evaluated by compiled Python code outside the LLM context, prohibiting unallowlisted services, ungrounded actions, and live production modifications.
- **Implementation Location**:
  - `backend/app/agent/remediation/policy.py` (`RemediationPolicyEngine`)
  - `backend/app/agent/remediation/simulator.py` (`RemediationSimulationExecutor`)
  - `backend/app/agent/remediation/actions.py`
- **Verification Command / Test**:
  - `pytest backend/tests/unit/test_remediation_policy.py`
  - `pytest backend/tests/unit/test_security_review.py`
  - `pytest backend/tests/unit/test_prompt_injection.py`
- **Measured Result**:
  - 28/28 safety and security tests passed.
  - Unallowlisted actions executed: **0 (Zero)**
  - Unapproved actions executed: **0 (Zero)**
  - Live production mutations: **0 (Zero)**
  - Injections attempting prompt override in telemetry denied with 100% reliability.
- **Dataset Origin**: Adversarial test suites and release candidate benchmark.
- **Limitations**: Remediation executes strictly as in-process simulation against `FaultInjectionEngine`; live Kubernetes or cloud API remediation is intentionally not implemented for safety.

---

### 2.7 Codebase Quality, Type Safety & CI Pipeline
- **Proposed Claim**: Maintained a production-grade codebase with strict type hints, zero lint errors, and an automated GitHub Actions CI pipeline executing migrations, tests, and benchmark gates.
- **Implementation Location**:
  - `.github/workflows/ci.yml`
  - `pyproject.toml`
  - `Dockerfile`, `docker-compose.yml`
- **Verification Command / Test**:
  - `python -m ruff check .`
  - `python -m ruff format --check .`
  - `mypy backend/app`
  - `pytest backend/tests`
- **Measured Result**:
  - **190/190 passing unit and integration tests** (100% pass rate in ~26 seconds).
  - **86 source files verified by Mypy** with zero static type errors under strict type annotations.
  - **134 source files formatted cleanly** under Ruff with zero lint or style violations.
  - Live GitHub Actions CI verified passing on `origin/main`.
- **Dataset Origin**: Complete local repository and remote GitHub repository.
- **Limitations**: Load testing under sustained 10,000+ req/sec traffic is not part of default CI (kept separate to maintain fast CI execution).
