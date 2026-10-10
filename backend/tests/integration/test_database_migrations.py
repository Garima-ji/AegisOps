"""Integration tests verifying Alembic database migration workflow."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def test_alembic_migration_upgrade_and_downgrade(tmp_path: Path) -> None:
    """Verify Alembic migration upgrade to head and downgrade to base."""
    test_db_file = tmp_path / "migration_test.db"
    db_url = f"sqlite:///{test_db_file}"

    # Prepare Alembic Config
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)

    # 1. Run upgrade head
    command.upgrade(alembic_cfg, "head")

    # 2. Inspect created tables
    engine = create_engine(db_url)
    with engine.connect() as conn:
        inspector = inspect(conn)
        tables = inspector.get_table_names()
        assert "system_audit_logs" in tables
        assert "services" in tables
        assert "metric_records" in tables
        assert "ml_models" in tables
        assert "anomaly_detections" in tables
        assert "topology_snapshots" in tables
        assert "topology_edges" in tables
        assert "incident_analyses" in tables
        assert "alembic_version" in tables

        # Insert test audit log row
        conn.execute(
            text(
                "INSERT INTO system_audit_logs (event_type, action, actor, success, created_at) "
                "VALUES ('SYSTEM_INIT', 'MIGRATION_APPLIED', 'test_runner', 1, CURRENT_TIMESTAMP)"
            )
        )
        conn.commit()

        # Query row
        result = conn.execute(
            text(
                "SELECT event_type, action FROM system_audit_logs WHERE event_type = 'SYSTEM_INIT'"
            )
        ).fetchone()
        assert result is not None
        assert result[0] == "SYSTEM_INIT"
        assert result[1] == "MIGRATION_APPLIED"

    # 3. Test downgrade to base
    command.downgrade(alembic_cfg, "base")

    # 4. Verify system_audit_logs table has been dropped
    with engine.connect() as conn:
        inspector = inspect(conn)
        tables_after_downgrade = inspector.get_table_names()
        assert "system_audit_logs" not in tables_after_downgrade

    engine.dispose()
