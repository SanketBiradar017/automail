"""Public endpoint for the OPT-IN open-tracking pixel.

Only emails whose sender ticked "track opens" contain a pixel. The token is an
unguessable per-email random string; the endpoint reveals nothing, always
returns the same 1x1 GIF, and stores no IP address or user agent.
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import Email
from backend import email_status

router = APIRouter(tags=["Tracking"])

_GIF = (b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00"
        b"\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;")
_HEADERS = {"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"}
MIN_SECONDS_AFTER_SEND = 5   # ignore instant hits: send-time scanners / prefetchers


@router.get("/t/{token}.gif")
def open_pixel(token: str, db: Session = Depends(get_db)):
    try:
        if 10 <= len(token) <= 64:
            email = db.query(Email).filter(Email.track_token == token, Email.open_tracking.is_(True)).first()
            now = datetime.utcnow()
            if email and email.sent_at and now - email.sent_at >= timedelta(seconds=MIN_SECONDS_AFTER_SEND):
                # one logical "opened" fact per email; repeat loads are not re-counted
                email_status.mark_opened(db, email, "open_pixel", now, external_id="pixel")
                db.commit()
    except Exception:
        db.rollback()      # tracking must never break the response
    return Response(content=_GIF, media_type="image/gif", headers=_HEADERS)
