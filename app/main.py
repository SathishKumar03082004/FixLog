from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session

from .database import Base, engine, get_db
from .models import Issue

BASE_DIR = Path(__file__).resolve().parent
Base.metadata.create_all(bind=engine)

app = FastAPI(title="FixLog - Problem & Resolution Tracker")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

STATUSES = ["Open", "In Progress", "Resolved"]
PRIORITIES = ["Low", "Medium", "High", "Critical"]
CATEGORIES = ["Python", "Web", "Database", "Networking", "Environment", "Other"]


def dashboard_counts(db: Session):
    total = db.query(Issue).count()
    open_count = db.query(Issue).filter(Issue.status == "Open").count()
    progress_count = db.query(Issue).filter(Issue.status == "In Progress").count()
    resolved_count = db.query(Issue).filter(Issue.status == "Resolved").count()
    return {
        "total": total,
        "open_count": open_count,
        "progress_count": progress_count,
        "resolved_count": resolved_count,
    }


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    recent_issues = db.query(Issue).order_by(Issue.updated_at.desc()).limit(5).all()
    context = {
        "request": request,
        **dashboard_counts(db),
        "recent_issues": recent_issues,
    }
    return templates.TemplateResponse(request=request, name="dashboard.html", context=context)


@app.get("/issues", response_class=HTMLResponse)
def list_issues(
    request: Request,
    q: str = "",
    status: str = "",
    priority: str = "",
    category: str = "",
    db: Session = Depends(get_db),
):
    query = db.query(Issue)
    if q.strip():
        term = f"%{q.strip()}%"
        query = query.filter(
            or_(
                Issue.title.ilike(term),
                Issue.description.ilike(term),
                Issue.error_message.ilike(term),
                Issue.root_cause.ilike(term),
                Issue.solution.ilike(term),
            )
        )
    if status in STATUSES:
        query = query.filter(Issue.status == status)
    if priority in PRIORITIES:
        query = query.filter(Issue.priority == priority)
    if category in CATEGORIES:
        query = query.filter(Issue.category == category)

    issues = query.order_by(Issue.updated_at.desc()).all()
    return templates.TemplateResponse(
        request=request,
        name="issues/list.html",
        context={
            "request": request,
            "issues": issues,
            "q": q,
            "selected_status": status,
            "selected_priority": priority,
            "selected_category": category,
            "statuses": STATUSES,
            "priorities": PRIORITIES,
            "categories": CATEGORIES,
        },
    )


@app.get("/issues/new", response_class=HTMLResponse)
def new_issue_form(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="issues/form.html",
        context={
            "request": request,
            "issue": None,
            "statuses": STATUSES,
            "priorities": PRIORITIES,
            "categories": CATEGORIES,
            "page_title": "Create an issue",
            "error": None,
        },
    )


@app.post("/issues/new")
def create_issue(
    request: Request,
    title: str = Form(...),
    description: str = Form(...),
    error_message: str = Form(""),
    category: str = Form("Other"),
    priority: str = Form("Medium"),
    status: str = Form("Open"),
    root_cause: str = Form(""),
    solution: str = Form(""),
    db: Session = Depends(get_db),
):
    title = title.strip()
    description = description.strip()
    if not title or not description:
        return templates.TemplateResponse(
            request=request,
            name="issues/form.html",
            status_code=400,
            context={
                "request": request, "issue": None, "statuses": STATUSES,
                "priorities": PRIORITIES, "categories": CATEGORIES,
                "page_title": "Create an issue",
                "error": "Title and description are required.",
            },
        )
    if category not in CATEGORIES or priority not in PRIORITIES or status not in STATUSES:
        raise HTTPException(status_code=400, detail="Invalid category, priority, or status.")

    issue = Issue(
        title=title,
        description=description,
        error_message=error_message.strip(),
        category=category,
        priority=priority,
        status=status,
        root_cause=root_cause.strip(),
        solution=solution.strip(),
    )
    db.add(issue)
    db.commit()
    db.refresh(issue)
    return RedirectResponse(url=f"/issues/{issue.id}", status_code=303)


@app.get("/issues/{issue_id}", response_class=HTMLResponse)
def issue_detail(issue_id: int, request: Request, db: Session = Depends(get_db)):
    issue = db.query(Issue).filter(Issue.id == issue_id).first()
    if issue is None:
        raise HTTPException(status_code=404, detail="Issue not found")
    return templates.TemplateResponse(
        request=request, name="issues/detail.html",
        context={"request": request, "issue": issue},
    )


@app.get("/issues/{issue_id}/edit", response_class=HTMLResponse)
def edit_issue_form(issue_id: int, request: Request, db: Session = Depends(get_db)):
    issue = db.query(Issue).filter(Issue.id == issue_id).first()
    if issue is None:
        raise HTTPException(status_code=404, detail="Issue not found")
    return templates.TemplateResponse(
        request=request,
        name="issues/form.html",
        context={
            "request": request, "issue": issue, "statuses": STATUSES,
            "priorities": PRIORITIES, "categories": CATEGORIES,
            "page_title": f"Edit issue #{issue.id}", "error": None,
        },
    )


@app.post("/issues/{issue_id}/edit")
def update_issue(
    issue_id: int,
    request: Request,
    title: str = Form(...),
    description: str = Form(...),
    error_message: str = Form(""),
    category: str = Form("Other"),
    priority: str = Form("Medium"),
    status: str = Form("Open"),
    root_cause: str = Form(""),
    solution: str = Form(""),
    db: Session = Depends(get_db),
):
    issue = db.query(Issue).filter(Issue.id == issue_id).first()
    if issue is None:
        raise HTTPException(status_code=404, detail="Issue not found")
    title = title.strip()
    description = description.strip()
    if not title or not description:
        return templates.TemplateResponse(
            request=request,
            name="issues/form.html",
            status_code=400,
            context={
                "request": request, "issue": issue, "statuses": STATUSES,
                "priorities": PRIORITIES, "categories": CATEGORIES,
                "page_title": f"Edit issue #{issue.id}",
                "error": "Title and description are required.",
            },
        )
    if category not in CATEGORIES or priority not in PRIORITIES or status not in STATUSES:
        raise HTTPException(status_code=400, detail="Invalid category, priority, or status.")

    issue.title = title
    issue.description = description
    issue.error_message = error_message.strip()
    issue.category = category
    issue.priority = priority
    issue.status = status
    issue.root_cause = root_cause.strip()
    issue.solution = solution.strip()
    db.commit()
    return RedirectResponse(url=f"/issues/{issue.id}", status_code=303)


@app.post("/issues/{issue_id}/delete")
def delete_issue(issue_id: int, db: Session = Depends(get_db)):
    issue = db.query(Issue).filter(Issue.id == issue_id).first()
    if issue is None:
        raise HTTPException(status_code=404, detail="Issue not found")
    db.delete(issue)
    db.commit()
    return RedirectResponse(url="/issues", status_code=303)


# Simple JSON endpoint: useful for testing the database/API with Swagger.
@app.get("/api/issues")
def api_list_issues(db: Session = Depends(get_db)):
    issues = db.query(Issue).order_by(Issue.id.desc()).all()
    return [
        {
            "id": item.id,
            "title": item.title,
            "category": item.category,
            "priority": item.priority,
            "status": item.status,
            "created_at": item.created_at.isoformat(),
        }
        for item in issues
    ]
