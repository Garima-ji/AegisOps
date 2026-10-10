# AegisOps — Release Candidate Operational Runbook

## 1. Scope & Objective

This runbook documents the standard operating procedures (SOP) for setting up, deploying, validating, and troubleshooting the AegisOps Release Candidate in a clean environment.

---

## 2. Clean-Environment Setup Procedure

### 2.1 System Prerequisites
- **Operating System**: Linux (Ubuntu 22.04+), macOS (13+), or Windows 10/11 (PowerShell)
- **Python**: Version 3.12.x (strict requirement)
- **Git**: 2.40+
- **Docker & Docker Compose**: (Optional; required for PostgreSQL containerized service)
- **PostgreSQL**: Version 16 (Optional; SQLite in-memory fallback available for offline validation)

### 2.2 Clone & Virtual Environment Creation
```bash
# 1. Clone repository
git clone https://github.com/Garima-ji/AegisOps.git
cd AegisOps

# 2. Create isolated virtual environment
python -m venv .venv

# 3. Activate virtual environment
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# Linux / macOS:
source .venv/bin/activate

# 4. Upgrade pip and install package with development dependencies
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

### 2.3 Environment Configuration
Copy the documented environment placeholder file and review default values:
```bash
# Copy template configuration
cp .env.example .env
```

Key environment configuration variables:
| Variable | Default Value | Description |
| :--- | :--- | :--- |
| `ENVIRONMENT` | `development` | Operating mode (`development`, `test`, `production`) |
| `DATABASE_URL` | `postgresql+psycopg://postgres:postgres@localhost:5432/aegisops` | Database connection string |
| `SECRET_KEY` | *(32+ character string)* | Session signing key |
| `API_PORT` | `8000` | HTTP service port |
| `LOG_LEVEL` | `INFO` | Console log verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `LLM_PROVIDER` | `fake` | LLM provider (`fake`, `openai`, `anthropic`) |

### 2.4 Database Startup & Schema Migration
If using PostgreSQL via Docker Compose:
```bash
# Start PostgreSQL service container
docker compose up -d postgres

# Verify PostgreSQL readiness
docker compose exec postgres pg_isready -U postgres

# Apply Alembic schema migrations
alembic -c backend/alembic.ini upgrade head
```

If validating offline without PostgreSQL, tests and benchmark harnesses automatically provision isolated in-memory SQLite databases.

### 2.5 Model Artifact Verification
Verify that required machine learning model artifacts are present in `ml/artifacts/`:
```bash
# Check artifact availability
python -c "from app.core.versioning import get_platform_manifest; m = get_platform_manifest(); print(m.to_dict())"
```
Required files:
- `ml/artifacts/isolation_forest.joblib`
- `ml/artifacts/incident_predictor.joblib`
- `ml/artifacts/evaluation_report.json`

---

## 3. Operational Health & Validation Checks

### 3.1 Code Quality & Static Analysis Gates
Execute all static verification checks before running services:
```bash
# 1. Code style & linting check
python -m ruff check .

# 2. Formatting verification
python -m ruff format --check .

# 3. Strict type analysis
mypy backend/app
```

### 3.2 Full Test Suite Execution
Execute the 190+ unit and integration test suite:
```bash
pytest backend/tests -v
```

### 3.3 Application Startup & Readiness Check
Start the FastAPI development server:
```bash
uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```
Verify endpoints via curl or browser:
```bash
# Health check endpoint
curl -s http://127.0.0.1:8000/health
# Expected: {"status":"healthy","database":"connected",...}

# Readiness probe
curl -s http://127.0.0.1:8000/ready
# Expected: {"ready":true,"models_loaded":true,...}

# Prometheus metrics endpoint
curl -s http://127.0.0.1:8000/metrics
```

---

## 4. Release Benchmark & Continuous Evaluation

### 4.1 Running the Release Benchmark Harness
Execute the 14-scenario independent release candidate benchmark:
```bash
# Offline fixture mode (fast, isolated, zero external dependencies)
python scripts/run_release_benchmark.py --mode fixture

# Summary-only output
python scripts/run_release_benchmark.py --mode fixture --summary
```

Generated reports:
- Machine-readable JSON: `ml/artifacts/release_benchmark_report.json`
- Human-readable Markdown: `docs/PHASE_7_EVALUATION.md`

### 4.2 Running Continuous Drift & Evaluation
Evaluate anomaly detectors and model drift over chronological data holdouts:
```bash
python scripts/evaluate_continuous.py
```

### 4.3 Running End-to-End Scenario Runner
```bash
# Execute controlled high latency scenario
python scripts/run_scenario.py --scenario high_latency --mode live-sim
```

---

## 5. Security Invariant & Remediation Policies

### 5.1 Remediation Safety Constraints
1. **Never Enable Live Remediation**: Production remediation execution is strictly prohibited in this release candidate.
2. **Deterministic Allowlist**: The target service must be in `["payment-service", "order-service", "inventory-service", "notification-service"]`.
3. **Approval Gating**: When configured with `require_approval_by_default=True`, all non-informational actions require explicit human operator approval.
4. **Zero Production Mutation**: Simulations execute solely against the in-process `FaultInjectionEngine`.

### 5.2 Security Regression Suite
```bash
pytest backend/tests/unit/test_security_review.py -v
```

---

## 6. Troubleshooting & Operational Recovery

### Issue 1: Database Migration Fails on Revision Name Length
- **Symptom**: `value too long for type character varying(32)` during Alembic upgrade.
- **Cause**: Revision ID exceeds PostgreSQL's default `VARCHAR(32)` column width in `alembic_version`.
- **Resolution**: Ensure all revision identifiers in `backend/migrations/versions/` are $\le 30$ characters. Revision `005_investigations` conforms to this standard.

### Issue 2: Missing ML Artifacts
- **Symptom**: `GracefulDegradationManager` logs degraded mode due to missing `isolation_forest.joblib`.
- **Resolution**: Run the training pipeline to regenerate artifacts:
  ```bash
  python ml/train.py --mode synthetic
  ```

### Issue 3: Incomplete Distributed Traces (Low Coverage)
- **Symptom**: RCA engine returns `status: "abstained"`, reason: `LOW_TRACE_COVERAGE`.
- **Resolution**: Ingest at least 3 linked parent-child spans before requesting topological root-cause analysis.

---

## 7. Graceful Shutdown & Environment Cleanup

```bash
# 1. Stop FastAPI server (Ctrl+C in terminal)

# 2. Reset in-memory fault engine states
python -c "from app.ingestion.fault_injection import fault_engine; fault_engine.reset()"

# 3. Stop PostgreSQL container (if running)
docker compose down
```
