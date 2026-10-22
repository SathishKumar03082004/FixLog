from typing import List

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload, selectinload

from ..attachments import (
    ACCEPT_ATTR, MAX_BYTES, MAX_FILES_PER_REPORT, delete_all_files, human_size,
    remove_attachments, save_uploads,
)
from ..auth import is_staff, require_admin, require_user, verify_csrf
from ..bug_report import (
    AFFECTED_USERS, ENVIRONMENTS, SEVERITIES, apply_report, detect_client, draft_from_form,
    missing_required, parse_bug_id, report_choice_problem, report_form,
)
from ..config import ROLE_ADMIN, ROLE_DEVELOPER, ROLE_USER
from ..database import get_db
from ..models import Issue, IssueActivity, IssueComment, User
from ..templating import flash, render

router = APIRouter()

STATUSES = ["Open", "In Progress", "Resolved"]
PRIORITIES = ["Low", "Medium", "High", "Critical"]
CATEGORIES = ["Python", "Web", "Database", "Networking", "Environment", "Other"]


def scoped(db: Session, user: User):
    """Plain users only ever see issues they reported; staff see everything."""
    query = db.query(Issue).options(joinedload(Issue.reporter), selectinload(Issue.attachments))
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


def assignable_users(db: Session):
    """Active developers/admins - the only people an issue can be assigned to."""
    return (
        db.query(User)
        .filter(User.role.in_([ROLE_DEVELOPER, ROLE_ADMIN]), User.is_active.is_(True))
        .order_by(func.lower(User.username))
        .all()
    )


def resolve_assignee(db: Session, raw):
    """'' -> unassigned (None). Otherwise a validated active staff user id."""
    raw = (raw or "").strip()
    if not raw:
        return None
    if not raw.isdigit():
        raise HTTPException(400, "Invalid assignee.")
    target = db.get(User, int(raw))
    if target is None or not target.is_active or not is_staff(target):
        raise HTTPException(400, "Assignee must be an active developer or admin.")
    return target.id


def truthy(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "on", "yes"}


def settle_blocked(issue: Issue):
    """A resolved or un-blocked issue never carries a stale blocked reason."""
    if issue.status == "Resolved" or not issue.is_blocked:
        issue.is_blocked = False
        issue.blocked_reason = ""


def form_context(issue, user, page_title, error=None, db: Session = None, draft=None, detected=None):
    """`issue` decides the form action/attachments; `draft` (if any) supplies the field values."""
    assignees = assignable_users(db) if (db is not None and is_staff(user)) else []
    return {"issue": issue, "f": draft if draft is not None else issue,
            "statuses": STATUSES, "priorities": PRIORITIES,
            "categories": CATEGORIES, "page_title": page_title, "error": error,
            "assignees": assignees, "severities": SEVERITIES, "environments": ENVIRONMENTS,
            "affected_options": AFFECTED_USERS, "detected": detected or {},
            "accept_attr": ACCEPT_ATTR, "max_files": MAX_FILES_PER_REPORT,
            "upload_limits": {k: human_size(v) for k, v in MAX_BYTES.items()},
            "upload_limits_bytes": MAX_BYTES}


def flash_upload_result(request: Request, saved: int, errors: List[str]):
    for message in errors:
        flash(request, f"Attachment skipped - {message}", "warning")


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
    severity: str = "",
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    query = scoped(db, user)
    if q.strip():
        term = f"%{q.strip()}%"
        conditions = [
            Issue.title.ilike(term), Issue.description.ilike(term), Issue.error_message.ilike(term),
            Issue.root_cause.ilike(term), Issue.solution.ilike(term),
            Issue.steps_to_reproduce.ilike(term), Issue.actual_result.ilike(term),
        ]
        bug_number = parse_bug_id(q)
        if bug_number is not None:
            conditions.append(Issue.id == bug_number)  # "BUG-00042", "#42" or "42"
        query = query.filter(or_(*conditions))
    if status in STATUSES:
        query = query.filter(Issue.status == status)
    if priority in PRIORITIES:
        query = query.filter(Issue.priority == priority)
    if severity in SEVERITIES:
        query = query.filter(Issue.severity == severity)
    if category in CATEGORIES:
        query = query.filter(Issue.category == category)
    issues = query.order_by(Issue.updated_at.desc()).all()
    return render(request, "issues/list.html", {
        "issues": issues, "q": q, "selected_status": status, "selected_priority": priority,
        "selected_category": category, "selected_severity": severity, "statuses": STATUSES,
        "priorities": PRIORITIES, "severities": SEVERITIES, "categories": CATEGORIES}, user=user)


@router.get("/issues/new")
def new_issue_form(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    detected = detect_client(request.headers.get("user-agent", ""))
    return render(request, "issues/form.html",
                  form_context(None, user, "Report a bug", db=db, detected=detected), user=user)


@router.post("/issues/new", dependencies=[Depends(verify_csrf)])
def create_issue(
    request: Request,
    title: str = Form(...), description: str = Form(...), error_message: str = Form(""),
    category: str = Form("Other"), priority: str = Form("Medium"), status: str = Form("Open"),
    root_cause: str = Form(""), solution: str = Form(""),
    assignee_id: str = Form(""), is_blocked: str = Form(""), blocked_reason: str = Form(""),
    report: dict = Depends(report_form), attachments: List[UploadFile] = File(default=[]),
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    title, description = title.strip(), description.strip()
    submitted = {"title": title, "description": description, "error_message": error_message,
                 "category": category, "priority": priority, "status": status,
                 "root_cause": root_cause, "solution": solution, "assignee_id": assignee_id,
                 "is_blocked": is_blocked, "blocked_reason": blocked_reason}
    problem = None
    if not title or not description:
        problem = "Title and description are required."
    else:
        problem = missing_required(report)
    if problem:
        return render(request, "issues/form.html",
                      form_context(None, user, "Report a bug", problem, db=db,
                                   draft=draft_from_form(submitted, report)),
                      status_code=400, user=user)
    if category not in CATEGORIES or priority not in PRIORITIES or status not in STATUSES:
        raise HTTPException(400, "Invalid category, priority, or status.")
    choice_problem = report_choice_problem(report)
    if choice_problem:
        raise HTTPException(400, choice_problem)

    issue = Issue(title=title[:160], description=description, error_message=error_message.strip(),
                  category=category, priority=priority, reported_by_id=user.id)
    apply_report(issue, report)
    if is_staff(user):  # only developers/admins may set triage fields
        issue.status, issue.root_cause, issue.solution = status, root_cause.strip(), solution.strip()
        issue.assignee_id = resolve_assignee(db, assignee_id)
        issue.is_blocked = truthy(is_blocked)
        issue.blocked_reason = blocked_reason.strip()[:255]
        settle_blocked(issue)
    db.add(issue)
    db.commit()
    db.refresh(issue)
    saved, upload_errors = save_uploads(db, issue, attachments, user)
    flash_upload_result(request, saved, upload_errors)
    flash(request, f"Bug report {issue.bug_id} recorded.")
    return RedirectResponse(url=f"/issues/{issue.id}", status_code=303)


@router.get("/issues/{issue_id}")
def issue_detail(issue_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    issue = get_issue_or_404(db, user, issue_id)
    comments = (db.query(IssueComment).filter(IssueComment.issue_id == issue.id,
                 IssueComment.parent_id.is_(None)).options(joinedload(IssueComment.author),
                 joinedload(IssueComment.replies).joinedload(IssueComment.author))
                 .order_by(IssueComment.created_at.asc()).all())
    activities = (db.query(IssueActivity).filter(IssueActivity.issue_id == issue.id)
                  .options(joinedload(IssueActivity.actor))
                  .order_by(IssueActivity.created_at.desc()).limit(100).all())
    return render(request, "issues/detail.html",
                  {"issue": issue, "can_edit": can_edit(user, issue), "can_delete": user.role == "admin",
                   "comments": comments, "activities": activities, "current_user": user}, user=user)


@router.get("/issues/{issue_id}/edit")
def edit_issue_form(issue_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    issue = get_issue_or_404(db, user, issue_id)
    if not can_edit(user, issue):
        raise HTTPException(403, "You cannot edit this issue.")
    return render(request, "issues/form.html", form_context(issue, user, f"Edit {issue.bug_id}", db=db), user=user)


@router.post("/issues/{issue_id}/edit", dependencies=[Depends(verify_csrf)])
def update_issue(
    issue_id: int, request: Request,
    title: str = Form(...), description: str = Form(...), error_message: str = Form(""),
    category: str = Form("Other"), priority: str = Form("Medium"), status: str = Form("Open"),
    root_cause: str = Form(""), solution: str = Form(""),
    assignee_id: str = Form(""), is_blocked: str = Form(""), blocked_reason: str = Form(""),
    report: dict = Depends(report_form), attachments: List[UploadFile] = File(default=[]),
    remove_attachments_ids: List[int] = Form(default=[], alias="remove_attachment"),
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    issue = get_issue_or_404(db, user, issue_id)
    if not can_edit(user, issue):
        raise HTTPException(403, "You cannot edit this issue.")
    title, description = title.strip(), description.strip()
    if not title or not description:
        submitted = {"title": title, "description": description, "error_message": error_message,
                     "category": category, "priority": priority, "status": status,
                     "root_cause": root_cause, "solution": solution, "assignee_id": assignee_id,
                     "is_blocked": is_blocked, "blocked_reason": blocked_reason}
        return render(request, "issues/form.html",
                      form_context(issue, user, f"Edit {issue.bug_id}", "Title and description are required.",
                                   db=db, draft=draft_from_form(submitted, report)),
                      status_code=400, user=user)
    if category not in CATEGORIES or priority not in PRIORITIES or status not in STATUSES:
        raise HTTPException(400, "Invalid category, priority, or status.")
    choice_problem = report_choice_problem(report)
    if choice_problem:
        raise HTTPException(400, choice_problem)

    issue.title, issue.description = title[:160], description
    issue.error_message = error_message.strip()
    issue.category, issue.priority = category, priority
    apply_report(issue, report)
    if is_staff(user):
        new_assignee_id = resolve_assignee(db, assignee_id)
        if issue.status != status:
            db.add(IssueActivity(issue_id=issue.id, actor_id=user.id, action="changed", field_name="status",
                                 old_value=issue.status, new_value=status))
        if issue.assignee_id != new_assignee_id:
            old_assignee = db.get(User, issue.assignee_id) if issue.assignee_id else None
            new_assignee = db.get(User, new_assignee_id) if new_assignee_id else None
            db.add(IssueActivity(issue_id=issue.id, actor_id=user.id, action="changed", field_name="assignee",
                                 old_value=old_assignee.display_name if old_assignee else "Unassigned",
                                 new_value=new_assignee.display_name if new_assignee else "Unassigned"))
        issue.status, issue.root_cause, issue.solution = status, root_cause.strip(), solution.strip()
        issue.assignee_id = new_assignee_id
        issue.is_blocked = truthy(is_blocked)
        issue.blocked_reason = blocked_reason.strip()[:255]
        settle_blocked(issue)
    db.commit()
    remove_attachments(db, issue, remove_attachments_ids)
    saved, upload_errors = save_uploads(db, issue, attachments, user)
    flash_upload_result(request, saved, upload_errors)
    flash(request, f"{issue.bug_id} updated.")
    return RedirectResponse(url=f"/issues/{issue.id}", status_code=303)


@router.post("/issues/{issue_id}/delete", dependencies=[Depends(verify_csrf)])
def delete_issue(issue_id: int, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    issue = db.get(Issue, issue_id)
    if issue is None:
        raise HTTPException(404, "Issue not found")
    delete_all_files(issue)
    db.delete(issue)
    db.commit()
    flash(request, "Issue deleted.")
    return RedirectResponse(url="/issues", status_code=303)


@router.get("/api/issues")
def api_list_issues(user: User = Depends(require_user), db: Session = Depends(get_db)):
    issues = scoped(db, user).order_by(Issue.id.desc()).all()
    return [
        {"id": i.id, "bug_id": i.bug_id, "title": i.title, "category": i.category,
         "priority": i.priority, "severity": i.severity, "environment": i.environment,
         "affected_users": i.affected_users, "attachment_count": len(i.attachments),
         "status": i.status, "is_blocked": bool(i.is_blocked),
         "assignee": i.assignee.display_name if i.assignee else None,
         "created_at": i.created_at.isoformat(), "reported_at": i.reported_at.isoformat()}
        for i in issues
    ]


@router.post("/issues/{issue_id}/comments", dependencies=[Depends(verify_csrf)])
def add_issue_comment(
    issue_id: int, request: Request, body: str = Form(...), parent_id: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    issue = get_issue_or_404(db, user, issue_id)
    body = body.strip()
    if not body:
        flash(request, "Comment cannot be empty.", "warning")
        return RedirectResponse(url=f"/issues/{issue.id}#discussion", status_code=303)
    if len(body) > 10000:
        flash(request, "Comments must be 10,000 characters or fewer.", "warning")
        return RedirectResponse(url=f"/issues/{issue.id}#discussion", status_code=303)
    parent = None
    if parent_id.strip():
        if not parent_id.isdigit():
            raise HTTPException(400, "Invalid reply target.")
        parent = db.query(IssueComment).filter_by(id=int(parent_id), issue_id=issue.id).first()
        if parent is None or parent.parent_id is not None:
            raise HTTPException(400, "Replies must target a top-level comment on this issue.")
    db.add(IssueComment(issue_id=issue.id, author_id=user.id, parent_id=parent.id if parent else None, body=body))
    db.add(IssueActivity(issue_id=issue.id, actor_id=user.id, action="commented", field_name="comment",
                         old_value="", new_value="Replied to a comment" if parent else "Added a comment"))
    db.commit()
    flash(request, "Reply added." if parent else "Comment added.")
    return RedirectResponse(url=f"/issues/{issue.id}#discussion", status_code=303)


@router.post("/issues/{issue_id}/comments/{comment_id}/edit", dependencies=[Depends(verify_csrf)])
def edit_issue_comment(
    issue_id: int, comment_id: int, request: Request, body: str = Form(...),
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    issue = get_issue_or_404(db, user, issue_id)
    comment = db.query(IssueComment).filter_by(id=comment_id, issue_id=issue.id).first()
    if comment is None:
        raise HTTPException(404, "Comment not found.")
    if comment.author_id != user.id and user.role != ROLE_ADMIN:
        raise HTTPException(403, "Only the comment author or an admin can edit this comment.")
    body = body.strip()
    if not body or len(body) > 10000:
        flash(request, "Comment must contain 1–10,000 characters.", "warning")
        return RedirectResponse(url=f"/issues/{issue.id}#discussion", status_code=303)
    comment.body = body
    db.add(IssueActivity(issue_id=issue.id, actor_id=user.id, action="edited", field_name="comment",
                         old_value="Comment text", new_value="Updated a comment"))
    db.commit()
    flash(request, "Comment updated.")
    return RedirectResponse(url=f"/issues/{issue.id}#discussion", status_code=303)


@router.post("/issues/{issue_id}/comments/{comment_id}/delete", dependencies=[Depends(verify_csrf)])
def delete_issue_comment(
    issue_id: int, comment_id: int, request: Request,
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    issue = get_issue_or_404(db, user, issue_id)
    comment = db.query(IssueComment).filter_by(id=comment_id, issue_id=issue.id).first()
    if comment is None:
        raise HTTPException(404, "Comment not found.")
    if comment.author_id != user.id and user.role != ROLE_ADMIN:
        raise HTTPException(403, "Only the comment author or an admin can delete this comment.")
    db.add(IssueActivity(issue_id=issue.id, actor_id=user.id, action="deleted", field_name="comment",
                         old_value="Comment", new_value="Deleted a comment"))
    db.delete(comment)
    db.commit()
    flash(request, "Comment deleted.")
    return RedirectResponse(url=f"/issues/{issue.id}#discussion", status_code=303)
