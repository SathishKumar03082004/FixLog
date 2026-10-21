import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from . import config
from .auth import AuthRedirect, get_current_user, hash_password
from .config import ROLE_ADMIN
from .database import Base, SessionLocal, engine, ensure_database_exists, ensure_schema
from .models import User
from .routers import admin_routes, auth_routes, issue_routes
from .templating import render

log = logging.getLogger("fixlog")
BASE_DIR = Path(__file__).resolve().parent


def seed_admin():
    db = SessionLocal()
    try:
        if db.query(User).filter(User.role == ROLE_ADMIN).first():
            return
        db.add(User(
            username=config.ADMIN_USERNAME.lower(), email=config.ADMIN_EMAIL.lower(),
            full_name="Administrator", password_hash=hash_password(config.ADMIN_PASSWORD),
            role=ROLE_ADMIN, is_active=True,
        ))
        db.commit()
        log.warning("Created first admin '%s'. Change its password after signing in.", config.ADMIN_USERNAME)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_database_exists()
    Base.metadata.create_all(bind=engine)  # creates `users`, and `issues` if missing
    ensure_schema()                        # adds issues.reported_by_id to old databases
    seed_admin()
    yield


app = FastAPI(title="FixLog - Problem & Resolution Tracker", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=config.SECRET_KEY,
    max_age=config.SESSION_MAX_AGE,
    same_site="lax",
    https_only=config.COOKIE_SECURE,
    session_cookie="fixlog_session",
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

app.include_router(auth_routes.router)
app.include_router(admin_routes.router)
app.include_router(issue_routes.router)


@app.exception_handler(AuthRedirect)
async def auth_redirect_handler(request: Request, exc: AuthRedirect):
    return RedirectResponse(exc.url, status_code=303)


@app.exception_handler(StarletteHTTPException)
async def html_error_handler(request: Request, exc: StarletteHTTPException):
    if request.url.path.startswith("/api/"):
        return await http_exception_handler(request, exc)
    from .database import SessionLocal as _S
    db = _S()
    try:
        user = get_current_user(request, db)
        return render(request, "error.html", {"code": exc.status_code, "message": exc.detail},
                      status_code=exc.status_code, user=user)
    finally:
        db.close()


@app.get("/")
def home(request: Request, user=Depends(get_current_user)):
    if user:
        return RedirectResponse("/dashboard", status_code=303)
    return render(request, "auth/landing.html", {"portals": auth_routes.PORTALS})
