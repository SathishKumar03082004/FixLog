"""Smart Bug Report helpers: choice lists, form parsing, client auto-detection."""
import re
from types import SimpleNamespace
from typing import Optional

from fastapi import Form

SEVERITIES = ["Low", "Medium", "High", "Critical"]
ENVIRONMENTS = ["Production", "Staging", "QA", "Development", "Local"]
AFFECTED_USERS = ["Just me", "A few users", "Many users", "All users"]

# (field, human label) for the fields a new report must fill in.
REQUIRED_ON_CREATE = [
    ("steps_to_reproduce", "Steps to reproduce"),
    ("expected_result", "Expected result"),
    ("actual_result", "Actual result"),
]

REPORT_FIELDS = [
    "steps_to_reproduce", "expected_result", "actual_result", "browser", "os_name",
    "app_version", "environment", "severity", "affected_users",
]


def report_form(
    steps_to_reproduce: str = Form(""),
    expected_result: str = Form(""),
    actual_result: str = Form(""),
    browser: str = Form(""),
    os_name: str = Form(""),
    app_version: str = Form(""),
    environment: str = Form(""),
    severity: str = Form("Medium"),
    affected_users: str = Form(""),
) -> dict:
    """FastAPI dependency: the structured bug-report part of the issue form."""
    return {
        "steps_to_reproduce": steps_to_reproduce.strip(),
        "expected_result": expected_result.strip(),
        "actual_result": actual_result.strip(),
        "browser": browser.strip()[:80],
        "os_name": os_name.strip()[:80],
        "app_version": app_version.strip()[:40],
        "environment": environment.strip(),
        "severity": severity.strip(),
        "affected_users": affected_users.strip(),
    }


def report_choice_problem(report: dict) -> Optional[str]:
    """Return an error message if a dropdown value was tampered with, else None."""
    if report["severity"] not in SEVERITIES:
        return "Invalid severity."
    if report["environment"] and report["environment"] not in ENVIRONMENTS:
        return "Invalid environment."
    if report["affected_users"] and report["affected_users"] not in AFFECTED_USERS:
        return "Invalid affected-users value."
    return None


def missing_required(report: dict) -> Optional[str]:
    """Message naming the required bug-report fields left empty (new reports only)."""
    missing = [label for key, label in REQUIRED_ON_CREATE if not report[key]]
    if missing:
        return "Please fill in: " + ", ".join(missing) + "."
    return None


def apply_report(issue, report: dict):
    for key in REPORT_FIELDS:
        setattr(issue, key, report[key])


def draft_from_form(form: dict, report: dict) -> SimpleNamespace:
    """Rebuild an unsaved 'issue-like' object so a failed submit keeps what the user typed."""
    assignee_raw = str(form.get("assignee_id", "") or "").strip()
    return SimpleNamespace(
        title=form.get("title", ""), description=form.get("description", ""),
        error_message=form.get("error_message", ""), category=form.get("category", "Other"),
        priority=form.get("priority", "Medium"), status=form.get("status", "Open"),
        root_cause=form.get("root_cause", ""), solution=form.get("solution", ""),
        assignee_id=int(assignee_raw) if assignee_raw.isdigit() else None,
        is_blocked=str(form.get("is_blocked", "")).lower() in {"1", "true", "on", "yes"},
        blocked_reason=form.get("blocked_reason", ""), **report,
    )


# ------------------------------------------------------ client auto-detection
def detect_client(user_agent: str) -> dict:
    """Best-effort browser and OS from a User-Agent string, to pre-fill the form."""
    ua = user_agent or ""
    browser = ""
    for label, pattern in (
        ("Edge", r"Edg(?:e|A|iOS)?/([\d.]+)"),
        ("Opera", r"OPR/([\d.]+)"),
        ("Firefox", r"(?:Firefox|FxiOS)/([\d.]+)"),
        ("Chrome", r"(?:Chrome|CriOS)/([\d.]+)"),
        ("Safari", r"Version/([\d.]+).*Safari/"),
    ):
        m = re.search(pattern, ua)
        if m:
            browser = f"{label} {m.group(1).split('.')[0]}"
            break

    os_name = ""
    if "Windows NT 10" in ua:
        os_name = "Windows 10/11"
    elif "Windows" in ua:
        os_name = "Windows"
    elif re.search(r"iPhone|iPad|iPod", ua):
        os_name = "iOS"
    elif "Android" in ua:
        m = re.search(r"Android ([\d.]+)", ua)
        os_name = f"Android {m.group(1)}" if m else "Android"
    elif "Mac OS X" in ua or "Macintosh" in ua:
        os_name = "macOS"
    elif "CrOS" in ua:
        os_name = "ChromeOS"
    elif "Linux" in ua:
        os_name = "Linux"
    return {"browser": browser, "os_name": os_name}


_BUG_ID_RE = re.compile(r"^\s*(?:bug[-\s]?|#)?0*(\d+)\s*$", re.IGNORECASE)


def parse_bug_id(term: str) -> Optional[int]:
    """'BUG-00042', 'bug 42', '#42' or '42' -> 42, otherwise None."""
    m = _BUG_ID_RE.match(term or "")
    return int(m.group(1)) if m else None
