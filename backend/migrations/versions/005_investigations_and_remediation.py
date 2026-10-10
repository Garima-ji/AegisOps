"""Agentic investigations, audit trail events, and safe remediation proposals tables.

Revision ID: 005_investigations
Revises: 004_topology_and_root_cause
Create Date: 2026-10-10 11:30:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "005_investigations"
down_revision: str | None = "004_topology_and_root_cause"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Investigations Table
    op.create_table(
        "investigations",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("incident_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="IN_PROGRESS"),
        sa.Column("time_window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("time_window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("leading_candidate", sa.String(length=64), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("is_abstained", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("abstention_reason", sa.Text(), nullable=True),
        sa.Column("report_payload", sa.JSON(), nullable=True),
        sa.Column("tool_call_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_investigations_incident_id", "investigations", ["incident_id"], unique=False)
    op.create_index("ix_investigations_status_created", "investigations", ["status", "created_at"], unique=False)

    # 2. Investigation Events (Audit Trail)
    op.create_table(
        "investigation_events",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("investigation_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["investigation_id"], ["investigations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_inv_events_inv_time", "investigation_events", ["investigation_id", "timestamp"], unique=False)
    op.create_index("ix_inv_events_event_type", "investigation_events", ["event_type"], unique=False)

    # 3. Remediation Proposals Table
    op.create_table(
        "remediation_proposals",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("investigation_id", sa.String(length=64), nullable=False),
        sa.Column("action_type", sa.String(length=64), nullable=False),
        sa.Column("target_service", sa.String(length=64), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=True),
        sa.Column("policy_decision", sa.String(length=32), nullable=False, server_default="DENY"),
        sa.Column("approval_status", sa.String(length=32), nullable=False, server_default="NOT_REQUIRED"),
        sa.Column("simulation_status", sa.String(length=32), nullable=False, server_default="NOT_EXECUTED"),
        sa.Column("simulation_result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["investigation_id"], ["investigations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_rem_proposals_inv_id", "remediation_proposals", ["investigation_id"], unique=False)
    op.create_index("ix_rem_proposals_target", "remediation_proposals", ["target_service"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_rem_proposals_target", table_name="remediation_proposals")
    op.drop_index("ix_rem_proposals_inv_id", table_name="remediation_proposals")
    op.drop_table("remediation_proposals")

    op.drop_index("ix_inv_events_event_type", table_name="investigation_events")
    op.drop_index("ix_inv_events_inv_time", table_name="investigation_events")
    op.drop_table("investigation_events")

    op.drop_index("ix_investigations_status_created", table_name="investigations")
    op.drop_index("ix_investigations_incident_id", table_name="investigations")
    op.drop_table("investigations")
