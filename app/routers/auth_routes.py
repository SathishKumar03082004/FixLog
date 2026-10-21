from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import (
    EMAIL_RE,
    USERNAME_RE,
    authenticate,
    clear_failures,
    get_current_user,
    hash_password,
    is_locked,
    login_session,
    password_problem,
    record_failure,
    require_user,
    safe_next,
    verify_csrf,
    verify_password,
)
from ..config import ROLE_ADMIN, ROLE_DEVELOPER, ROLE_USER
from ..database import get_db
from ..models import User
from ..templating import flash, render

router = APIRouter()

PORTALS = {
    ROLE_USER: {"path": "/login", "title": "User sign in", "subtitle": "Report problems and track their fixes.", "can_register": True},
    ROLE_DEVELOPER: {"path": "/developer/login", "title": "Developer sign in", "subtitle": "Triage issues and record root causes and solutions.", "can_register": True},
    ROLE_ADMIN: {"path": "/admin/login", "title": "Admin sign in", "subtitle": "Manage users, roles and all issue records.", "can_register": False},
}


def _login_page(request: Request, portal: str, error: Optional[str] = None, identifier: str = "", status_code: int = 200):
    return render(
        request,
        "auth/login.html",
        {"portal": portal, "cfg": PORTALS[portal], "portals": PORTALS, "error": error,
         "identifier": identifier, "next": request.query_params.get("next", "")},
        status_code=status_code,
    )


def _do_login(request: Request, db: Session, portal: str, identifier: str, password: str, next_url: str):
    ip = request.client.host if request.client else "unknown"
    key = f"{portal}:{ip}:{identifier.strip().lower()}"
    if is_locked(key):
        return _login_page(request, portal, "Too many failed attempts. Try again in a few minutes.", identifier, 429)

    user = authenticate(db, identifier, password)
    # Wrong password, unknown user, or wrong portal all give the same message.
    if user is None or user.role != portal:
        record_failure(key)
        return _login_page(request, portal, "Invalid username or password.", identifier, 401)
    if not user.is_active:
        msg = ("Your developer account is waiting for admin approval."
               if user.role == ROLE_DEVELOPER and user.last_login_at is None
               else "This account is disabled. Please contact an administrator.")
        return _login_page(request, portal, msg, identifier, 403)

    clear_failures(key)
    user.last_login_at = datetime.utcnow()
    db.commit()
    login_session(request, user)
    return RedirectResponse(url=safe_next(next_url), status_code=303)


# ---------------------------------------------------------------- login routes
@router.get("/login")
def user_login_page(request: Request, user=Depends(get_current_user)):
    if user:
        return RedirectResponse("/dashboard", status_code=303)
    return _login_page(request, ROLE_USER)


@router.post("/login", dependencies=[Depends(verify_csrf)])
def user_login(request: Request, identifier: str = Form(...), password: str = Form(...),
               next: str = Form(""), db: Session = Depends(get_db)):
    return _do_login(request, db, ROLE_USER, identifier, password, next)


@router.get("/developer/login")
def developer_login_page(request: Request, user=Depends(get_current_user)):
    if user:
        return RedirectResponse("/dashboard", status_code=303)
    return _login_page(request, ROLE_DEVELOPER)


@router.post("/developer/login", dependencies=[Depends(verify_csrf)])
def developer_login(request: Request, identifier: str = Form(...), password: str = Form(...),
                    next: str = Form(""), db: Session = Depends(get_db)):
    return _do_login(request, db, ROLE_DEVELOPER, identifier, password, next)


@router.get("/admin/login")
def admin_login_page(request: Request, user=Depends(get_current_user)):
    if user:
        return RedirectResponse("/dashboard", status_code=303)
    return _login_page(request, ROLE_ADMIN)


@router.post("/admin/login", dependencies=[Depends(verify_csrf)])
def admin_login(request: Request, identifier: str = Form(...), password: str = Form(...),
                next: str = Form(""), db: Session = Depends(get_db)):
    return _do_login(request, db, ROLE_ADMIN, identifier, password, next)


@router.post("/logout", dependencies=[Depends(verify_csrf)])
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


# ------------------------------------------------------------------- register
def _register_page(request: Request, form: dict, error: Optional[str] = None, status_code: int = 200):
    return render(request, "auth/register.html", {"form": form, "error": error}, status_code=status_code)


@router.get("/register")
def register_page(request: Request, role: str = ROLE_USER, user=Depends(get_current_user)):
    if user:
        return RedirectResponse("/dashboard", status_code=303)
    role = role if role in (ROLE_USER, ROLE_DEVELOPER) else ROLE_USER
    return _register_page(request, {"role": role})


@router.post("/register", dependencies=[Depends(verify_csrf)])
def register(
    request: Request,
    full_name: str = Form(""),
    username: str = Form(...),
    email: str = Form(...),
    role: str = Form(ROLE_USER),
    password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db),
):
    username = username.strip().lower()
    email = email.strip().lower()
    full_name = full_name.strip()[:100]
    role = ROLE_DEVELOPER if role == ROLE_DEVELOPER else ROLE_USER  # admin can never self-register
    form = {"full_name": full_name, "username": username, "email": email, "role": role}

    error = None
    if not USERNAME_RE.match(username):
        error = "Username must be 3-50 characters: letters, numbers, dot, dash or underscore."
    elif not EMAIL_RE.match(email) or len(email) > 120:
        error = "Please enter a valid email address."
    elif password != confirm_password:
        error = "Passwords do not match."
    else:
        error = password_problem(password)
    if not error:
        taken = db.query(User).filter(
            (func.lower(User.username) == username) | (func.lower(User.email) == email)
        ).first()
        if taken:
            error = "That username or email is already registered."
    if error:
        return _register_page(request, form, error, 400)

    pending = role == ROLE_DEVELOPER
    db.add(User(
        username=username, email=email, full_name=full_name,
        password_hash=hash_password(password), role=role, is_active=not pending,
    ))
    db.commit()
    if pending:
        flash(request, "Developer account created. An administrator must approve it before you can sign in.", "info")
        return RedirectResponse("/developer/login", status_code=303)
    flash(request, "Account created. You can sign in now.")
    return RedirectResponse("/login", status_code=303)


# ------------------------------------------------------------ change password
@router.get("/account/password")
def password_page(request: Request, user: User = Depends(require_user)):
    return render(request, "account/password.html", {"error": None}, user=user)


@router.post("/account/password", dependencies=[Depends(verify_csrf)])
def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    error = None
    if not verify_password(current_password, user.password_hash):
        error = "Current password is incorrect."
    elif new_password != confirm_password:
        error = "New passwords do not match."
    else:
        error = password_problem(new_password)
    if error:
        return render(request, "account/password.html", {"error": error}, status_code=400, user=user)
    user.password_hash = hash_password(new_password)
    db.commit()
    flash(request, "Password updated.")
    return RedirectResponse("/dashboard", status_code=303)
