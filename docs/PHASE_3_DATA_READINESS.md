# AegisOps Phase 3: Telemetry Data Readiness Audit

**Audit Date**: 2026-10-10  
**Phase**: Phase 3 — ML Detection, Incident Prediction and Evaluation  
**Auditor**: ML Platform & Observability Engineering  
**Artifact**: `ml/artifacts/dataset_audit_report.json`  

---

## 1. Executive Summary & Readiness Verdict

Prior to training any machine learning models, an empirical audit was conducted on both the live persistence store and the available synthetic telemetry generation pipeline.

### Verdict:
- **Production Telemetry Status**: **INSUFFICIENT (Empty/Unconnected)**  
  In the local development environment, a long-term production PostgreSQL telemetry lake does not yet exist. There are currently 0 historical production records stored in persistent cold storage.
- **Synthetic Benchmark Workload Status**: **READY (Verified & Seed-Deterministic)**  
  Phase 2 established a deterministic cooperating microservice workload generator (`order-service` -> `payment-service`) capable of emitting synchronized metrics, structured logs, and distributed trace spans across normal and degraded operating conditions.
- **Execution Strategy**:  
  In strict accordance with the Phase 3 requirements:
  > *"Do not fabricate data coverage or claim a model is trained on real telemetry if it is trained only on synthetic fixtures. If there is insufficient historical data, build the pipeline and tests using explicitly identified synthetic fixtures, clearly mark the models as experimental, and document the data required for valid evaluation."*
  All Phase 3 ML pipelines (Feature Extraction, Statistical Baselines, Isolation Forest, Incident-Risk Prediction) are built and validated against verified synthetic benchmark fixtures. Models trained on these fixtures are explicitly designated as **`EXPERIMENTAL`** in the model registry and API responses.

---

## 2. Live Database Audit Results

An automated inspection was performed against the live database connection:

| Metric | Measured Value | Description |
| :--- | :--- | :--- |
| **Connection Status** | `UNAVAILABLE / EMPTY` | Local PostgreSQL instance on host is unseeded or port conflict |
| **Services Registered** | 0 | No historical service registrations in cold storage |
| **Metric Records** | 0 | No persisted metric observations |
| **Structured Logs** | 0 | No persisted application logs |
| **Trace Spans** | 0 | No persisted trace spans |
| **Fault Experiments** | 0 | No active or completed experiments |
| **Ground-Truth Labels** | 0 | No persistent labels in cold storage |

---

## 3. Synthetic Benchmark Workload Audit

The benchmark workload was generated using `DeterministicTelemetryGenerator(seed=42)` across 340 contiguous discrete transactions:

### A. Volume & Signal Breakdown
- **Total Transactions**: 340
  - Normal Operation Transactions: 220 (64.7%)
  - Fault Condition Transactions: 120 (35.3%)
- **Total Metric Observations**: 2,188
- **Total Structured Logs**: 980
- **Total Distributed Trace Spans**: 1,020 (3 spans per transaction: root `order-service`, child `payment-service`, grandchild `payment-db`)
- **Missing Values**: 0 (0.0%)
- **Duplicate Records**: 0 (0.0%)

### B. Service & Metric Coverage
- **Distinct Services**: 2 (`order-service`, `payment-service`)
- **Metric Definitions & Unit Consistency**:
  - `http.server.request.duration_ms`: Unit `ms` (100% consistent)
  - `http.server.requests.count`: Unit `count` (100% consistent)
  - `http.server.errors.count`: Unit `count` (100% consistent)
  - `system.cpu.utilization`: Unit `percent` (100% consistent)
  - `system.memory.utilization`: Unit `percent` (100% consistent)
- **Unit Consistency Verified**: **YES** (Zero conflicting units detected across observations)

### C. Temporal Boundaries & Resolution
- **Start UTC**: `2026-10-10T00:00:00.051652Z`
- **End UTC**: `2026-10-10T00:05:39.067510Z`
- **Total Duration**: 339.02 seconds (~5.65 minutes)
- **Sampling Frequency**: 1.0 second per inter-service transaction

### D. Fault-Injection Scenarios & Ground-Truth Intervals
Three distinct, non-overlapping fault scenarios were executed, each separated by a 40-second normal recovery buffer:

| Experiment ID | Scenario Key | Target Service | Initiator | Duration | Samples | Overlap Detected |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `exp_bench_latency_01` | `latency_spike` | `payment-service` | `payment-service` | 40s | 40 | **False** (Clean buffer) |
| `exp_bench_error_02` | `error_surge` | `payment-service` | `payment-service` | 40s | 40 | **False** (Clean buffer) |
| `exp_bench_timeout_03` | `service_timeout` | `payment-service` | `payment-service` | 40s | 40 | **False** (Clean buffer) |

---

## 4. Suitability for ML Training and Independent Evaluation

1. **Temporal Train/Test Partitioning**:  
   The benchmark dataset cleanly supports chronological train/test splitting:
   - **Training Partition (First 200s)**: Covers initial baseline normal period (100s), `latency_spike` scenario (40s), recovery period 1 (40s), and the beginning of `error_surge` (20s).
   - **Embargo Buffer (20s)**: Eliminates sliding window overlap between partitions.
   - **Test Partition (Final 119s)**: Covers remaining `error_surge`, recovery period 2, `service_timeout` scenario (40s), and final recovery (40s).
2. **Leakage Protection**:  
   All sliding window features are extracted strictly from telemetry at or before the window cutoff timestamp ($t \le T$). Future observations cannot contaminate past features.

---

## 5. Requirements for Production Model Graduation

To transition models from `EXPERIMENTAL` to `PRODUCTION_CERTIFIED`, the live system must accumulate:
1. **Minimum Normal Baseline**: At least 7 consecutive days of continuous telemetry per registered service without unmanaged outages.
2. **Incident Diversity**: Minimum of 20 independent historical incident events with verified ground-truth labels across distinct root cause categories.
3. **Continuous Drift Monitoring**: Automated Kolmogorov-Smirnov test or Population Stability Index (PSI) monitoring to detect data drift between training baseline and live inference windows.
