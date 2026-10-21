from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import DATABASE_URL

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=3600)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_database_exists():
    """Create the MySQL database (e.g. `unity`) if it does not exist yet."""
    url = make_url(DATABASE_URL)
    if not url.drivername.startswith("mysql"):
        return
    # URL.set(database=None) does NOT clear the database, so rebuild the URL without it.
    server_url = URL.create(
        drivername=url.drivername, username=url.username, password=url.password,
        host=url.host, port=url.port, database=None, query=dict(url.query),
    )
    server_engine = create_engine(server_url)
    try:
        with server_engine.connect() as conn:
            conn.execute(
                text(
                    f"CREATE DATABASE IF NOT EXISTS `{url.database}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            )
            conn.commit()
    finally:
        server_engine.dispose()


def ensure_schema():
    """Lightweight migration: add issues.reported_by_id to pre-auth databases.

    Base.metadata.create_all() never alters existing tables, so an `issues`
    table created by the old version of FixLog needs this column added.
    """
    inspector = inspect(engine)
    if "issues" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("issues")}
    if "reported_by_id" in columns:
        return
    with engine.begin() as conn:
        if engine.dialect.name == "mysql":
            conn.execute(text("ALTER TABLE issues ADD COLUMN reported_by_id INT NULL"))
            conn.execute(
                text(
                    "ALTER TABLE issues ADD CONSTRAINT fk_issues_reported_by "
                    "FOREIGN KEY (reported_by_id) REFERENCES users(id) ON DELETE SET NULL"
                )
            )
        else:
            conn.execute(
                text("ALTER TABLE issues ADD COLUMN reported_by_id INTEGER NULL REFERENCES users(id)")
            )
