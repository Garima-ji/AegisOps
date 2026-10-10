"""Initial schema baseline with system audit logs.

Revision ID: 001_initial_schema
Revises: None
Create Date: 2026-10-09 18:30:00.000000
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "system_audit_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_system_audit_logs_event_type",
        "system_audit_logs",
        ["event_type"],
        unique=False,
    )
    op.create_index(
        "ix_system_audit_logs_created_at",
        "system_audit_logs",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_system_audit_logs_created_at", table_name="system_audit_logs")
    op.drop_index("ix_system_audit_logs_event_type", table_name="system_audit_logs")
    op.drop_table("system_audit_logs")
