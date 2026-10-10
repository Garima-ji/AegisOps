# AegisOps — Operational Feature Engineering Specification

This document provides the definitive mathematical definitions, windowing rules, leakage guards, and operational semantics for the AegisOps Phase 3 feature engineering pipeline (`app.ml.features`).

---

## 1. Windowing Semantics and Leakage Prevention

### 1.1 Mathematical Window Boundary
For an observation cutoff timestamp $T \in \text{UTC}$ and a configurable window duration $W$ (default $W = 60.0\,\text{s}$):

$$\mathcal{W}(T, W) = \{ x \in \text{Observations} \mid T - W < t(x) \le T \land \text{service}(x) = S \}$$

### 1.2 Zero Future-Leakage Invariant
**Strict Guarantee:** Any telemetry observation with timestamp $t(x) > T$ is strictly discarded prior to aggregation.

$$\forall x \in \text{Observations}, \quad t(x) > T \implies x \notin \mathcal{W}(T, W)$$

This guarantee is enforced in code by `extract_window_features` and validated by automated unit tests (`test_strict_temporal_leakage_protection`), demonstrating that injecting arbitrary future outliers produces numerically identical feature vectors for earlier cutoffs.

---

## 2. Canonical Feature Schema (`STANDARD_FEATURE_NAMES`)

The feature vector $\mathbf{x} \in \mathbb{R}^{15}$ is strictly ordered to ensure deterministic matrix alignment between training pipelines and inference runtimes.

| Index | Feature Name | Unit | Type | Description |
|---|---|---|---|---|
| 0 | `req_count` | Count | Float | Number of incoming HTTP requests recorded in window $\mathcal{W}$ |
| 1 | `req_rate_per_sec` | Req/s | Float | Ingress request throughput $\frac{\text{req\_count}}{W}$ |
| 2 | `error_count` | Count | Float | Number of failed HTTP requests (status $\ge 500$) in window |
| 3 | `error_rate` | Ratio $[0, 1]$ | Float | HTTP error fraction $\frac{\text{error\_count}}{\max(1, \text{req\_count})}$ |
| 4 | `latency_mean_ms` | Milliseconds | Float | Arithmetic mean duration of completed request spans |
| 5 | `latency_median_ms` | Milliseconds | Float | 50th percentile (median) duration |
| 6 | `latency_p95_ms` | Milliseconds | Float | 95th percentile duration |
| 7 | `latency_p99_ms` | Milliseconds | Float | 99th percentile duration |
| 8 | `latency_std_ms` | Milliseconds | Float | Sample standard deviation of request duration |
| 9 | `cpu_utilization_mean` | Ratio $[0, 1]$ | Float | Mean host/container CPU utilization across gauges |
| 10 | `memory_utilization_mean` | Ratio $[0, 1]$ | Float | Mean resident memory utilization across gauges |
| 11 | `child_span_count` | Count | Float | Downstream dependency RPC spans initiated by service |
| 12 | `error_span_count` | Count | Float | Downstream dependency RPC spans returning error status |
| 13 | `missingness_ratio` | Ratio $[0, 1]$ | Float | Fraction of standard metric channels absent from window |
| 14 | `freshness_lag_seconds` | Seconds | Float | Elapsed time since newest observation $T - \max_{x \in \mathcal{W}} t(x)$ |

---

## 3. Metric Fallback & Missingness Transparency

### 3.1 Transparent Outage Defense
Traditional monitoring pipelines frequently impute missing metrics with $0.0$ or running medians. In an operations environment, **a sudden silence in telemetry is itself a critical symptom of network partition, process crash, or agent failure**. Imputing nominal metrics conceals active outages.

AegisOps defends against this by:
1. **Never imputing missing raw observations as healthy traffic.**
2. Computing **`freshness_lag_seconds`**:
   $$\text{freshness\_lag} = T - \max_{x \in \mathcal{W}} t(x)$$
   If no telemetry was emitted during the window, $\text{freshness\_lag} \ge W$, which directly triggers baseline freshness alerts.
3. Computing **`missingness_ratio`**:
   $$\text{missingness\_ratio} = 1.0 - \frac{|\mathcal{M}_{\text{observed}} \cap \mathcal{M}_{\text{expected}}|}{|\mathcal{M}_{\text{expected}}|}$$
   where $\mathcal{M}_{\text{expected}} = \{\text{duration}, \text{requests}, \text{errors}, \text{cpu}, \text{memory}\}$.

### 3.2 Trace Duration Fallback
If direct metric duration gauges are absent, `extract_window_features` computes duration statistics from trace span lifetimes within the window:

$$\text{duration}(s) = \min(10000.0, \max(0.1, t_{\text{end}}(s) - t_{\text{start}}(s)))$$

---

## 4. Supervised Incident Target Labeling & Embargoing

### 4.1 Forward-Looking Target Formulation
For supervised incident-risk prediction (`app.ml.incident_predictor`), the label target indicates whether a ground-truth fault starts in the forward horizon $(T, T + H]$:

$$y(T, S) = \begin{cases} 1 & \text{if } \exists \text{ incident } (\tau_{\text{start}}, \tau_{\text{end}}) \text{ on service } S \text{ s.t. } T < \tau_{\text{start}} \le T + H \\ 0 & \text{otherwise} \end{cases}$$

Default prediction horizon: $H = 300.0\,\text{s}$ (5 minutes).

### 4.2 Split Embargoing
To prevent autocorrelation leakage across chronological splits:
1. Telemetry observed during an active fault ($\tau_{\text{start}} \le T \le \tau_{\text{end}}$) is labeled with `is_incident = 1` and flagged `is_embargoed = True`.
2. A buffer window of duration $E$ (default $E = 60.0\,\text{s}$) preceding fault onset is excluded from negative training sets (`is_embargoed = True`) to prevent subtle precursors from acting as negative samples.
