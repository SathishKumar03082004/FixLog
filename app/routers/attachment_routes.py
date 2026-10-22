"""Serves bug-report attachments. Access follows the same rules as the issue itself."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..attachments import ALLOWED, INLINE_KINDS, file_path
from ..auth import require_user
from ..database import get_db
from ..models import IssueAttachment, User
from .issue_routes import get_issue_or_404

router = APIRouter()


@router.get("/attachments/{attachment_id}")
def get_attachment(
    attachment_id: int, download: int = 0,
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    att = db.get(IssueAttachment, attachment_id)
    if att is None:
        raise HTTPException(404, "Attachment not found")
    get_issue_or_404(db, user, att.issue_id)  # plain users: only their own reports

    path = file_path(att)
    if not path.is_file():
        raise HTTPException(404, "Attachment file is missing")

    ext = "." + att.stored_name.rsplit(".", 1)[-1]
    media_type = ALLOWED.get(ext, ("", "application/octet-stream"))[1]
    inline = att.kind in INLINE_KINDS and not download
    return FileResponse(
        path, media_type=media_type, filename=att.original_name,
        content_disposition_type="inline" if inline else "attachment",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600"},
    )
