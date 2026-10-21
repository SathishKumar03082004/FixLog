from pathlib import Path
from typing import Optional

from fastapi import Request
from fastapi.templating import Jinja2Templates

from .auth import csrf_token, is_staff
from .models import User

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def flash(request: Request, message: str, category: str = "success"):
    request.session.setdefault("flashes", []).append({"m": message, "c": category})


def render(
    request: Request,
    name: str,
    context: Optional[dict] = None,
    status_code: int = 200,
    user: Optional[User] = None,
):
    ctx = dict(context or {})
    ctx.setdefault("user", user)
    ctx["is_staff"] = bool(user and is_staff(user))
    ctx["csrf_token"] = csrf_token(request)
    ctx["flashes"] = request.session.pop("flashes", [])
    return templates.TemplateResponse(request=request, name=name, context=ctx, status_code=status_code)
