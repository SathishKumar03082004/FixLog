from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from ..auth import is_staff, require_admin, require_user, verify_csrf
from ..config import ROLE_USER
from ..database import get_db
from ..models import Issue, User
from ..templating import flash, render

router = APIRouter()

STATUSES = ["Open", "In Progress", "Resolved"]
PRIORITIES = ["Low", "Medium", "High", "Critical"]
CATEGORIES = ["Python", "Web", "Database", "Networking", "Environment", "Other"]


def scoped(db: Session, user: User):
    """Plain users only ever see issues they reported; staff see everything."""
    query = db.query(Issue).options(joinedload(Issue.reporter))
    if user.role == ROLE_USER:
        query = query.filter(Issue.reported_by_id == user.id)
    return query


def get_issue_or_404(db: Session, user: User, issue_id: int) -> Issue:
    issue = scoped(db, user).filter(Issue.id == issue_id).first()
    if issue is None:
        raise HTTPException(404, "Issue not found")
    return issue


def can_edit(user: User, issue: Issue) -> bool:
    if is_staff(user):
        return True
    return issue.reported_by_id == user.id and issue.status != "Resolved"


def form_context(issue, user, page_title, error=None):
    return {"issue": issue, "statuses": STATUSES, "priorities": PRIORITIES,
            "categories": CATEGORIES, "page_title": page_title, "error": error}


def dashboard_counts(db: Session, user: User):
    base = scoped(db, user)
    return {
        "total": base.count(),
        "open_count": base.filter(Issue.status == "Open").count(),
        "progress_count": base.filter(Issue.status == "In Progress").count(),
        "resolved_count": base.filter(Issue.status == "Resolved").count(),
    }


@router.get("/dashboard")
def dashboard(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    recent = scoped(db, user).order_by(Issue.updated_at.desc()).limit(5).all()
    return render(request, "dashboard.html",
                  {**dashboard_counts(db, user), "recent_issues": recent}, user=user)


@router.get("/issues")
def list_issues(
    request: Request, q: str = "", status: str = "", priority: str = "", category: str = "",
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    query = scoped(db, user)
    if q.strip():
        term = f"%{q.strip()}%"
        query = query.filter(or_(
            Issue.title.ilike(term), Issue.description.ilike(term), Issue.error_message.ilike(term),
            Issue.root_cause.ilike(term), Issue.solution.ilike(term),
        ))
    if status in STATUSES:
        query = query.filter(Issue.status == status)
    if priority in PRIORITIES:
        query = query.filter(Issue.priority == priority)
    if category in CATEGORIES:
        query = query.filter(Issue.category == category)
    issues = query.order_by(Issue.updated_at.desc()).all()
    return render(request, "issues/list.html", {
        "issues": issues, "q": q, "selected_status": status, "selected_priority": priority,
        "selected_category": category, "statuses": STATUSES, "priorities": PRIORITIES,
        "categories": CATEGORIES}, user=user)


@router.get("/issues/new")
def new_issue_form(request: Request, user: User = Depends(require_user)):
    return render(request, "issues/form.html", form_context(None, user, "Create an issue"), user=user)


@router.post("/issues/new", dependencies=[Depends(verify_csrf)])
def create_issue(
    request: Request,
    title: str = Form(...), description: str = Form(...), error_message: str = Form(""),
    category: str = Form("Other"), priority: str = Form("Medium"), status: str = Form("Open"),
    root_cause: str = Form(""), solution: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    title, description = title.strip(), description.strip()
    if not title or not description:
        return render(request, "issues/form.html",
                      form_context(None, user, "Create an issue", "Title and description are required."),
                      status_code=400, user=user)
    if category not in CATEGORIES or priority not in PRIORITIES or status not in STATUSES:
        raise HTTPException(400, "Invalid category, priority, or status.")

    issue = Issue(title=title[:160], description=description, error_message=error_message.strip(),
                  category=category, priority=priority, reported_by_id=user.id)
    if is_staff(user):  # only developers/admins may set triage fields
        issue.status, issue.root_cause, issue.solution = status, root_cause.strip(), solution.strip()
    db.add(issue)
    db.commit()
    db.refresh(issue)
    flash(request, "Issue recorded.")
    return RedirectResponse(url=f"/issues/{issue.id}", status_code=303)


@router.get("/issues/{issue_id}")
def issue_detail(issue_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    issue = get_issue_or_404(db, user, issue_id)
    return render(request, "issues/detail.html",
                  {"issue": issue, "can_edit": can_edit(user, issue), "can_delete": user.role == "admin"},
                  user=user)


@router.get("/issues/{issue_id}/edit")
def edit_issue_form(issue_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    issue = get_issue_or_404(db, user, issue_id)
    if not can_edit(user, issue):
        raise HTTPException(403, "You cannot edit this issue.")
    return render(request, "issues/form.html", form_context(issue, user, f"Edit issue #{issue.id}"), user=user)


@router.post("/issues/{issue_id}/edit", dependencies=[Depends(verify_csrf)])
def update_issue(
    issue_id: int, request: Request,
    title: str = Form(...), description: str = Form(...), error_message: str = Form(""),
    category: str = Form("Other"), priority: str = Form("Medium"), status: str = Form("Open"),
    root_cause: str = Form(""), solution: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    issue = get_issue_or_404(db, user, issue_id)
    if not can_edit(user, issue):
        raise HTTPException(403, "You cannot edit this issue.")
    title, description = title.strip(), description.strip()
    if not title or not description:
        return render(request, "issues/form.html",
                      form_context(issue, user, f"Edit issue #{issue.id}", "Title and description are required."),
                      status_code=400, user=user)
    if category not in CATEGORIES or priority not in PRIORITIES or status not in STATUSES:
        raise HTTPException(400, "Invalid category, priority, or status.")

    issue.title, issue.description = title[:160], description
    issue.error_message = error_message.strip()
    issue.category, issue.priority = category, priority
    if is_staff(user):
        issue.status, issue.root_cause, issue.solution = status, root_cause.strip(), solution.strip()
    db.commit()
    flash(request, "Issue updated.")
    return RedirectResponse(url=f"/issues/{issue.id}", status_code=303)


@router.post("/issues/{issue_id}/delete", dependencies=[Depends(verify_csrf)])
def delete_issue(issue_id: int, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    issue = db.get(Issue, issue_id)
    if issue is None:
        raise HTTPException(404, "Issue not found")
    db.delete(issue)
    db.commit()
    flash(request, "Issue deleted.")
    return RedirectResponse(url="/issues", status_code=303)


@router.get("/api/issues")
def api_list_issues(user: User = Depends(require_user), db: Session = Depends(get_db)):
    issues = scoped(db, user).order_by(Issue.id.desc()).all()
    return [
        {"id": i.id, "title": i.title, "category": i.category, "priority": i.priority,
         "status": i.status, "created_at": i.created_at.isoformat()}
        for i in issues
    ]
