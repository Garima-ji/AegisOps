"""Service dependency topology snapshots, edges, and incident root-cause analysis tables.

Revision ID: 004_topology_and_root_cause
Revises: 003_ml_detection_and_models
Create Date: 2026-10-10 10:30:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "004_topology_and_root_cause"
down_revision: str | None = "003_ml_detection_and_models"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Topology Snapshots Table
    op.create_table(
        "topology_snapshots",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False, server_default="v1.0.0"),
        sa.Column("data_source", sa.String(length=64), nullable=False, server_default="trace_spans"),
        sa.Column("time_window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("time_window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("service_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("edge_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("services_json", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_topology_snapshots_created_at", "topology_snapshots", ["created_at"], unique=False)
    op.create_index("ix_topology_snapshots_version", "topology_snapshots", ["version"], unique=False)

    # 2. Topology Edges Table
    op.create_table(
        "topology_edges",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), autoincrement=True, nullable=False),
        sa.Column("topology_id", sa.String(length=64), nullable=False),
        sa.Column("caller_service", sa.String(length=64), nullable=False),
        sa.Column("downstream_service", sa.String(length=64), nullable=False),
        sa.Column("call_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_rate", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("latency_p50_ms", sa.Float(), nullable=True),
        sa.Column("latency_p95_ms", sa.Float(), nullable=True),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_observed", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("is_manual", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["topology_id"], ["topology_snapshots.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_edges_topo_caller_downstream",
        "topology_edges",
        ["topology_id", "caller_service", "downstream_service"],
        unique=False,
    )
    op.create_index("ix_topology_edges_caller", "topology_edges", ["caller_service"], unique=False)
    op.create_index("ix_topology_edges_downstream", "topology_edges", ["downstream_service"], unique=False)
    op.create_index("ix_topology_edges_topo_id", "topology_edges", ["topology_id"], unique=False)

    # 3. Incident Analyses Table
    op.create_table(
        "incident_analyses",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("incident_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="COMPLETED"),
        sa.Column("time_window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("time_window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("topology_version", sa.String(length=64), nullable=True),
        sa.Column("affected_services", sa.JSON(), nullable=False),
        sa.Column("top_candidate_service", sa.String(length=64), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("summary_explanation", sa.Text(), nullable=True),
        sa.Column("candidates_json", sa.JSON(), nullable=False),
        sa.Column("diagnostics_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_incident_analyses_incident_id", "incident_analyses", ["incident_id"], unique=False)
    op.create_index("ix_incident_analyses_status", "incident_analyses", ["status"], unique=False)
    op.create_index("ix_incident_analyses_created_at", "incident_analyses", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_incident_analyses_created_at", table_name="incident_analyses")
    op.drop_index("ix_incident_analyses_status", table_name="incident_analyses")
    op.drop_index("ix_incident_analyses_incident_id", table_name="incident_analyses")
    op.drop_table("incident_analyses")

    op.drop_index("ix_topology_edges_topo_id", table_name="topology_edges")
    op.drop_index("ix_topology_edges_downstream", table_name="topology_edges")
    op.drop_index("ix_topology_edges_caller", table_name="topology_edges")
    op.drop_index("ix_edges_topo_caller_downstream", table_name="topology_edges")
    op.drop_table("topology_edges")

    op.drop_index("ix_topology_snapshots_version", table_name="topology_snapshots")
    op.drop_index("ix_topology_snapshots_created_at", table_name="topology_snapshots")
    op.drop_table("topology_snapshots")
