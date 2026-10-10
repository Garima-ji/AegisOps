"""Unit tests for resilience retries, degradation handling, artifact validation, and platform manifest."""

from pathlib import Path

import pytest

from app.core.resilience import GracefulDegradationManager, retry_with_backoff
from app.core.versioning import ArtifactValidator, get_platform_manifest


def test_retry_with_backoff_succeeds_after_transient_failure() -> None:
    """Verify decorator retries transient failure and succeeds when function recovers."""
    attempts = 0

    @retry_with_backoff(max_attempts=3, initial_delay=0.01, retryable_exceptions=(ValueError,))
    def flaky_func() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 2:
            raise ValueError("Temporary database glitch")
        return "success"

    result = flaky_func()
    assert result == "success"
    assert attempts == 2


def test_retry_with_backoff_exhausts_and_raises() -> None:
    """Verify decorator raises when max_attempts is exceeded."""

    @retry_with_backoff(max_attempts=2, initial_delay=0.01, retryable_exceptions=(RuntimeError,))
    def always_fails() -> None:
        raise RuntimeError("Fatal connection failure")

    with pytest.raises(RuntimeError, match="Fatal connection failure"):
        always_fails()


def test_retry_with_backoff_disallows_non_idempotent() -> None:
    """Verify non-idempotent operations are never retried automatically."""
    attempts = 0

    @retry_with_backoff(max_attempts=3, is_idempotent=False)
    def non_idempotent_mutation() -> None:
        nonlocal attempts
        attempts += 1
        raise OSError("Network write failed")

    with pytest.raises(IOError):
        non_idempotent_mutation()

    assert attempts == 1  # Only tried once


def test_artifact_validator_nonexistent_file(tmp_path: Path) -> None:
    """Verify ArtifactValidator correctly flags missing files."""
    missing = tmp_path / "nonexistent.joblib"
    meta = ArtifactValidator.inspect_file(missing)
    assert not meta.exists
    assert meta.error is not None


def test_artifact_validator_valid_file(tmp_path: Path) -> None:
    """Verify ArtifactValidator calculates valid SHA-256 hash for real files."""
    valid_file = tmp_path / "model.joblib"
    valid_file.write_bytes(b"dummy serialized model bytes")

    meta = ArtifactValidator.inspect_file(valid_file)
    assert meta.exists
    assert meta.size_bytes > 0
    assert meta.sha256_hash is not None
    assert len(meta.sha256_hash) == 64
    assert meta.compatible


def test_platform_manifest_structure() -> None:
    """Verify PlatformManifest produces complete schema with feature and threshold metadata."""
    manifest = get_platform_manifest()
    m_dict = manifest.to_dict()

    assert "platform_version" in m_dict
    assert "feature_schema_version" in m_dict
    assert "detector_thresholds" in m_dict
    assert "rca_algorithm_version" in m_dict
    assert "agent_workflow_version" in m_dict


def test_graceful_degradation_handlers() -> None:
    """Verify degradation managers produce structured fallback outputs."""
    deg_model = GracefulDegradationManager.handle_missing_model_artifact("isolation_forest.joblib")
    assert deg_model["status"] == "degraded"
    assert "warning" in deg_model

    deg_traces = GracefulDegradationManager.handle_incomplete_traces("order-service", 2)
    assert deg_traces["status"] == "abstained"
    assert deg_traces["reason"] == "LOW_TRACE_COVERAGE"

    deg_sim = GracefulDegradationManager.handle_simulation_verification_failure(
        "stop_fault_scenario", expected_state="stopped", observed_state="active"
    )
    assert not deg_sim["verified"]
    assert deg_sim["status"] == "SIMULATION_VERIFICATION_FAILED"
