"""ML model registry and anomaly detection tables.

Revision ID: 003_ml_detection_and_models
Revises: 002_telemetry_and_experiments
Create Date: 2026-10-10 08:30:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "003_ml_detection_and_models"
down_revision: str | None = "002_telemetry_and_experiments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. ML Model Registry Table
    op.create_table(
        "ml_models",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("model_name", sa.String(length=64), nullable=False),
        sa.Column("model_type", sa.String(length=32), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="UNTRAINED"),
        sa.Column("artifact_path", sa.String(length=256), nullable=True),
        sa.Column("feature_names", sa.JSON(), nullable=True),
        sa.Column("hyperparameters", sa.JSON(), nullable=True),
        sa.Column("metrics_json", sa.JSON(), nullable=True),
        sa.Column("trained_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("model_name", name="uq_ml_model_name"),
    )
    op.create_index("ix_ml_models_name", "ml_models", ["model_name"], unique=True)

    # 2. Anomaly Detections Table
    op.create_table(
        "anomaly_detections",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), autoincrement=True, nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("service_name", sa.String(length=64), nullable=False),
        sa.Column("detector_name", sa.String(length=64), nullable=False),
        sa.Column("model_version", sa.String(length=32), nullable=False),
        sa.Column("is_anomaly", sa.Boolean(), nullable=False),
        sa.Column("anomaly_score", sa.Float(), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("triggering_features", sa.JSON(), nullable=True),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_anomalies_service_time",
        "anomaly_detections",
        ["service_name", "timestamp"],
        unique=False,
    )
    op.create_index(
        "ix_anomalies_detector_time",
        "anomaly_detections",
        ["detector_name", "timestamp"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_anomalies_detector_time", table_name="anomaly_detections")
    op.drop_index("ix_anomalies_service_time", table_name="anomaly_detections")
    op.drop_table("anomaly_detections")

    op.drop_index("ix_ml_models_name", table_name="ml_models")
    op.drop_table("ml_models")
