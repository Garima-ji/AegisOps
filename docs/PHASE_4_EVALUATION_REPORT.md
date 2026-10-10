# AegisOps — Phase 4 Root-Cause Analysis Evaluation Report

**Generated At**: 2026-10-10T10:22:56.330532  
**Data Source Mode**: `synthetic`  
**Total Incidents Evaluated**: 3  
**Eligible Incidents**: 3  
**Excluded Incidents**: 0  

## 1. Executive Summary & Benchmark Comparison

| Metric | Multi-Factor Deterministic Ranker | Earliest Anomaly Baseline | Absolute Delta |
| :--- | :--- | :--- | :--- |
| **Top-1 Accuracy** | **100.0%** | 0.0% | +100.0% |
| **Top-3 Accuracy** | **100.0%** | 100.0% | +0.0% |
| **Mean Reciprocal Rank (MRR)** | **1.0000** | 0.5000 | +0.5000 |
| **Abstention Rate** | **0.0%** | 0.0% | +0.0% |
| **Mean Diagnosis Latency** | 0.01s | 0.0s | - |

## 2. Accuracy by Fault Scenario

| Fault Scenario | Incidents | Deterministic Top-1 | Deterministic Top-3 | Baseline Top-1 |
| :--- | :--- | :--- | :--- | :--- |
| `latency_spike` | 1 | 100.0% | 100.0% | 0.0% |
| `error_surge` | 1 | 100.0% | 100.0% | 0.0% |
| `service_timeout` | 1 | 100.0% | 100.0% | 0.0% |

## 3. Topology Coverage Breakdown

| Coverage Category | Incidents | Top-1 Accuracy | Top-3 Accuracy | MRR |
| :--- | :--- | :--- | :--- | :--- |
| `in_topology` | 3 | 100.0% | 100.0% | 1.0000 |

## 4. Per-Incident Ranking Breakdown

| Incident ID | Scenario | Ground Truth Service | In Topology | Deterministic Top-1 | Rank | Correct? | Baseline Top-1 |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `bench-inc-latency_spike` | `latency_spike` | `payment-service` | True | `payment-service` | #1 | ✅ Yes | `order-service` |
| `bench-inc-error_surge` | `error_surge` | `payment-service` | True | `payment-service` | #1 | ✅ Yes | `order-service` |
| `bench-inc-service_timeout` | `service_timeout` | `payment-service` | True | `payment-service` | #1 | ✅ Yes | `order-service` |

## 5. Methodological Rigor & Ground-Truth Isolation

- **Zero Information Leakage**: Ground-truth initiating services and fault scenario labels are strictly segregated from ranking features and inference.
- **Deterministic Reproducibility**: Ranking uses deterministic scoring ($S = 0.35 \cdot S_{\text{origin}} + 0.30 \cdot S_{\text{temp}} + 0.20 \cdot S_{\text{impact}} + 0.15 \cdot S_{\text{sev}}$) with stable tie-breaking.
- **Honest Abstention**: When candidate scores fall below 0.20 or no operational anomalies are present, the subsystem outputs `INSUFFICIENT_EVIDENCE` without guessing.