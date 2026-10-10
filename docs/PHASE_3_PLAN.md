# AegisOps — Phase 3 Implementation Plan: ML Detection, Incident Prediction & Evaluation

This document outlines the detailed architecture, data pipelines, algorithms, database models, API contracts, evaluation protocols, and acceptance criteria for **Phase 3** of AegisOps.

---

## 1. Architectural Architecture & Pipeline Flow

Phase 3 introduces an automated, reproducible machine learning platform on top of the Phase 2 telemetry storage foundation.

```
                    +-------------------------------------------------------+
                    |             Raw Telemetry Lake (PostgreSQL)           |
                    |   (metric_observations, structured_logs, trace_spans)  |
                    +-------------------------------------------------------+
                                                |
                                                v
                    +-------------------------------------------------------+
                    |        Telemetry-to-Feature Engineering Pipeline      |
                    |   - Configurable windowing (60s default)              |
                    |   - Leakage-safe chronological aggregation            |
                    |   - Latency percentiles, error rates, freshness       |
                    +-------------------------------------------------------+
                                                |
               +--------------------------------+-------------------------------+
               |                                |                               |
               v                                v                               v
    +----------------------+       +--------------------------+    +--------------------------+
    |   Model A: Baselines |       | Model B: Isolation Forest|    | Model C: Incident Pred.  |
    | - Static Thresholds  |       | - Unsupervised detector  |    | - Supervised Classifier  |
    | - Rolling Median/MAD |       | - Standardized features  |    | - Chronological split    |
    | - Zero variance safe |       | - Sigmoid score mapping  |    | - Embargo window         |
    +----------------------+       +--------------------------+    +--------------------------+
               |                                |                               |
               +--------------------------------+-------------------------------+
                                                |
                                                v
                    +-------------------------------------------------------+
                    |           ML Platform Persistence & Registry          |
                    |   - ml_models (Model artifacts, schemas, metrics)     |
                    |   - anomaly_detections (Event logs, scores, triggers) |
                    |   - JSON evaluation artifacts                         |
                    +-------------------------------------------------------+
                                                |
                                                v
                    +-------------------------------------------------------+
                    |              FastAPI Endpoints (v1)                   |
                    |   - POST /api/v1/ml/anomaly-detection/run             |
                    |   - GET  /api/v1/ml/anomalies                         |
                    |   - GET  /api/v1/ml/models                            |
                    |   - GET  /api/v1/ml/models/{model_name}               |
                    |   - POST /api/v1/ml/incident-risk/predict             |
                    +-------------------------------------------------------+
```

---

## 2. Feature Engineering Pipeline

### A. Windowing & Aggregation Rules
- **Window Size ($W$)**: 60.0 seconds (configurable).
- **Cutoff Time ($t$)**: Only telemetry observations where $\text{timestamp} \le t$ and $\text{timestamp} > t - W$ are aggregated.
- **Strict Leakage Prevention**: Future observations are never visible to the feature extractor.
- **Service-Centric**: Features are computed per service or across the cooperating service graph.

### B. Standard Feature Set (15 Features)
| Feature Name | Type | Description |
| :--- | :--- | :--- |
| `req_count` | `float` | Total request count in window |
| `req_rate_per_sec` | `float` | Request rate (`req_count / window_seconds`) |
| `error_count` | `float` | Total HTTP errors in window |
| `error_rate` | `float` | HTTP error ratio (`error_count / max(1, req_count)`) |
| `latency_mean_ms` | `float` | Mean request latency in ms |
| `latency_median_ms` | `float` | Median request latency in ms |
| `latency_p95_ms` | `float` | 95th percentile request latency in ms |
| `latency_p99_ms` | `float` | 99th percentile request latency in ms |
| `latency_std_ms` | `float` | Standard deviation of latency |
| `cpu_utilization_mean` | `float` | Average CPU utilization (%) |
| `memory_utilization_mean` | `float` | Average memory utilization (%) |
| `child_span_count` | `float` | Count of downstream span dependencies |
| `error_span_count` | `float` | Count of spans with status `ERROR` |
| `missingness_ratio` | `float` | Proportion of expected metric signals missing |
| `freshness_lag_seconds` | `float` | Time delta between window end and latest signal |

---

## 3. Anomaly Detection & Incident Prediction Models

### Model A: Deterministic Statistical Baselines
1. **Configurable Threshold Detector (`ThresholdAnomalyDetector`)**:
   - Detects rule-based threshold violations:
     - Error rate spike: `error_rate > 0.10`
     - Severe latency degradation: `latency_p95_ms > 500.0`
     - Metric staleness: `freshness_lag_seconds > 120.0`
   - Score: $[0.0, 1.0]$ based on maximum threshold breach ratio.
2. **Rolling Median & MAD Detector (`RollingMadAnomalyDetector`)**:
   - Uses rolling window history to compute median $\tilde{x}$ and Median Absolute Deviation ($\text{MAD}$).
   - Modified Z-Score: $M_i = \frac{0.6745 \cdot (x_i - \tilde{x})}{\max(\text{MAD}, 1e-4)}$.
   - Anomaly triggered when $|M_i| > 3.5$.
   - Defensive guards for zero variance ($\text{MAD} = 0$), flat metrics, and sparse history.

### Model B: Isolation Forest (`IsolationForestDetector`)
- Uses Scikit-learn `IsolationForest(n_estimators=100, contamination=0.05, random_state=42)`.
- Preprocessing: `SimpleImputer(strategy="median")` + `StandardScaler()`.
- Score Normalization: Transforms Scikit-learn raw decision score $s \in (-\infty, \infty)$ into a calibrated $[0.0, 1.0]$ anomaly score via logistic transformation.
- Feature Order Verification: Validates strict feature schema ordering at inference time.
- Persistence: Stored via `joblib` in `ml/artifacts/isolation_forest.joblib` with serialized metadata in the database.

### Model C: Supervised Incident-Risk Prediction (`IncidentRiskPredictor`)
- **Target Label**: Binary indicator $y \in \{0, 1\}$ denoting whether an incident begins within future horizon $H = 300\text{s}$ (5 minutes).
- **Embargo Window**: Excludes windows during active fault periods or immediately preceding a fault by a buffer zone to prevent label leakage.
- **Classifier**: `RandomForestClassifier(n_estimators=100, random_state=42)` or `HistGradientBoostingClassifier`.
- **Chronological Split**: Strict temporal train/test split (First 65% for train, 10% embargo, Final 25% for test).
- **Safety Label Guard**: If positive historical incidents are fewer than the minimum validity threshold (< 10 incidents), model is registered as `UNAVAILABLE` or `EXPERIMENTAL` with clear diagnostic explanation.

---

## 4. Evaluation Protocol

- **Chronological Partitioning**: Untouched temporal split.
- **Anomaly Detection Metrics**:
  - Event-level Precision, Recall, F1 score.
  - False Alerts per Service-Hour.
  - Mean Detection Delay (seconds from fault onset to first alert).
- **Incident Prediction Metrics**:
  - Precision-Recall AUC (PR-AUC).
  - Precision, Recall, F1 score at optimal threshold.
  - Confusion Matrix.
  - Brier score for probability calibration.

---

## 5. Database Schema Changes & Alembic Migration

Alembic Migration `003_ml_detection_and_models.py` will introduce:
1. `ml_models`: Model Registry:
   - `id`: `varchar(64)` PK
   - `model_name`: `varchar(64)`
   - `model_type`: `varchar(32)` (`statistical_threshold`, `rolling_mad`, `isolation_forest`, `incident_predictor`)
   - `version`: `varchar(32)`
   - `status`: `varchar(32)` (`TRAINED`, `UNTRAINED`, `UNAVAILABLE`, `EXPERIMENTAL`)
   - `artifact_path`: `varchar(256)`
   - `feature_names`: `jsonb`
   - `hyperparameters`: `jsonb`
   - `metrics_json`: `jsonb`
   - `trained_at`: `timestamptz`
   - `created_at`: `timestamptz`
2. `anomaly_detections`: Detection Event Log:
   - `id`: `bigint` PK
   - `timestamp`: `timestamptz` (observation time)
   - `service_name`: `varchar(64)`
   - `detector_name`: `varchar(64)`
   - `model_version`: `varchar(32)`
   - `is_anomaly`: `boolean`
   - `anomaly_score`: `double precision`
   - `severity`: `varchar(16)` (`INFO`, `LOW`, `MEDIUM`, `HIGH`, `CRITICAL`)
   - `triggering_features`: `jsonb`
   - `explanation`: `text`
   - `trace_id`: `varchar(32)`
   - `created_at`: `timestamptz`
   - Composite index on `(service_name, timestamp)` and `(detector_name, timestamp)`.

---

## 6. API Contracts

1. `POST /api/v1/ml/anomaly-detection/run`:
   - Request: `service_name: str`, `detector_name: str` (`threshold`, `rolling_mad`, `isolation_forest`), `timestamp: datetime | None`, `window_seconds: int = 60`.
   - Response: `AnomalyDetectionResponse` (`is_anomaly`, `anomaly_score`, `severity`, `triggering_features`, `explanation`, `features_analyzed`).
2. `GET /api/v1/ml/anomalies`:
   - Query filters: `service_name`, `detector_name`, `severity`, `start_time`, `end_time`, `limit`, `offset`.
   - Response: `AnomalyListResponse`.
3. `GET /api/v1/ml/models`:
   - Response: `ModelListResponse` (all registered models).
4. `GET /api/v1/ml/models/{model_name}`:
   - Response: `ModelDetailResponse` (parameters, feature list, evaluation metrics, training timestamp).
5. `POST /api/v1/ml/incident-risk/predict`:
   - Request: `service_name: str`, `horizon_seconds: int = 300`, `timestamp: datetime | None`.
   - Response: `IncidentRiskResponse` (`service_name`, `risk_score`, `risk_level`, `model_status`, `predicted_incident_probability`).

---

## 7. Acceptance Criteria & Definition of Done

1. Telemetry-to-feature pipeline converts raw observations into leak-free feature vectors.
2. Baselines (Threshold and Rolling MAD) operate safely with zero variance and missing values.
3. Isolation Forest is fitted on normal baseline, serialized, reloaded, and produces bounded $[0, 1]$ anomaly scores.
4. Incident-risk prediction pipeline enforces chronological split, embargo window, and avoids feature leakage.
5. Database migration `003_ml_detection_and_models` executes cleanly with upgrade and downgrade.
6. REST endpoints for anomaly detection, model registry, and incident prediction are implemented and tested.
7. Automated tests verify zero future observation leakage into past windows.
8. All 61 existing tests continue passing without regression.
9. Documentation artifacts (`PHASE_3_PLAN.md`, `PHASE_3_DATA_READINESS.md`, `ML_FEATURES.md`, `ML_EVALUATION_PROTOCOL.md`, `PHASE_3_EVALUATION_REPORT.md`, `README.md`) are complete.
