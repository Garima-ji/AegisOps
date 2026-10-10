# AegisOps — ML Evaluation Protocol & Benchmarking Standards

This document establishes the scientific evaluation protocol, split methodologies, metric definitions, and safety guards governing all machine learning models in AegisOps Phase 3.

---

## 1. Core Principles

1. **Strict Chronological Holdout:** Time-series telemetry possesses temporal autocorrelation. Random $K$-fold cross-validation leaks future patterns into past observations and is strictly forbidden.
2. **Temporal Embargo Buffers:** A chronological test partition must not immediately abut the training partition. An embargo gap of at least $10\%$ duration (minimum 60 seconds) must separate the training timeline from the test timeline.
3. **Identical Benchmark Partitions:** Unsupervised models (Isolation Forest) and deterministic baselines (Threshold, Rolling MAD) must be evaluated against the exact same held-out test partition.
4. **No Metric Fabrication:** If positive ground-truth events are absent or insufficient in a split, metrics must honestly reflect the zero-sample state (e.g., Recall undefined or 0.0) rather than inventing synthetic scores.
5. **Operational Unit Exposure:** False alerts must be quantified per unit of operational exposure (e.g., False Alerts per Service-Hour), not merely as raw false positive counts.

---

## 2. Partition Strategy

Given a timeline $[T_{\min}, T_{\max}]$ of length $\Delta T$:

```
[------------- Train Partition (65%) -------------] [ Embargo (10%) ] [---- Test Partition (25%) ----]
T_min                                               T_train_end        T_test_start              T_max
```

- **Train Partition (0% to 65%):** Used for fitting the imputer, scaler, Isolation Forest contamination distribution, and supervised Random Forest classifiers.
- **Embargo Partition (65% to 75%):** Untouched buffer zone ensuring no multi-step rolling window or cascading failure overlaps train and test.
- **Test Partition (75% to 100%):** Completely held out. No parameter tuning, contamination calibration, or threshold selection may use this partition.

---

## 3. Evaluation Metrics

### 3.1 Anomaly Detection Metrics

| Metric | Formula | Operational Significance |
|---|---|---|
| **Event Precision** | $\frac{\text{TP}}{\text{TP} + \text{FP}}$ | Signal purity. Avoids operator alert fatigue. |
| **Event Recall** | $\frac{\text{TP}}{\text{TP} + \text{FN}}$ | Coverage. Fraction of true outages detected. |
| **F1-Score** | $2 \cdot \frac{\text{Precision} \cdot \text{Recall}}{\text{Precision} + \text{Recall}}$ | Harmonic balance between noise and sensitivity. |
| **PR-AUC** | $\sum_n (R_n - R_{n-1}) P_n$ | Precision-Recall Area Under Curve across all thresholds. |
| **False Alerts / Hour** | $\frac{\text{FP}}{\text{Exposure Hours}}$ | Expected alert noise per operational service-hour. |
| **Missed Incidents** | $\text{FN}$ | Count of genuine service disruptions missed by detector. |

### 3.2 Incident Risk Prediction Metrics

| Metric | Formula | Operational Significance |
|---|---|---|
| **PR-AUC** | $\text{AveragePrecision}(y_{\text{true}}, \hat{p})$ | Robust performance metric for imbalanced incident forecasting. |
| **Brier Score** | $\frac{1}{N}\sum_{i=1}^N (\hat{p}_i - y_i)^2$ | Calibration accuracy (mean squared probability error). Lower is better. |
| **Recall @ Top 20% Alert Budget** | $\text{Recall}(y_{\text{true}}, \hat{p} \ge \text{Percentile}_{80}(\hat{p}))$ | Proportion of future incidents caught if an SRE team can only investigate the top 20% highest risk scores. |

---

## 4. Minimum Label Sufficiency Rule

For supervised incident risk classification:
- If positive incident target occurrences in the training partition are fewer than $\text{min\_positive\_samples} = 5$, supervised model fitting is suppressed.
- The model status is recorded as `UNAVAILABLE` or `EXPERIMENTAL` with an explanatory diagnostic message.
- Accuracy must **never** be cited in isolation for imbalanced failure prediction datasets.
