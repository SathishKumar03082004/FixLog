"""Lightweight migration for the Kanban board columns.

Base.metadata.create_all() never alters an existing table, so databases that
already have an `issues` table need these columns added once:

    issues.assignee_id     INT NULL  -> users.id  (who is working on it)
    issues.is_blocked      BOOLEAN   (card is stuck)
    issues.blocked_reason  VARCHAR(255)

This lives in its own module on purpose: it reuses the existing engine and
does not touch config.py or database.py, so the database connection is
unchanged. It is idempotent - safe to run on every startup.
"""
from sqlalchemy import inspect, text

from .database import engine


def ensure_board_schema():
    inspector = inspect(engine)
    if "issues" not in inspector.get_table_names():
        return  # fresh database: create_all() already built the full table

    existing = {c["name"] for c in inspector.get_columns("issues")}
    is_mysql = engine.dialect.name == "mysql"

    with engine.begin() as conn:
        if "assignee_id" not in existing:
            if is_mysql:
                conn.execute(text("ALTER TABLE issues ADD COLUMN assignee_id INT NULL"))
                conn.execute(text(
                    "ALTER TABLE issues ADD CONSTRAINT fk_issues_assignee "
                    "FOREIGN KEY (assignee_id) REFERENCES users(id) ON DELETE SET NULL"
                ))
            else:
                conn.execute(text(
                    "ALTER TABLE issues ADD COLUMN assignee_id INTEGER NULL REFERENCES users(id)"
                ))
        if "is_blocked" not in existing:
            conn.execute(text("ALTER TABLE issues ADD COLUMN is_blocked BOOLEAN NOT NULL DEFAULT 0"))
        if "blocked_reason" not in existing:
            conn.execute(text("ALTER TABLE issues ADD COLUMN blocked_reason VARCHAR(255) NOT NULL DEFAULT ''"))
