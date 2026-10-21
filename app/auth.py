import hashlib
import hmac
import re
import secrets
import time
from collections import defaultdict
from typing import Optional
from urllib.parse import quote

from fastapi import Depends, HTTPException, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .config import ROLE_ADMIN, ROLE_DEVELOPER
from .database import get_db
from .models import User

# ---------------------------------------------------------------- passwords
_ITERATIONS = 600_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt_hex, digest_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


# A pre-computed hash so failed lookups cost the same time as real ones.
_DUMMY_HASH = hash_password("not-a-real-password")


def password_problem(password: str) -> Optional[str]:
    if len(password) < 8:
        return "Password must be at least 8 characters."
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return "Password must contain at least one letter and one number."
    return None


USERNAME_RE = re.compile(r"^[a-z0-9_.-]{3,50}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ------------------------------------------------------------ login throttle
_failures = defaultdict(list)
MAX_FAILURES = 5
LOCKOUT_SECONDS = 300


def is_locked(key: str) -> bool:
    now = time.time()
    _failures[key] = [t for t in _failures[key] if now - t < LOCKOUT_SECONDS]
    return len(_failures[key]) >= MAX_FAILURES


def record_failure(key: str):
    _failures[key].append(time.time())


def clear_failures(key: str):
    _failures.pop(key, None)


# --------------------------------------------------------------------- CSRF
def csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


async def verify_csrf(request: Request):
    form = await request.form()
    sent = str(form.get("csrf_token", ""))
    expected = request.session.get("csrf", "")
    if not expected or not hmac.compare_digest(sent, expected):
        raise HTTPException(status_code=403, detail="Security token missing or expired. Please go back and try again.")


# ------------------------------------------------------------ session / user
class AuthRedirect(Exception):
    def __init__(self, url: str):
        self.url = url


def safe_next(url: str, default: str = "/dashboard") -> str:
    if url and url.startswith("/") and not url.startswith("//") and "\\" not in url:
        return url
    return default


def authenticate(db: Session, identifier: str, password: str) -> Optional[User]:
    ident = identifier.strip().lower()
    user = (
        db.query(User)
        .filter(or_(func.lower(User.username) == ident, func.lower(User.email) == ident))
        .first()
    )
    if user is None:
        verify_password(password, _DUMMY_HASH)
        return None
    return user if verify_password(password, user.password_hash) else None


def login_session(request: Request, user: User):
    request.session.clear()
    request.session["uid"] = user.id
    request.session["csrf"] = secrets.token_urlsafe(32)


def get_current_user(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    uid = request.session.get("uid")
    if not uid:
        return None
    user = db.get(User, uid)
    if user is None or not user.is_active:
        request.session.clear()
        return None
    return user


def _login_url_for(request: Request) -> str:
    path = request.url.path
    base = "/admin/login" if path.startswith("/admin") else "/login"
    return f"{base}?next={quote(path)}"


def require_user(request: Request, user: Optional[User] = Depends(get_current_user)) -> User:
    if user is None:
        if request.url.path.startswith("/api/"):
            raise HTTPException(status_code=401, detail="Authentication required")
        raise AuthRedirect(_login_url_for(request))
    return user


def require_roles(*roles: str):
    def checker(user: User = Depends(require_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="You do not have permission to access this page.")
        return user

    return checker


require_staff = require_roles(ROLE_DEVELOPER, ROLE_ADMIN)
require_admin = require_roles(ROLE_ADMIN)


def is_staff(user: User) -> bool:
    return user.role in (ROLE_DEVELOPER, ROLE_ADMIN)
