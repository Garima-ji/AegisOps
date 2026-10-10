"""End-to-End lifecycle test: Telemetry Generation -> Ingestion -> Query -> Fault Injection -> Labels Retrieval."""

from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.ingestion.generator import DeterministicTelemetryGenerator


def test_complete_telemetry_and_fault_injection_lifecycle(
    client: TestClient, test_db_session: Session
) -> None:
    """Execute complete end-to-end operational lifecycle:

    1. Generate baseline normal telemetry from cooperating services (order-service -> payment-service).
    2. Ingest metrics, logs, and distributed trace spans via REST API.
    3. Query metrics and trace waterfall tree to confirm persistence and correlation.
    4. Register a controlled fault-injection scenario (latency_spike on payment-service).
    5. Generate and ingest degraded telemetry under active fault conditions.
    6. Query degraded traces and verify observable symptoms (significantly higher duration).
    7. Retrieve and verify ground-truth labels for ML model training and evaluation.
    8. Stop the experiment and verify clean state recovery.
    """
    generator = DeterministicTelemetryGenerator(seed=101)

    # -------------------------------------------------------------------------
    # Step 1 & 2: Generate and Ingest Baseline Normal Telemetry
    # -------------------------------------------------------------------------
    normal_batch = generator.generate_normal_traffic(count=5)
    assert len(normal_batch.metrics) > 0
    assert len(normal_batch.logs) > 0
    assert len(normal_batch.traces) > 0

    # Ingest metrics
    metrics_payload = {"items": [m.model_dump(mode="json") for m in normal_batch.metrics]}
    resp_metrics = client.post("/api/v1/telemetry/metrics", json=metrics_payload)
    assert resp_metrics.status_code == status.HTTP_200_OK
    assert resp_metrics.json()["status"] == "success"

    # Ingest logs
    logs_payload = {"items": [item.model_dump(mode="json") for item in normal_batch.logs]}
    resp_logs = client.post("/api/v1/telemetry/logs", json=logs_payload)
    assert resp_logs.status_code == status.HTTP_200_OK
    assert resp_logs.json()["status"] == "success"

    # Ingest traces
    traces_payload = {"items": [t.model_dump(mode="json") for t in normal_batch.traces]}
    resp_traces = client.post("/api/v1/telemetry/traces", json=traces_payload)
    assert resp_traces.status_code == status.HTTP_200_OK
    assert resp_traces.json()["status"] == "success"

    # -------------------------------------------------------------------------
    # Step 3: Query Telemetry & Verify Microservice Registry
    # -------------------------------------------------------------------------
    # Check services registered
    resp_services = client.get("/api/v1/services")
    assert resp_services.status_code == status.HTTP_200_OK
    services_list = resp_services.json()["items"]
    registered_names = {s["name"] for s in services_list}
    assert "order-service" in registered_names
    assert "payment-service" in registered_names

    # Check waterfall tree of first normal transaction
    first_trace_id = normal_batch.transactions[0].trace_id
    resp_waterfall = client.get(f"/api/v1/telemetry/traces/{first_trace_id}")
    assert resp_waterfall.status_code == status.HTTP_200_OK
    wf = resp_waterfall.json()
    assert wf["trace_id"] == first_trace_id
    assert wf["span_count"] >= 3
    # Baseline payment duration is fast (< 100ms)
    payment_span = next(s for s in wf["spans"] if s["service_name"] == "payment-service")
    assert payment_span["duration_ms"] < 200.0

    # -------------------------------------------------------------------------
    # Step 4: Launch Controlled Fault-Injection Experiment
    # -------------------------------------------------------------------------
    exp_payload = {
        "scenario_name": "payment_gateway_latency_spike",
        "description": "Controlled 750ms latency degradation on payment-service",
        "target_service": "payment-service",
        "fault_type": "latency_spike",
        "parameters": {"added_latency_ms": 750.0, "jitter_ms": 20.0},
        "duration_seconds": 60,
        "initiating_service": "payment-service",
        "symptom_services": ["order-service"],
    }
    resp_exp = client.post("/api/v1/experiments", json=exp_payload)
    assert resp_exp.status_code == status.HTTP_201_CREATED
    exp_data = resp_exp.json()
    exp_id = exp_data["id"]
    assert exp_data["status"] == "ACTIVE"

    # -------------------------------------------------------------------------
    # Step 5 & 6: Generate Degraded Telemetry & Ingest
    # -------------------------------------------------------------------------
    fault_batch = generator.generate_fault_traffic(
        fault_type="latency_spike",
        experiment_id=exp_id,
        count=5,
        custom_params={"added_latency_ms": 750.0},
    )

    client.post(
        "/api/v1/telemetry/traces",
        json={"items": [t.model_dump(mode="json") for t in fault_batch.traces]},
    )
    client.post(
        "/api/v1/telemetry/metrics",
        json={"items": [m.model_dump(mode="json") for m in fault_batch.metrics]},
    )

    # Query degraded traces: Check that payment-service duration elevated above 700ms
    degraded_trace_id = fault_batch.transactions[0].trace_id
    resp_deg_wf = client.get(f"/api/v1/telemetry/traces/{degraded_trace_id}")
    assert resp_deg_wf.status_code == status.HTTP_200_OK
    deg_wf = resp_deg_wf.json()
    deg_payment_span = next(s for s in deg_wf["spans"] if s["service_name"] == "payment-service")
    assert deg_payment_span["duration_ms"] >= 700.0

    # -------------------------------------------------------------------------
    # Step 7: Retrieve and Verify Isolated Ground-Truth Labels
    # -------------------------------------------------------------------------
    resp_labels = client.get(f"/api/v1/experiments/{exp_id}/labels")
    assert resp_labels.status_code == status.HTTP_200_OK
    labels_data = resp_labels.json()
    assert labels_data["total"] >= 2

    # Check initiating service label
    initiator = next(
        lbl for lbl in labels_data["labels"] if lbl["service_name"] == "payment-service"
    )
    assert initiator["is_fault"] is True
    assert initiator["fault_type"] == "latency_spike"
    assert initiator["metadata"]["role"] == "initiator"

    # Check symptom service label
    symptom = next(lbl for lbl in labels_data["labels"] if lbl["service_name"] == "order-service")
    assert symptom["is_fault"] is True
    assert symptom["metadata"]["role"] == "symptom"

    # -------------------------------------------------------------------------
    # Step 8: Safely Stop Experiment & Cleanup
    # -------------------------------------------------------------------------
    resp_stop = client.post(f"/api/v1/experiments/{exp_id}/stop")
    assert resp_stop.status_code == status.HTTP_200_OK
    assert resp_stop.json()["status"] == "STOPPED"
