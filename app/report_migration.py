"""Lightweight migration for the Smart Bug Report columns.

Base.metadata.create_all() never alters an existing table, so databases that
already have an `issues` table need these columns added once:

    issues.steps_to_reproduce  TEXT
    issues.expected_result     TEXT
    issues.actual_result       TEXT
    issues.browser             VARCHAR(80)
    issues.os_name             VARCHAR(80)
    issues.app_version         VARCHAR(40)
    issues.environment         VARCHAR(30)
    issues.severity            VARCHAR(20)  (existing rows become 'Medium')
    issues.affected_users      VARCHAR(30)

The new `issue_attachments` table is brand new, so create_all() builds it.

Like board_migration.py, this reuses the existing engine and does not touch
config.py or database.py, so the database connection string is unchanged.
It is idempotent - safe to run on every startup.
"""
from sqlalchemy import inspect, text

from .database import engine

# column name -> column definition (valid on both MySQL and SQLite)
_COLUMNS = {
    "steps_to_reproduce": "TEXT NULL",
    "expected_result": "TEXT NULL",
    "actual_result": "TEXT NULL",
    "browser": "VARCHAR(80) NOT NULL DEFAULT ''",
    "os_name": "VARCHAR(80) NOT NULL DEFAULT ''",
    "app_version": "VARCHAR(40) NOT NULL DEFAULT ''",
    "environment": "VARCHAR(30) NOT NULL DEFAULT ''",
    "severity": "VARCHAR(20) NOT NULL DEFAULT 'Medium'",
    "affected_users": "VARCHAR(30) NOT NULL DEFAULT ''",
}


def ensure_report_schema():
    inspector = inspect(engine)
    if "issues" not in inspector.get_table_names():
        return  # fresh database: create_all() already built the full table

    existing = {c["name"] for c in inspector.get_columns("issues")}
    missing = {name: ddl for name, ddl in _COLUMNS.items() if name not in existing}
    if not missing:
        return

    with engine.begin() as conn:
        for name, ddl in missing.items():
            conn.execute(text(f"ALTER TABLE issues ADD COLUMN {name} {ddl}"))
