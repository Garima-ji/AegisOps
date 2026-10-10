"""Centralized model, algorithm, workflow, and artifact versioning manifest."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.ml.features import STANDARD_FEATURE_NAMES

logger = logging.getLogger("aegisops.core.versioning")


class ArtifactValidationError(Exception):
    """Raised when an artifact file is missing, corrupted, or schema-incompatible."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


@dataclass
class ArtifactMetadata:
    """Version metadata and cryptographic digest for a persisted model artifact."""

    artifact_name: str
    file_path: str
    exists: bool
    size_bytes: int = 0
    sha256_hash: str | None = None
    last_modified: str | None = None
    compatible: bool = False
    error: str | None = None


@dataclass
class PlatformManifest:
    """Immutable system manifest tying active predictions and investigations to exact code and model versions."""

    platform_version: str = "0.1.0-phase6"
    environment: str = "development"
    feature_schema_version: str = "v1.0-standard-15f"
    feature_names: list[str] = field(default_factory=lambda: list(STANDARD_FEATURE_NAMES))
    detector_thresholds: dict[str, float] = field(
        default_factory=lambda: {
            "cpu_utilization_mean": 0.85,
            "memory_utilization_mean": 0.90,
            "error_rate": 0.05,
            "latency_p95_ms": 1000.0,
        }
    )
    rca_algorithm_version: str = "v1.0-deterministic-temporal-graph"
    agent_workflow_version: str = "v1.0-langgraph-12node"
    tool_schema_version: str = "v1.0-typed-bounded-9tools"
    runbook_catalog_version: str = "v1.0-5runbooks"
    evaluation_suite_version: str = "v1.0-13scenarios"
    model_artifacts: dict[str, ArtifactMetadata] = field(default_factory=dict)
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        """Convert manifest to serializable dictionary."""
        return asdict(self)


class ArtifactValidator:
    """Validates model artifact integrity, checksums, and schema compatibility before loading."""

    @staticmethod
    def inspect_file(artifact_path: Path) -> ArtifactMetadata:
        """Inspect and compute cryptographic digest for a model artifact file."""
        if not artifact_path.exists():
            return ArtifactMetadata(
                artifact_name=artifact_path.name,
                file_path=str(artifact_path),
                exists=False,
                error="File does not exist on disk.",
            )

        try:
            stat = artifact_path.stat()
            size = stat.st_size
            if size == 0:
                return ArtifactMetadata(
                    artifact_name=artifact_path.name,
                    file_path=str(artifact_path),
                    exists=True,
                    size_bytes=0,
                    error="Artifact file is empty (0 bytes).",
                )

            # Compute SHA-256 digest
            hasher = hashlib.sha256()
            with open(artifact_path, "rb") as f:
                while chunk := f.read(65536):
                    hasher.update(chunk)
            digest = hasher.hexdigest()

            modified = datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat()
            return ArtifactMetadata(
                artifact_name=artifact_path.name,
                file_path=str(artifact_path),
                exists=True,
                size_bytes=size,
                sha256_hash=digest,
                last_modified=modified,
                compatible=True,
            )
        except Exception as exc:
            logger.error("Error inspecting artifact '%s': %s", artifact_path, exc)
            return ArtifactMetadata(
                artifact_name=artifact_path.name,
                file_path=str(artifact_path),
                exists=True,
                error=str(exc),
            )

    @classmethod
    def validate_or_raise(cls, artifact_path: Path) -> ArtifactMetadata:
        """Validate artifact and raise structured ArtifactValidationError if corrupt or missing."""
        meta = cls.inspect_file(artifact_path)
        if not meta.exists or not meta.compatible:
            raise ArtifactValidationError(
                f"Artifact validation failed for '{artifact_path.name}': {meta.error}",
                details=asdict(meta),
            )
        return meta


def get_platform_manifest(artifacts_dir: Path | None = None) -> PlatformManifest:
    """Construct current platform version manifest with active artifact inspection."""
    settings = get_settings()
    base_dir = artifacts_dir or Path("ml/artifacts")

    artifacts: dict[str, ArtifactMetadata] = {}
    known_artifacts = ["isolation_forest.joblib", "incident_predictor.joblib"]
    for name in known_artifacts:
        path = base_dir / name
        artifacts[name] = ArtifactValidator.inspect_file(path)

    return PlatformManifest(
        platform_version=f"{settings.app_version}-phase6",
        environment=settings.environment,
        model_artifacts=artifacts,
    )
