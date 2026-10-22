"""Screenshot / video / log attachments for bug reports.

Files are stored on disk under <project>/uploads/ with random names; the
database only keeps metadata (see models.IssueAttachment). Nothing here touches
config.py or database.py.
"""
import re
import secrets
from pathlib import Path
from typing import List, Tuple

from fastapi import UploadFile
from sqlalchemy.orm import Session

from .models import Issue, IssueAttachment, User

BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / "uploads"

MAX_FILES_PER_REPORT = 10
MAX_BYTES = {"screenshot": 10 * 1024 * 1024, "video": 50 * 1024 * 1024, "log": 5 * 1024 * 1024}

# extension -> (kind, media type we serve it as). Never trust the browser's content type.
ALLOWED = {
    ".png": ("screenshot", "image/png"),
    ".jpg": ("screenshot", "image/jpeg"),
    ".jpeg": ("screenshot", "image/jpeg"),
    ".gif": ("screenshot", "image/gif"),
    ".webp": ("screenshot", "image/webp"),
    ".mp4": ("video", "video/mp4"),
    ".webm": ("video", "video/webm"),
    ".mov": ("video", "video/quicktime"),
    ".log": ("log", "text/plain; charset=utf-8"),
    ".txt": ("log", "text/plain; charset=utf-8"),
    ".json": ("log", "application/json"),
    ".csv": ("log", "text/csv; charset=utf-8"),
    ".zip": ("log", "application/zip"),
}
ACCEPT_ATTR = ",".join(sorted(ALLOWED))
INLINE_KINDS = {"screenshot", "video"}  # shown in the page; logs are always downloaded

# Cheap content sniffing so a renamed file cannot pose as an image.
_MAGIC = {
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",), ".jpeg": (b"\xff\xd8\xff",),
    ".gif": (b"GIF87a", b"GIF89a"),
    ".webp": (b"RIFF",),
    ".zip": (b"PK\x03\x04", b"PK\x05\x06"),
}


def human_size(num: int) -> str:
    size = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


def _clean_name(name: str) -> str:
    name = Path((name or "").replace("\\", "/")).name
    name = re.sub(r"[^\w.\- ()]", "_", name).strip(" .")
    return name[:200] or "file"


def file_path(attachment: IssueAttachment) -> Path:
    return UPLOAD_DIR / attachment.stored_name


def delete_file(attachment: IssueAttachment):
    try:
        file_path(attachment).unlink(missing_ok=True)
    except OSError:
        pass  # a leftover file is harmless; never block the request on it


def save_uploads(
    db: Session, issue: Issue, files: List[UploadFile], user: User
) -> Tuple[int, List[str]]:
    """Validate and store uploads. Returns (saved_count, error messages).

    One bad file never blocks the others or the report itself.
    """
    files = [f for f in files if f and f.filename]
    if not files:
        return 0, []

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    existing = len(issue.attachments)
    saved, errors = 0, []

    for upload in files:
        original = _clean_name(upload.filename)
        ext = Path(original).suffix.lower()
        if ext not in ALLOWED:
            errors.append(f"{original}: file type not allowed.")
            continue
        if existing + saved >= MAX_FILES_PER_REPORT:
            errors.append(f"{original}: a report can have at most {MAX_FILES_PER_REPORT} attachments.")
            continue

        kind = ALLOWED[ext][0]
        limit = MAX_BYTES[kind]
        stored = f"{secrets.token_hex(16)}{ext}"
        target = UPLOAD_DIR / stored
        size, problem, first = 0, None, b""
        try:
            with open(target, "wb") as out:
                while True:
                    chunk = upload.file.read(1024 * 256)
                    if not chunk:
                        break
                    if not first:
                        first = chunk[:16]
                    size += len(chunk)
                    if size > limit:
                        problem = f"{original}: larger than {human_size(limit)}."
                        break
                    out.write(chunk)
        finally:
            upload.file.close()

        if not problem and size == 0:
            problem = f"{original}: file is empty."
        if not problem and ext in _MAGIC and not first.startswith(_MAGIC[ext]):
            problem = f"{original}: contents do not look like a {ext[1:].upper()} file."
        if problem:
            target.unlink(missing_ok=True)
            errors.append(problem)
            continue

        db.add(IssueAttachment(
            issue_id=issue.id, kind=kind, original_name=original, stored_name=stored,
            size_bytes=size, uploaded_by_id=user.id,
        ))
        saved += 1

    if saved:
        db.commit()
        db.refresh(issue)
    return saved, errors


def remove_attachments(db: Session, issue: Issue, ids: List[int]) -> int:
    """Delete the given attachments, but only ones that belong to this issue."""
    wanted = set(ids)
    removed = 0
    for att in list(issue.attachments):
        if att.id in wanted:
            delete_file(att)
            db.delete(att)
            removed += 1
    if removed:
        db.commit()
        db.refresh(issue)
    return removed


def delete_all_files(issue: Issue):
    for att in issue.attachments:
        delete_file(att)
