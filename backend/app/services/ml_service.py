"""Domain service orchestrating ML anomaly detection, model registry, and incident prediction."""

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.core.exceptions import ModelUnavailableError, ResourceNotFoundError
from app.db.models.ml import AnomalyDetectionModel, MLModelModel
from app.ml.baselines import (
    AnomalyResult,
    RollingMadAnomalyDetector,
    ThresholdAnomalyDetector,
)
from app.ml.features import (
    STANDARD_FEATURE_NAMES,
    extract_features_from_db,
)
from app.ml.incident_predictor import IncidentRiskPredictor
from app.ml.isolation_forest import IsolationForestDetector
from app.schemas.ml import (
    AnomalyDetectionRecordSchema,
    AnomalyDetectionResultSchema,
    AnomalyDetectionRunRequest,
    AnomalyDetectionRunResponse,
    AnomalyListResponse,
    IncidentRiskPredictRequest,
    IncidentRiskPredictResponse,
    MLModelListResponse,
    MLModelResponse,
)

logger = logging.getLogger("aegisops.ml.service")

# Default locations for persisted artifacts
ARTIFACTS_DIR = Path("ml/artifacts")
IFOREST_ARTIFACT_PATH = ARTIFACTS_DIR / "isolation_forest.joblib"
INCIDENT_PREDICTOR_ARTIFACT_PATH = ARTIFACTS_DIR / "incident_predictor.joblib"


class MLService:
    """Service encapsulating feature extraction, detector dispatch, persistence, and inference."""

    def __init__(self, artifacts_dir: Path = ARTIFACTS_DIR) -> None:
        self.artifacts_dir = artifacts_dir
        self.threshold_detector = ThresholdAnomalyDetector()
        self.rolling_mad_detector = RollingMadAnomalyDetector()

        self._iforest_cache: IsolationForestDetector | None = None
        self._predictor_cache: IncidentRiskPredictor | None = None

    def _get_isolation_forest(self) -> IsolationForestDetector:
        """Retrieve or lazy-load Isolation Forest model."""
        if self._iforest_cache is not None and self._iforest_cache.is_fitted:
            return self._iforest_cache

        artifact_path = self.artifacts_dir / "isolation_forest.joblib"
        if not artifact_path.exists():
            raise ModelUnavailableError(
                "Isolation Forest detector is not fitted or artifact was not found.",
                details={
                    "artifact_path": str(artifact_path),
                    "resolution": "Execute 'python scripts/train_models.py'",
                },
            )

        try:
            detector = IsolationForestDetector.load(artifact_path)
            self._iforest_cache = detector
            return detector
        except Exception as exc:
            logger.error("Failed to load Isolation Forest artifact: %s", exc)
            raise ModelUnavailableError(
                f"Failed to load Isolation Forest model: {exc}",
                details={"artifact_path": str(artifact_path)},
            ) from exc

    def _get_incident_predictor(self) -> IncidentRiskPredictor:
        """Retrieve or lazy-load supervised Incident Risk Predictor."""
        if self._predictor_cache is not None and self._predictor_cache.is_fitted:
            return self._predictor_cache

        artifact_path = self.artifacts_dir / "incident_predictor.joblib"
        if not artifact_path.exists():
            raise ModelUnavailableError(
                "Incident risk predictor is not fitted or artifact was not found.",
                details={
                    "artifact_path": str(artifact_path),
                    "resolution": "Train model using 'python scripts/train_models.py' after sufficient incident labels exist",
                },
            )

        try:
            predictor = IncidentRiskPredictor.load(artifact_path)
            if predictor.status == "UNAVAILABLE" or not predictor.is_fitted:
                raise ModelUnavailableError(
                    "Incident risk predictor is marked UNAVAILABLE due to insufficient ground-truth labels.",
                    details={"status": predictor.status, "metadata": predictor.metadata},
                )
            self._predictor_cache = predictor
            return predictor
        except ModelUnavailableError:
            raise
        except Exception as exc:
            logger.error("Failed to load Incident Risk Predictor artifact: %s", exc)
            raise ModelUnavailableError(
                f"Failed to load Incident Risk Predictor: {exc}",
                details={"artifact_path": str(artifact_path)},
            ) from exc

    def run_anomaly_detection(
        self,
        db: Session,
        req: AnomalyDetectionRunRequest,
    ) -> AnomalyDetectionRunResponse:
        """Run requested anomaly detectors on windowed features and optionally persist detections."""
        window_end = req.window_end or datetime.now(UTC)
        if window_end.tzinfo is None:
            window_end = window_end.replace(tzinfo=UTC)
        else:
            window_end = window_end.astimezone(UTC)

        window_start = window_end - timedelta(seconds=req.window_seconds)

        # 1. Feature extraction
        if req.custom_features is not None:
            features = {k: float(v) for k, v in req.custom_features.items()}
            # Pad missing standard features with 0.0
            for sf in STANDARD_FEATURE_NAMES:
                features.setdefault(sf, 0.0)
        else:
            features = extract_features_from_db(
                session=db,
                service_name=req.service_name,
                window_end=window_end,
                window_seconds=req.window_seconds,
            )

        detection_results: list[AnomalyResult] = []

        # 2. Dispatch detectors
        if req.detector in ("all", "threshold"):
            detection_results.append(
                self.threshold_detector.detect(
                    features=features,
                    service_name=req.service_name,
                    timestamp=window_end,
                )
            )

        if req.detector in ("all", "rolling_mad"):
            detection_results.append(
                self.rolling_mad_detector.detect(
                    features=features,
                    service_name=req.service_name,
                    timestamp=window_end,
                )
            )

        if req.detector in ("all", "isolation_forest"):
            try:
                iforest = self._get_isolation_forest()
                detection_results.append(
                    iforest.detect(
                        features=features,
                        service_name=req.service_name,
                        timestamp=window_end,
                    )
                )
            except ModelUnavailableError:
                if req.detector == "isolation_forest":
                    raise
                # For "all", if Isolation Forest is not trained yet, note its absence gracefully
                logger.info(
                    "Isolation Forest detector artifact unavailable; continuing with baselines."
                )

        # 3. Persistence
        if req.persist_results:
            for res in detection_results:
                model_row = AnomalyDetectionModel(
                    service_name=req.service_name,
                    detector_name=res.detector_name,
                    model_version="v1.0.0",
                    timestamp=window_end,
                    is_anomaly=res.is_anomaly,
                    anomaly_score=res.anomaly_score,
                    severity=res.severity,
                    triggering_features=res.triggering_features,
                    explanation=res.explanation,
                )
                db.add(model_row)
            db.commit()

        schema_detections = [
            AnomalyDetectionResultSchema(
                detector_name=res.detector_name,
                is_anomaly=res.is_anomaly,
                anomaly_score=res.anomaly_score,
                severity=res.severity,  # type: ignore[arg-type]
                triggering_features=res.triggering_features,
                explanation=res.explanation,
                timestamp=res.timestamp,
                service_name=res.service_name,
                features=features,
            )
            for res in detection_results
        ]

        return AnomalyDetectionRunResponse(
            service_name=req.service_name,
            window_start=window_start,
            window_end=window_end,
            features=features,
            detections=schema_detections,
        )

    def list_anomalies(
        self,
        db: Session,
        service_name: str | None = None,
        detector_name: str | None = None,
        severity: str | None = None,
        is_anomaly: bool | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> AnomalyListResponse:
        """Query persisted anomaly detection events with bounded pagination."""
        limit = max(1, min(limit, 500))
        offset = max(0, offset)

        query = select(AnomalyDetectionModel)

        if service_name:
            query = query.where(AnomalyDetectionModel.service_name == service_name)
        if detector_name:
            query = query.where(AnomalyDetectionModel.detector_name == detector_name)
        if severity:
            query = query.where(AnomalyDetectionModel.severity == severity.upper())
        if is_anomaly is not None:
            query = query.where(AnomalyDetectionModel.is_anomaly == is_anomaly)
        if start_time:
            query = query.where(AnomalyDetectionModel.timestamp >= start_time)
        if end_time:
            query = query.where(AnomalyDetectionModel.timestamp <= end_time)

        # Count total
        count_query = select(func.count()).select_from(query.subquery())
        total = db.scalar(count_query) or 0

        # Fetch records
        records_query = (
            query.order_by(desc(AnomalyDetectionModel.timestamp)).limit(limit).offset(offset)
        )
        rows = db.scalars(records_query).all()

        items = [
            AnomalyDetectionRecordSchema(
                id=str(r.id),
                service_name=r.service_name,
                detector_name=r.detector_name,
                timestamp=r.timestamp,
                is_anomaly=r.is_anomaly,
                anomaly_score=r.anomaly_score,
                severity=r.severity,
                triggering_features=r.triggering_features or [],
                explanation=r.explanation or "",
                created_at=r.created_at,
            )
            for r in rows
        ]

        return AnomalyListResponse(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
        )

    def list_models(self, db: Session) -> MLModelListResponse:
        """List registered ML models and baselines."""
        rows = db.scalars(select(MLModelModel).order_by(MLModelModel.model_name)).all()

        if rows:
            models = [
                MLModelResponse(
                    model_name=r.model_name,
                    model_version=r.version,
                    model_type=r.model_type,
                    status=r.status,  # type: ignore[arg-type]
                    last_trained_at=r.trained_at,
                    artifact_path=r.artifact_path,
                    feature_schema_version="v1",
                    evaluation_summary=r.metrics_json or {},
                    created_at=r.created_at,
                )
                for r in rows
            ]
            return MLModelListResponse(models=models, total=len(models))

        # Default static entries if database has not been seeded yet
        iforest_path = self.artifacts_dir / "isolation_forest.joblib"
        incident_path = self.artifacts_dir / "incident_predictor.joblib"

        static_models = [
            MLModelResponse(
                model_name="threshold_baseline",
                model_version="v1.0.0",
                model_type="deterministic_rule",
                status="AVAILABLE",
                last_trained_at=None,
                artifact_path=None,
                feature_schema_version="v1",
                evaluation_summary={
                    "description": "Deterministic threshold rules for latency, error rate, freshness"
                },
                created_at=datetime.now(UTC),
            ),
            MLModelResponse(
                model_name="rolling_mad_baseline",
                model_version="v1.0.0",
                model_type="statistical_robust",
                status="AVAILABLE",
                last_trained_at=None,
                artifact_path=None,
                feature_schema_version="v1",
                evaluation_summary={
                    "description": "Rolling Median and Median Absolute Deviation modified Z-score"
                },
                created_at=datetime.now(UTC),
            ),
            MLModelResponse(
                model_name="isolation_forest",
                model_version="v1.0.0",
                model_type="unsupervised_isolation_forest",
                status="AVAILABLE" if iforest_path.exists() else "UNAVAILABLE",
                last_trained_at=None,
                artifact_path=str(iforest_path) if iforest_path.exists() else None,
                feature_schema_version="v1",
                evaluation_summary={},
                created_at=datetime.now(UTC),
            ),
            MLModelResponse(
                model_name="incident_risk_predictor",
                model_version="v1.0.0",
                model_type="supervised_random_forest",
                status="AVAILABLE" if incident_path.exists() else "UNAVAILABLE",
                last_trained_at=None,
                artifact_path=str(incident_path) if incident_path.exists() else None,
                feature_schema_version="v1",
                evaluation_summary={},
                created_at=datetime.now(UTC),
            ),
        ]
        return MLModelListResponse(models=static_models, total=len(static_models))

    def get_model(self, db: Session, model_name: str) -> MLModelResponse:
        """Fetch metadata for a single registered model."""
        row = db.scalars(select(MLModelModel).where(MLModelModel.model_name == model_name)).first()
        if row is not None:
            return MLModelResponse(
                model_name=row.model_name,
                model_version=row.version,
                model_type=row.model_type,
                status=row.status,  # type: ignore[arg-type]
                last_trained_at=row.trained_at,
                artifact_path=row.artifact_path,
                feature_schema_version="v1",
                evaluation_summary=row.metrics_json or {},
                created_at=row.created_at,
            )

        # Check default registry fallback
        all_models = self.list_models(db)
        for m in all_models.models:
            if m.model_name == model_name:
                return m

        raise ResourceNotFoundError(
            f"Model '{model_name}' is not registered.",
            details={"requested_model": model_name},
        )

    def predict_incident_risk(
        self,
        db: Session,
        req: IncidentRiskPredictRequest,
    ) -> IncidentRiskPredictResponse:
        """Predict incident risk within future horizon."""
        cutoff = req.prediction_timestamp or datetime.now(UTC)
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=UTC)
        else:
            cutoff = cutoff.astimezone(UTC)

        predictor = self._get_incident_predictor()

        if req.custom_features is not None:
            features = {k: float(v) for k, v in req.custom_features.items()}
            for sf in STANDARD_FEATURE_NAMES:
                features.setdefault(sf, 0.0)
        else:
            features = extract_features_from_db(
                session=db,
                service_name=req.service_name,
                window_end=cutoff,
                window_seconds=60.0,
            )

        pred = predictor.predict(
            features=features,
            service_name=req.service_name,
            timestamp=cutoff,
        )

        return IncidentRiskPredictResponse(
            model_name=pred.model_name,
            model_version=pred.model_version,
            service_name=pred.service_name,
            prediction_timestamp=pred.prediction_timestamp,
            horizon_seconds=req.horizon_seconds,
            risk_probability=pred.risk_probability,
            predicted_incident=pred.predicted_incident,
            risk_level=pred.risk_level,  # type: ignore[arg-type]
            contributing_factors=pred.contributing_factors,
            explanation=pred.explanation,
            features=pred.features,
            status=predictor.status,
        )


# Global service instance
ml_service = MLService()
