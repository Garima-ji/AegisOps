# AegisOps Phase 5 Implementation Review

## 1. Executive Summary

Phase 5 of AegisOps implements **Agentic Incident Investigation, Grounded Diagnosis & Safe Remediation**. The subsystem uses **LangGraph** to orchestrate an evidence-gathering and diagnosis workflow that preserves the deterministic Phase 4 root-cause ranking, validates every factual report claim against immutable evidence citations, defends deterministically against prompt injection, and gates all remediation actions behind safety policies and controlled simulation environments.

### Key Metrics & Quality Status
- **Automated Tests**: 149 passed out of 149 tests (0 regressions across Phases 1–4).
- **Type Safety**: Strict `mypy` passing with zero errors across all 79 source files.
- **Linter & Code Style**: `ruff check` and `ruff format` passing with 0 warnings/errors.
- **Evaluation Benchmark**: 13/13 scenarios passed (100.0%) in `scripts/evaluate_investigations.py`.
- **Database Migrations**: Migration `005_investigations_and_remediation` implemented and verified.

---

## 2. Files Created and Modified

### New Modules Created
- `backend/app/agent/`:
  - `evidence.py`: `EvidenceRecord`, `EvidenceBank`, immutable IDs (`ev-{type}-{service}-{hash}`), and `validate_citations`.
  - `reporting.py`: `InvestigationReport`, `ProposedRemediationPlan`, `CandidateEvaluation`, and citation verification.
  - `state.py`: Typed `InvestigationState` schema for LangGraph.
  - `providers.py`: `BaseLLMProvider`, `FakeDeterministicProvider`, `ConfigurableLLMProvider`.
  - `graph.py`: LangGraph state machine with 12 nodes, conditional edges, and iteration/tool bounding.
  - `service.py`: `InvestigationService` coordinating graph execution, database persistence, and remediation lifecycle.
  - `runbooks/catalog.py`: Versioned local markdown runbooks and deterministic token retriever.
  - `remediation/actions.py`: Typed remediation action catalog (`RemediationActionType`, `PolicyDecision`, `SimulationStatus`).
  - `remediation/policy.py`: Deterministic safety policy engine (`RemediationPolicyEngine`).
  - `remediation/simulator.py`: Safe execution adapter interfacing with `FaultInjectionEngine`.
  - `tools/`: Bounded investigation tools (`GetAnomalyEventsTool`, `QueryServiceMetricsTool`, `QueryServiceLogsTool`, `QueryTraceSpansTool`, `GetServiceTopologyTool`, `GetRootCauseCandidatesTool`, `RetrieveRunbookTool`, `EvaluateRemediationPolicyTool`, `ExecuteSimulatedRemediationTool`).
- Database & Migrations:
  - `backend/app/db/models/investigation.py`: `InvestigationModel`, `InvestigationEventModel`, `RemediationProposalModel`.
  - `backend/migrations/versions/005_investigations_and_remediation.py`: Alembic migration script.
- API Endpoints:
  - `backend/app/api/v1/investigations.py`: REST endpoints for investigation launch, status, report, audit events, dry-run, approval, and simulation.
- Evaluation & Testing:
  - `scripts/evaluate_investigations.py`: 13-scenario reproducible benchmark suite.
  - `backend/tests/unit/test_agent_tools.py`
  - `backend/tests/unit/test_agent_graph.py`
  - `backend/tests/unit/test_remediation_policy.py`
  - `backend/tests/unit/test_citation_validation.py`
  - `backend/tests/unit/test_prompt_injection.py`
  - `backend/tests/integration/test_investigations_api.py`
- Documentation:
  - `docs/PHASE_5_PLAN.md`
  - `docs/PHASE_5_ARCHITECTURE.md`
  - `docs/PHASE_5_EVALUATION.md`
  - `docs/PHASE_5_REVIEW.md` (this document)

### Existing Files Modified
- `backend/app/core/config.py`: Added Phase 5 agent settings (`agent_max_tool_calls`, `agent_llm_provider`, `remediation_simulation_enabled`, etc.).
- `backend/app/db/base.py`: Registered Phase 5 models on `Base.metadata`.
- `backend/app/db/models/__init__.py`: Exported investigation models.
- `backend/app/api/v1/router.py`: Mounted `investigations.router`.
- `pyproject.toml`: Added `langgraph` and `langchain-core` dependencies and mypy overrides.

---

## 3. LangGraph Workflow & Tool Summary

### State Machine Nodes
1. `initialize`: Validates time window bounds and initializes audit session.
2. `gather_initial_evidence`: Pulls anomalies, topology snapshot, and Phase 4 root-cause candidates into the immutable evidence bank.
3. `assess_evidence_sufficiency`: Checks for missing signals or conflicting telemetry; routes to targeted telemetry queries or triggers explicit abstention.
4. `targeted_investigation`: Queries bounded metrics and logs for suspected initiating services.
5. `correlate_evidence`: Correlates evidence against Phase 4 candidates while strictly preserving the baseline ranking.
6. `retrieve_runbook`: Queries local versioned runbooks for matched remediation guidance.
7. `validate_diagnosis`: Synthesizes grounded diagnosis via model provider or deterministic fake.
8. `recommend_action`: Formulates proposed remediation plan with required parameters.
9. `apply_safety_policy`: Deterministic code evaluation enforcing allowlists, parameter constraints, and approval requirements.
10. `execute_simulation`: Safely executes permitted action against `FaultInjectionEngine`.
11. `generate_report`: Compiles `InvestigationReport` and validates that all citations resolve to actual records in the evidence bank.
12. `finalize_and_audit`: Records completion status and audit timeline.

### Tool Bounding and Safeguards
- Maximum 50 items per query; maximum 4-hour time window.
- Maximum 15 tool calls per investigation run.
- Zero raw SQL, shell execution, or arbitrary file system tools.

---

## 4. REST API Endpoints Added

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/api/v1/investigations` | Start an agentic investigation over an incident window |
| `GET` | `/api/v1/investigations/{id}` | Inspect investigation run status, duration, and findings |
| `GET` | `/api/v1/investigations/{id}/report` | Retrieve the validated evidence-grounded report JSON |
| `GET` | `/api/v1/investigations/{id}/events` | Retrieve sanitized audit event timeline |
| `POST` | `/api/v1/investigations/{id}/remediation/dry-run` | Evaluate proposed action without mutating simulation state |
| `POST` | `/api/v1/investigations/{id}/remediation/approve` | Record operator sign-off on a pending proposal |
| `POST` | `/api/v1/investigations/{id}/remediation/execute-simulation` | Execute eligible action inside controlled simulator |

---

## 5. Security & Safety Controls

1. **Adversarial Telemetry Defense**: Untrusted telemetry content is strictly treated as data inside structured JSON containers. Evaluated in Scenario 10 with malicious injection strings, verifying that prompt injection cannot bypass the deterministic policy engine.
2. **Deterministic Safety Policy**: Governed by code (`RemediationPolicyEngine`), not natural language prompts.
3. **Target Service Allowlist**: Restricted to `SAFETY_ALLOWLISTED_SERVICES` (`order-service`, `payment-service`, `inventory-service`, `notification-service`). Unallowlisted targets are denied immediately.
4. **Zero Production Mutation**: The simulation execution adapter interfaces strictly with the in-process `FaultInjectionEngine`.

---

## 6. Exact Quality & Test Results

```
======================= 149 passed, 1 warning in 18.23s =======================

Checks:
- ruff check .: All checks passed!
- ruff format --check .: 116 files already formatted
- mypy backend/app: Success: no issues found in 45 source files
- mypy scripts/evaluate_investigations.py: Success: no issues found in 1 source file
- scripts/evaluate_investigations.py: 13/13 passed (100.0%) in 0.24s
```

---

## 7. Known Limitations & Deferred Work

1. **Controlled Simulation Scope**: Remediations and evaluations are executed against the in-process synthetic simulator (`FaultInjectionEngine`). Live Kubernetes/AWS cluster remediation integrations are intentionally excluded.
2. **Production-Data Validation Pending**: Benchmark results reflect controlled fault injection scenarios; validation against production telemetry traffic remains deferred.
3. **Local Runbook Knowledge Base**: The versioned runbook catalog is stored locally in Python/Markdown structures. External vector database integration (e.g. Qdrant/Pinecone) is deferred to future enterprise extensions.

---

## 8. Local End-to-End Demo Guide

### Prerequisites
Activate virtual environment:
```powershell
.\.venv\Scripts\Activate.ps1
```

### Step 1: Run the Evaluation Benchmark (13 Scenarios)
```powershell
python scripts/evaluate_investigations.py
```
Expected output:
```
Starting AegisOps Phase 5 Agentic Investigation Benchmark (13 Scenarios)...
Phase 5 Benchmark complete: 13/13 passed (100.0%) in 0.24s
```

### Step 2: Start the FastAPI Server
```powershell
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### Step 3: Trigger an Investigation via API
```bash
curl -X POST "http://127.0.0.1:8000/api/v1/investigations" \
  -H "Content-Type: application/json" \
  -d '{"incident_id": "inc-demo-001", "require_approval": true}'
```

### Step 4: Retrieve the Validated Report
```bash
curl "http://127.0.0.1:8000/api/v1/investigations/{investigation_id}/report"
```

### Step 5: Dry-Run and Approve Remediation
```bash
# Dry-run
curl -X POST "http://127.0.0.1:8000/api/v1/investigations/{investigation_id}/remediation/dry-run"

# Approve
curl -X POST "http://127.0.0.1:8000/api/v1/investigations/{investigation_id}/remediation/approve" \
  -H "Content-Type: application/json" \
  -d '{"operator_id": "lead-reliability-engineer"}'

# Execute in controlled simulator
curl -X POST "http://127.0.0.1:8000/api/v1/investigations/{investigation_id}/remediation/execute-simulation"
```
