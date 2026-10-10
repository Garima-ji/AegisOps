"""Unit tests for telemetry data quality, PSI calculation, and drift monitoring."""

import numpy as np

from app.ml.drift import DriftCategory, DriftMonitor, calculate_psi


def test_calculate_psi_identical_distributions() -> None:
    """Verify PSI is near 0 for identical sample distributions."""
    data = [10.0, 20.0, 30.0, 40.0, 50.0]
    psi = calculate_psi(data, data)
    assert psi == 0.0


def test_calculate_psi_shifted_distribution() -> None:
    """Verify PSI increases significantly for shifted distribution."""
    expected = [10.0, 12.0, 14.0, 15.0, 18.0, 20.0, 22.0]
    actual = [100.0, 120.0, 140.0, 150.0, 180.0, 200.0, 220.0]
    psi = calculate_psi(expected, actual)
    assert psi >= 0.25  # Significant critical drift


def test_telemetry_quality_checks_out_of_bounds() -> None:
    """Verify data quality checks detect invalid CPU, memory, and latency values."""
    monitor = DriftMonitor()
    bad_rows = [
        {
            "cpu_utilization_mean": 1.5,  # Invalid > 1.0
            "memory_utilization_mean": -0.2,  # Invalid < 0.0
            "latency_mean_ms": -5.0,  # Negative latency
            "freshness_lag_seconds": 450.0,  # Stale > 300s
        }
    ]

    report = monitor.evaluate_drift(bad_rows)
    assert not report.data_quality_passed
    assert report.overall_category == DriftCategory.DATA_QUALITY_ISSUE
    assert len(report.quality_checks) > 0


def test_healthy_telemetry_reports_no_drift() -> None:
    """Verify normal telemetry without shifts is classified as NO_DRIFT."""
    monitor = DriftMonitor()
    rng = np.random.default_rng(seed=42)
    normal_rows = []
    for _ in range(50):
        row = {}
        for feat, b_info in monitor.baseline_stats.items():
            val = float(rng.normal(b_info["mean"], b_info.get("std", 1.0)))
            if "min" in b_info and "max" in b_info:
                val = float(np.clip(val, b_info["min"], b_info["max"]))
            elif "min" in b_info:
                val = max(b_info["min"], val)
            row[feat] = val
        normal_rows.append(row)

    report = monitor.evaluate_drift(normal_rows)
    assert report.data_quality_passed
    assert report.overall_category == DriftCategory.NO_DRIFT
