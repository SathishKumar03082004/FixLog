import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.engine import URL

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def build_database_url():
    """Return the SQLAlchemy URL.

    DATABASE_URL wins if set. Otherwise it is built from DB_* values, which
    safely handles passwords containing characters such as @ : / #.
    Default result: mysql+pymysql://root:***@localhost:3306/unity?charset=utf8mb4
    """
    explicit = os.getenv("DATABASE_URL", "").strip()
    if explicit:
        return explicit
    return URL.create(
        drivername="mysql+pymysql",
        username=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", "sathish123"),
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "3306")),
        database=os.getenv("DB_NAME", "unity"),
        query={"charset": "utf8mb4"},
    )


DATABASE_URL = build_database_url()
SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-insecure-key-change-me")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"
SESSION_MAX_AGE = 60 * 60 * 8  # 8 hours

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@fixlog.local")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "Admin@12345")

ROLE_USER = "user"
ROLE_DEVELOPER = "developer"
ROLE_ADMIN = "admin"
ROLES = [ROLE_USER, ROLE_DEVELOPER, ROLE_ADMIN]
