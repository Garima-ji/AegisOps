"""Telemetry signals, service registry, and experiment tables.

Revision ID: 002_telemetry_and_experiments
Revises: 001_initial_schema
Create Date: 2026-10-10 01:30:00.000000
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "002_telemetry_and_experiments"
down_revision: Union[str, None] = "001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Services Registry Table
    op.create_table(
        "services",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("environment", sa.String(length=32), nullable=False, server_default="production"),
        sa.Column("version", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="healthy"),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "environment", name="uq_service_name_env"),
    )
    op.create_index("ix_services_name", "services", ["name"], unique=False)
    op.create_index("ix_services_last_seen", "services", ["last_seen"], unique=False)

    # 2. Metric Observations Table
    op.create_table(
        "metric_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("service_name", sa.String(length=64), nullable=False),
        sa.Column("environment", sa.String(length=32), nullable=False, server_default="production"),
        sa.Column("metric_name", sa.String(length=128), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False, server_default="count"),
        sa.Column("attributes", sa.JSON(), nullable=True),
        sa.Column("trace_id", sa.String(length=32), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_metrics_timestamp", "metric_records", ["timestamp"], unique=False)
    op.create_index("ix_metrics_service_name", "metric_records", ["service_name"], unique=False)
    op.create_index("ix_metrics_metric_name", "metric_records", ["metric_name"], unique=False)
    op.create_index("ix_metrics_trace_id", "metric_records", ["trace_id"], unique=False)
    op.create_index(
        "ix_metrics_svc_name_time",
        "metric_records",
        ["service_name", "metric_name", "timestamp"],
        unique=False,
    )
    op.create_index(
        "ix_metrics_env_time",
        "metric_records",
        ["environment", "timestamp"],
        unique=False,
    )

    # 3. Structured Logs Table
    op.create_table(
        "log_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("service_name", sa.String(length=64), nullable=False),
        sa.Column("environment", sa.String(length=32), nullable=False, server_default="production"),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("attributes", sa.JSON(), nullable=True),
        sa.Column("trace_id", sa.String(length=32), nullable=True),
        sa.Column("span_id", sa.String(length=16), nullable=True),
        sa.Column("exception_type", sa.String(length=128), nullable=True),
        sa.Column("error_class", sa.String(length=64), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_logs_timestamp", "log_records", ["timestamp"], unique=False)
    op.create_index("ix_logs_service_name", "log_records", ["service_name"], unique=False)
    op.create_index("ix_logs_severity", "log_records", ["severity"], unique=False)
    op.create_index("ix_logs_trace_id", "log_records", ["trace_id"], unique=False)
    op.create_index(
        "ix_logs_svc_sev_time",
        "log_records",
        ["service_name", "severity", "timestamp"],
        unique=False,
    )
    op.create_index(
        "ix_logs_trace_time",
        "log_records",
        ["trace_id", "timestamp"],
        unique=False,
    )

    # 4. Trace Spans Table
    op.create_table(
        "trace_spans",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("trace_id", sa.String(length=32), nullable=False),
        sa.Column("span_id", sa.String(length=16), nullable=False),
        sa.Column("parent_span_id", sa.String(length=16), nullable=True),
        sa.Column("service_name", sa.String(length=64), nullable=False),
        sa.Column("operation_name", sa.String(length=128), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=False),
        sa.Column("status_code", sa.String(length=16), nullable=False, server_default="UNSET"),
        sa.Column("status_message", sa.String(length=512), nullable=True),
        sa.Column("attributes", sa.JSON(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trace_id", "span_id", name="uq_trace_id_span_id"),
    )
    op.create_index("ix_spans_trace_id", "trace_spans", ["trace_id"], unique=False)
    op.create_index("ix_spans_span_id", "trace_spans", ["span_id"], unique=False)
    op.create_index("ix_spans_service_name", "trace_spans", ["service_name"], unique=False)
    op.create_index("ix_spans_start_time", "trace_spans", ["start_time"], unique=False)
    op.create_index(
        "ix_spans_trace_start",
        "trace_spans",
        ["trace_id", "start_time"],
        unique=False,
    )
    op.create_index(
        "ix_spans_svc_start",
        "trace_spans",
        ["service_name", "start_time"],
        unique=False,
    )

    # 5. Experiments Table
    op.create_table(
        "experiments",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("scenario_name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("target_service", sa.String(length=64), nullable=False),
        sa.Column("fault_type", sa.String(length=64), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=True),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="PENDING"),
        sa.Column("initiating_service", sa.String(length=64), nullable=False),
        sa.Column("symptom_services", sa.JSON(), nullable=True),
        sa.Column("cleanup_result", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_experiments_start_time", "experiments", ["start_time"], unique=False)
    op.create_index("ix_experiments_target_service", "experiments", ["target_service"], unique=False)
    op.create_index("ix_experiments_status", "experiments", ["status"], unique=False)

    # 6. Fault Labels Table
    op.create_table(
        "fault_labels",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("experiment_id", sa.String(length=64), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("service_name", sa.String(length=64), nullable=False),
        sa.Column("is_fault", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("fault_type", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False, server_default="HIGH"),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_labels_experiment_id", "fault_labels", ["experiment_id"], unique=False)
    op.create_index("ix_labels_timestamp", "fault_labels", ["timestamp"], unique=False)
    op.create_index("ix_labels_service_name", "fault_labels", ["service_name"], unique=False)
    op.create_index(
        "ix_labels_exp_svc_time",
        "fault_labels",
        ["experiment_id", "service_name", "timestamp"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("fault_labels")
    op.drop_table("experiments")
    op.drop_table("trace_spans")
    op.drop_table("log_records")
    op.drop_table("metric_records")
    op.drop_table("services")
