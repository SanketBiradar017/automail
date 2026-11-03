import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from googleapiclient.errors import HttpError

from backend import gmail_inbox as inbox
from backend import email_status
from backend.database import get_db
from backend.models import Email
from backend.gmail_auth import SenderNotAuthenticatedError

router = APIRouter(prefix="/api/inbox", tags=["Inbox"])

FOLDERS = {
    "inbox": "in:inbox",
    "unread": "in:inbox is:unread",
    "starred": "is:starred",
    "sent": "in:sent",
}


TIME_RANGES = {"1d": "newer_than:1d", "7d": "newer_than:7d", "30d": "newer_than:30d",
               "3m": "newer_than:3m", "1y": "newer_than:1y"}
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ADDR = re.compile(r"[^\w.@+\-' ]")


def build_query(folder: str, q: str, when: str, after: Optional[str], before: Optional[str],
                has_attachment: bool, unread: bool, addr: str, addr_field: str) -> str:
    """Gmail search string from the filter bar. Every filter value is validated
    or stripped so a value can never inject extra operators."""
    parts = [FOLDERS[folder]]
    if when in TIME_RANGES:
        parts.append(TIME_RANGES[when])
    elif when == "custom":
        if after and _DATE.match(after):
            parts.append("after:" + after.replace("-", "/"))
        if before and _DATE.match(before):
            parts.append("before:" + before.replace("-", "/"))
    if has_attachment:
        parts.append("has:attachment")
    if unread and folder != "unread":
        parts.append("is:unread")
    clean = _ADDR.sub("", addr or "").strip()
    if clean:
        parts.append(f'{addr_field}:"{clean}"')
    if q.strip():
        parts.append(q.strip())
    return " ".join(parts)


@router.get("")
def list_messages(
    folder: str = Query("inbox", pattern="^(inbox|unread|starred|sent)$"),
    q: str = Query("", max_length=200),
    when: str = Query("any", pattern="^(any|1d|7d|30d|3m|1y|custom)$"),
    after: Optional[str] = Query(None, max_length=10),
    before: Optional[str] = Query(None, max_length=10),
    has_attachment: bool = Query(False),
    unread: bool = Query(False),
    addr: str = Query("", max_length=100, description="recipient (sent) or sender (other folders)"),
    limit: int = Query(20, ge=1, le=50),
    page_token: Optional[str] = Query(None, max_length=500),
    db: Session = Depends(get_db),
):
    query = build_query(folder, q, when, after, before, has_attachment, unread, addr,
                        "to" if folder == "sent" else "from")
    try:
        items, next_token = inbox.list_threads(query, limit, page_token)
    except SenderNotAuthenticatedError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except HttpError as exc:
        raise HTTPException(status_code=502, detail=f"Gmail API error: {exc}")
    if folder == "sent" and items:
        # Attach delivery status to threads that AutoMail itself sent.
        by_thread = {}
        rows = db.query(Email).filter(Email.thread_id.in_([t["thread_id"] for t in items])).all()
        for e in rows:
            by_thread.setdefault(e.thread_id, e)
        for t in items:
            e = by_thread.get(t["thread_id"])
            t["tracking"] = email_status.status_payload(e) if e else None
    return {"success": True, "folder": folder, "threads": items, "next_page_token": next_token}
