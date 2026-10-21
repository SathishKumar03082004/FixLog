"""Kanban board: issues as cards across status columns."""
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import case
from sqlalchemy.orm import Session, joinedload

from ..auth import is_staff, require_staff, require_user, verify_csrf
from ..database import get_db
from ..models import Issue, User
from ..templating import render
from .issue_routes import (
    PRIORITIES,
    STATUSES,
    assignable_users,
    get_issue_or_404,
    resolve_assignee,
    scoped,
    settle_blocked,
    truthy,
)

router = APIRouter()

DONE_STATUS = "Resolved"
VIEWS = ("all", "unfinished", "blocked")
COLUMN_LIMIT = 50  # cards rendered per column; the rest are counted, not drawn

# Critical first, then High, Medium, Low.
_PRIORITY_RANK = case(
    {name: rank for rank, name in enumerate(reversed(PRIORITIES))},
    value=Issue.priority,
    else_=len(PRIORITIES),
)


@router.get("/board")
def board(
    request: Request,
    assignee: str = "",
    priority: str = "",
    view: str = "all",
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if view not in VIEWS:
        view = "all"
    if priority not in PRIORITIES:
        priority = ""

    query = scoped(db, user).options(joinedload(Issue.assignee))

    # assignee filter: "", "unassigned", "me", or a numeric user id
    selected_assignee = ""
    if assignee == "unassigned":
        query = query.filter(Issue.assignee_id.is_(None))
        selected_assignee = "unassigned"
    elif assignee == "me":
        query = query.filter(Issue.assignee_id == user.id)
        selected_assignee = "me"
    elif assignee.isdigit():
        query = query.filter(Issue.assignee_id == int(assignee))
        selected_assignee = assignee
    if priority:
        query = query.filter(Issue.priority == priority)

    issues = query.order_by(_PRIORITY_RANK.asc(), Issue.updated_at.desc()).all()

    # Quick-view counts reflect the assignee/priority filters but not the view itself.
    stats = {
        "all": len(issues),
        "unfinished": sum(1 for i in issues if i.status != DONE_STATUS),
        "blocked": sum(1 for i in issues if i.is_blocked and i.status != DONE_STATUS),
    }

    if view == "unfinished":
        issues = [i for i in issues if i.status != DONE_STATUS]
    elif view == "blocked":
        issues = [i for i in issues if i.is_blocked and i.status != DONE_STATUS]

    # "Unfinished" and "Blocked" are about work still to do, so the Resolved column is dropped.
    visible_statuses = STATUSES if view == "all" else [s for s in STATUSES if s != DONE_STATUS]
    columns = []
    for status in visible_statuses:
        items = [i for i in issues if i.status == status]
        columns.append({
            "status": status,
            "total": len(items),
            "cards": items[:COLUMN_LIMIT],
            "hidden": max(0, len(items) - COLUMN_LIMIT),
        })

    return render(request, "board.html", {
        "columns": columns,
        "stats": stats,
        "view": view,
        "statuses": STATUSES,
        "priorities": PRIORITIES,
        "assignees": assignable_users(db),
        "selected_assignee": selected_assignee,
        "selected_priority": priority,
        "can_manage": is_staff(user),
        "column_limit": COLUMN_LIMIT,
    }, user=user)


def _card_state(issue: Issue) -> dict:
    return {
        "id": issue.id,
        "status": issue.status,
        "is_blocked": bool(issue.is_blocked),
        "blocked_reason": issue.blocked_reason or "",
        "assignee": (
            {"id": issue.assignee.id, "name": issue.assignee.display_name}
            if issue.assignee else None
        ),
    }


@router.post("/api/issues/{issue_id}/triage", dependencies=[Depends(verify_csrf)])
def triage_issue(
    issue_id: int,
    status: Optional[str] = Form(None),
    assignee_id: Optional[str] = Form(None),
    is_blocked: Optional[str] = Form(None),
    blocked_reason: Optional[str] = Form(None),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    """Update any of status / assignee / blocked in one call. Omitted fields are left alone.

    Drag-and-drop sends only `status`; the card dialog sends all four.
    An empty `assignee_id` means "unassign".
    """
    issue = get_issue_or_404(db, user, issue_id)

    if status is not None:
        if status not in STATUSES:
            raise HTTPException(400, "Invalid status.")
        issue.status = status
    if assignee_id is not None:
        issue.assignee_id = resolve_assignee(db, assignee_id)
    if is_blocked is not None:
        issue.is_blocked = truthy(is_blocked)
    if blocked_reason is not None:
        issue.blocked_reason = blocked_reason.strip()[:255]
    settle_blocked(issue)  # resolving or unblocking clears the reason

    db.commit()
    db.refresh(issue)
    return JSONResponse(_card_state(issue))
