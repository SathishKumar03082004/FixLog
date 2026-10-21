from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import (
    EMAIL_RE,
    USERNAME_RE,
    hash_password,
    password_problem,
    require_admin,
    verify_csrf,
)
from ..config import ROLE_ADMIN, ROLES
from ..database import get_db
from ..models import Issue, User
from ..templating import flash, render

router = APIRouter(prefix="/admin/users", dependencies=[Depends(require_admin)])


def _active_admin_count(db: Session) -> int:
    return db.query(User).filter(User.role == ROLE_ADMIN, User.is_active.is_(True)).count()


@router.get("")
def users_page(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    users = db.query(User).order_by(User.is_active.asc(), User.created_at.desc()).all()
    pending = [u for u in users if not u.is_active and u.last_login_at is None and u.role != "user"]
    return render(request, "admin/users.html",
                  {"users": users, "roles": ROLES, "pending_count": len(pending), "form": {}, "error": None},
                  user=admin)


@router.post("/create", dependencies=[Depends(verify_csrf)])
def create_user(
    request: Request,
    full_name: str = Form(""),
    username: str = Form(...),
    email: str = Form(...),
    role: str = Form(...),
    password: str = Form(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    username, email, full_name = username.strip().lower(), email.strip().lower(), full_name.strip()[:100]
    form = {"full_name": full_name, "username": username, "email": email, "role": role}
    error = None
    if role not in ROLES:
        error = "Invalid role."
    elif not USERNAME_RE.match(username):
        error = "Username must be 3-50 characters: letters, numbers, dot, dash or underscore."
    elif not EMAIL_RE.match(email) or len(email) > 120:
        error = "Please enter a valid email address."
    else:
        error = password_problem(password)
    if not error and db.query(User).filter(
        (func.lower(User.username) == username) | (func.lower(User.email) == email)
    ).first():
        error = "That username or email is already registered."
    if error:
        users = db.query(User).order_by(User.created_at.desc()).all()
        return render(request, "admin/users.html",
                      {"users": users, "roles": ROLES, "pending_count": 0, "form": form, "error": error},
                      status_code=400, user=admin)
    db.add(User(username=username, email=email, full_name=full_name,
                password_hash=hash_password(password), role=role, is_active=True))
    db.commit()
    flash(request, f"User '{username}' created as {role}.")
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/{user_id}/update", dependencies=[Depends(verify_csrf)])
def update_user(
    user_id: int,
    request: Request,
    role: str = Form(...),
    is_active: str = Form(""),
    new_password: str = Form(""),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(404, "User not found")
    active = is_active == "on"
    if role not in ROLES:
        raise HTTPException(400, "Invalid role")

    if target.id == admin.id and (role != ROLE_ADMIN or not active):
        flash(request, "You cannot demote or disable your own account.", "danger")
        return RedirectResponse("/admin/users", status_code=303)
    if target.role == ROLE_ADMIN and target.is_active and (role != ROLE_ADMIN or not active) \
            and _active_admin_count(db) <= 1:
        flash(request, "At least one active admin must remain.", "danger")
        return RedirectResponse("/admin/users", status_code=303)
    if new_password:
        problem = password_problem(new_password)
        if problem:
            flash(request, problem, "danger")
            return RedirectResponse("/admin/users", status_code=303)
        target.password_hash = hash_password(new_password)

    target.role = role
    target.is_active = active
    db.commit()
    flash(request, f"Updated '{target.username}'.")
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/{user_id}/delete", dependencies=[Depends(verify_csrf)])
def delete_user(user_id: int, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(404, "User not found")
    if target.id == admin.id:
        flash(request, "You cannot delete your own account.", "danger")
        return RedirectResponse("/admin/users", status_code=303)
    if target.role == ROLE_ADMIN and target.is_active and _active_admin_count(db) <= 1:
        flash(request, "At least one active admin must remain.", "danger")
        return RedirectResponse("/admin/users", status_code=303)
    db.query(Issue).filter(Issue.reported_by_id == target.id).update({Issue.reported_by_id: None})
    db.delete(target)
    db.commit()
    flash(request, f"Deleted '{target.username}'. Their issues were kept.")
    return RedirectResponse("/admin/users", status_code=303)
