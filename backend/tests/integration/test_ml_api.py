"""Integration tests for Phase 3 ML anomaly detection, model registry, and incident prediction APIs."""

from datetime import UTC, datetime, timedelta

from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.models.ml import AnomalyDetectionModel
from app.db.models.telemetry import MetricRecordModel
from app.ml.features import STANDARD_FEATURE_NAMES


def test_run_anomaly_detection_with_custom_features(
    client: TestClient, test_db_session: Session
) -> None:
    """Run anomaly detection with ad-hoc custom feature payload across baselines and models."""
    custom_feats = {
        "req_count": 50.0,
        "req_rate_per_sec": 5.0,
        "error_count": 25.0,
        "error_rate": 0.50,  # High error rate should breach threshold
        "latency_mean_ms": 300.0,
        "latency_median_ms": 280.0,
        "latency_p95_ms": 650.0,  # High latency breach
        "latency_p99_ms": 900.0,
        "latency_std_ms": 100.0,
        "cpu_utilization_mean": 0.85,
        "memory_utilization_mean": 0.70,
        "child_span_count": 5.0,
        "error_span_count": 3.0,
        "missingness_ratio": 0.0,
        "freshness_lag_seconds": 2.0,
    }

    payload = {
        "service_name": "order-service",
        "window_seconds": 60.0,
        "detector": "all",
        "persist_results": True,
        "custom_features": custom_feats,
    }

    resp = client.post("/api/v1/ml/anomaly-detection/run", json=payload)
    assert resp.status_code == status.HTTP_200_OK

    body = resp.json()
    assert body["service_name"] == "order-service"
    assert len(body["detections"]) >= 2  # threshold and rolling_mad at minimum

    # Threshold detector should have caught the breach
    threshold_res = next(
        (d for d in body["detections"] if d["detector_name"] == "threshold"),
        None,
    )
    assert threshold_res is not None
    assert threshold_res["is_anomaly"] is True
    assert threshold_res["severity"] in ("HIGH", "CRITICAL")
    assert "error_rate" in threshold_res["triggering_features"]


def test_run_anomaly_detection_from_db_telemetry(
    client: TestClient, test_db_session: Session
) -> None:
    """Test anomaly detection extracting features directly from persisted database telemetry."""
    now = datetime.now(UTC)
    metric = MetricRecordModel(
        service_name="payment-service",
        metric_name="http.server.requests.count",
        value=20.0,
        unit="count",
        timestamp=now - timedelta(seconds=15),
    )
    test_db_session.add(metric)
    test_db_session.commit()

    payload = {
        "service_name": "payment-service",
        "window_end": now.isoformat(),
        "window_seconds": 60.0,
        "detector": "threshold",
        "persist_results": False,
    }

    resp = client.post("/api/v1/ml/anomaly-detection/run", json=payload)
    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body["features"]["req_count"] == 20.0
    assert len(body["detections"]) == 1


def test_list_anomalies_endpoint_and_filtering(
    client: TestClient, test_db_session: Session
) -> None:
    """Test querying persisted anomaly records with pagination and service filtering."""
    now = datetime.now(UTC)

    # Seed 3 anomaly detection records directly
    rec1 = AnomalyDetectionModel(
        service_name="payment-service",
        detector_name="threshold",
        model_version="v1.0.0",
        timestamp=now - timedelta(minutes=5),
        is_anomaly=True,
        anomaly_score=0.85,
        severity="CRITICAL",
        triggering_features=["latency_p95_ms"],
        explanation="Latency critical threshold breached",
    )
    rec2 = AnomalyDetectionModel(
        service_name="order-service",
        detector_name="threshold",
        model_version="v1.0.0",
        timestamp=now - timedelta(minutes=2),
        is_anomaly=False,
        anomaly_score=0.10,
        severity="INFO",
        triggering_features=[],
        explanation="Nominal operation",
    )
    test_db_session.add_all([rec1, rec2])
    test_db_session.commit()

    # 1. Unfiltered query
    resp_all = client.get("/api/v1/ml/anomalies")
    assert resp_all.status_code == status.HTTP_200_OK
    data_all = resp_all.json()
    assert data_all["total"] >= 2

    # 2. Filter by service and is_anomaly=true
    resp_filtered = client.get("/api/v1/ml/anomalies?service_name=payment-service&is_anomaly=true")
    assert resp_filtered.status_code == status.HTTP_200_OK
    data_filtered = resp_filtered.json()
    assert len(data_filtered["items"]) >= 1
    assert data_filtered["items"][0]["service_name"] == "payment-service"
    assert data_filtered["items"][0]["is_anomaly"] is True


def test_list_and_get_ml_models(client: TestClient, test_db_session: Session) -> None:
    """Test model registry retrieval and individual model inspection."""
    # 1. List all models
    resp_list = client.get("/api/v1/ml/models")
    assert resp_list.status_code == status.HTTP_200_OK
    body_list = resp_list.json()
    assert body_list["total"] >= 2
    model_names = [m["model_name"] for m in body_list["models"]]
    assert "threshold_baseline" in model_names

    # 2. Get specific valid model
    resp_get = client.get("/api/v1/ml/models/threshold_baseline")
    assert resp_get.status_code == status.HTTP_200_OK
    body_get = resp_get.json()
    assert body_get["model_name"] == "threshold_baseline"
    assert body_get["status"] == "AVAILABLE"

    # 3. Get non-existent model (returns RFC 7807 problem details 404)
    resp_missing = client.get("/api/v1/ml/models/non-existent-model")
    assert resp_missing.status_code == status.HTTP_404_NOT_FOUND
    err_body = resp_missing.json()
    assert err_body["code"] == "RESOURCE_NOT_FOUND"


def test_predict_incident_risk_endpoint(client: TestClient, test_db_session: Session) -> None:
    """Test incident risk prediction endpoint with custom features."""
    custom_feats = dict.fromkeys(STANDARD_FEATURE_NAMES, 10.0)
    custom_feats["error_rate"] = 0.50
    custom_feats["latency_p95_ms"] = 900.0

    payload = {
        "service_name": "payment-service",
        "horizon_seconds": 300.0,
        "custom_features": custom_feats,
    }

    resp = client.post("/api/v1/ml/incident-risk/predict", json=payload)
    # If model is trained and available, returns 200 OK
    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body["service_name"] == "payment-service"
    assert 0.0 <= body["risk_probability"] <= 1.0
    assert body["risk_level"] in ("LOW", "MEDIUM", "HIGH", "CRITICAL")
    assert "predicted_incident" in body


def test_validation_error_on_invalid_parameters(client: TestClient) -> None:
    """Ensure invalid payload schemas return RFC 7807 422 problem details."""
    payload = {
        "service_name": "x",  # Too short (min_length=2)
        "window_seconds": -5.0,  # Negative duration
    }
    resp = client.post("/api/v1/ml/anomaly-detection/run", json=payload)
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    body = resp.json()
    assert body["code"] == "VALIDATION_ERROR"
