"""WhatsApp-style delivery status for sent emails - evidence based.

Status ladder (highest applicable wins):
  failed   Gmail rejected the send                      (red)
  bounced  a delivery-failure notice arrived            (red)
  replied  a human replied in the thread                (green double tick)
  opened   read receipt (MDN), opt-in pixel, or a reply (you can't reply to what you haven't read)
  delivered  positive evidence the message arrived      (grey double tick)
  sent     Gmail API accepted the send request          (grey single tick)

Nothing is inferred. In particular "delivered" is only set from:
  dsn          a success delivery-status notification from the recipient's server
  reply        the recipient replied (they obviously received it)
  auto_reply   an automatic reply (out-of-office) came back
  read_receipt a read-receipt (MDN) came back
  open_pixel   the opt-in tracking pixel was fetched
Gmail itself gives no per-recipient delivery receipt, so most emails stay on
"sent" until one of those arrives. Every transition is logged in email_events
with its source; (email, type, external_id) is unique so retries are idempotent.
"""

import secrets
from datetime import datetime
from typing import Iterable, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.models import Email, EmailEvent

LADDER = ["failed", "bounced", "replied", "opened", "delivered", "sent", "pending"]

STATUS_META = {
    "pending":   {"label": "Pending",   "tick": "none",   "tip": "Not sent yet."},
    "sent":      {"label": "Sent",      "tick": "single", "tip": "Sent: Gmail accepted the message. Delivery to the recipient is not confirmed yet."},
    "delivered": {"label": "Delivered", "tick": "double", "tip": "Delivered: we have evidence the message reached the recipient's mail server."},
    "opened":    {"label": "Opened",    "tick": "blue",   "tip": "Opened: a read receipt or the opt-in tracking pixel confirmed it was opened."},
    "replied":   {"label": "Replied",   "tick": "green",  "tip": "Replied: the recipient answered in the Gmail thread."},
    "bounced":   {"label": "Bounced",   "tick": "x",      "tip": "Bounced: a delivery-failure notice came back."},
    "failed":    {"label": "Failed",    "tick": "x",      "tip": "Failed: Gmail rejected the send request."},
}


def derive_status(e: Email) -> str:
    if e.status == "failed":
        return "failed"
    if e.status != "sent":
        return "pending"
    if e.bounced_at:
        return "bounced"
    if e.replied_at:
        return "replied"
    if e.opened_at:
        return "opened"
    if e.delivered_at:
        return "delivered"
    return "sent"


def status_payload(e: Email) -> dict:
    """What the UI needs to draw ticks + tooltip for one email."""
    s = derive_status(e)
    iso = lambda d: d.isoformat() + "Z" if d else None
    return {
        "state": s,
        "label": STATUS_META[s]["label"],
        "tick": STATUS_META[s]["tick"],
        "tip": STATUS_META[s]["tip"],
        "sent_at": iso(e.sent_at),
        "delivered_at": iso(e.delivered_at), "delivered_source": e.delivered_source,
        "opened_at": iso(e.opened_at), "opened_source": e.opened_source,
        "replied_at": iso(e.replied_at),
        "bounced_at": iso(e.bounced_at), "bounce_reason": e.bounce_reason,
        "open_tracking": bool(e.open_tracking),
        "error": e.error_message if e.status == "failed" else None,
    }


# ------------------------------------------------------------------ events

def record_event(db: Session, email: Email, event_type: str, source: str,
                 when: Optional[datetime] = None, external_id: str = "",
                 detail: Optional[str] = None) -> bool:
    """Appends an event unless the same (email, type, external_id) exists.
    Returns True if it was new."""
    when = when or datetime.utcnow()
    exists = db.query(EmailEvent.id).filter(
        EmailEvent.email_id == email.id,
        EmailEvent.event_type == event_type,
        EmailEvent.external_id == (external_id or ""),
    ).first()
    if exists:
        return False
    try:
        with db.begin_nested():
            db.add(EmailEvent(email_id=email.id, event_type=event_type, source=source,
                              external_id=external_id or "", occurred_at=when,
                              detail=(detail or None) and detail[:1000]))
    except IntegrityError:      # lost a race with another worker: already recorded
        return False
    email.status_updated_at = datetime.utcnow()
    return True


def _earliest(current: Optional[datetime], new: datetime) -> datetime:
    return new if current is None or new < current else current


def mark_sent(db, email, when=None):
    email.status = "sent"
    when = when or email.sent_at or datetime.utcnow()
    email.sent_at = email.sent_at or when
    return record_event(db, email, "sent", "gmail_api", when, external_id=email.gmail_message_id or "")


def mark_failed(db, email, error: str = ""):
    email.status = "failed"
    return record_event(db, email, "failed", "gmail_api", datetime.utcnow(), detail=error)


def mark_delivered(db, email, source: str, when: datetime, external_id: str = "") -> bool:
    new = record_event(db, email, "delivered", source, when, external_id)
    if email.delivered_at is None or when < email.delivered_at:
        email.delivered_at, email.delivered_source = when, source
    return new


def mark_opened(db, email, source: str, when: datetime, external_id: str = "") -> bool:
    new = record_event(db, email, "opened", source, when, external_id)
    if email.opened_at is None or when < email.opened_at:
        email.opened_at, email.opened_source = when, source
    # a receipt / pixel hit is itself proof the message was delivered
    mark_delivered(db, email, source, when, external_id)
    return new


def mark_replied(db, email, when: datetime, message_id: str) -> bool:
    new = record_event(db, email, "replied", "thread_sync", when, message_id)
    if email.replied_at is None or when < email.replied_at:
        email.replied_at, email.replied_source, email.replied_message_id = when, "thread_sync", message_id
    mark_delivered(db, email, "reply", when, message_id)
    # A reply proves the message was read. Keep an earlier receipt/pixel time if there is one.
    if email.opened_at is None or when < email.opened_at:
        email.opened_at, email.opened_source = when, "reply"
    record_event(db, email, "opened", "reply", when, message_id)
    return new


def mark_bounced(db, email, when: datetime, reason: str, message_id: str) -> bool:
    new = record_event(db, email, "bounced", "dsn", when, message_id, detail=reason)
    if email.bounced_at is None or when < email.bounced_at:
        email.bounced_at, email.bounce_reason = when, (reason or "")[:500]
    return new


def apply_thread_findings(db, email, info: dict) -> dict:
    """Applies the output of gmail_sender.inspect_thread to one email.
    Safe to call repeatedly with the same data."""
    counts = {"replied": 0, "bounced": 0, "opened": 0, "delivered": 0}
    for at, src, mid in info.get("delivered", []):
        counts["delivered"] += mark_delivered(db, email, src, at, mid)
    for at, src, mid in info.get("opened", []):
        counts["opened"] += mark_opened(db, email, src, at, mid)
    if info.get("bounced_at"):
        counts["bounced"] += mark_bounced(db, email, info["bounced_at"], info.get("bounce_reason") or "",
                                          info.get("bounce_message_id") or "")
    if info.get("replied_at"):
        counts["replied"] += mark_replied(db, email, info["replied_at"], info.get("replied_message_id") or "")
    return counts


def new_track_token() -> str:
    return secrets.token_urlsafe(18)


# ---------------------------------------------------------------- backfill

def backfill(db: Session, connected_email: Optional[str] = None) -> int:
    """One-off/idempotent: gives pre-existing emails their event rows and
    attributes unowned rows to the connected account."""
    n = 0
    if connected_email:
        n += db.query(Email).filter(Email.sender_email.is_(None)).update(
            {Email.sender_email: connected_email}, synchronize_session=False)

    rows = (db.query(Email)
            .outerjoin(EmailEvent, EmailEvent.email_id == Email.id)
            .filter(EmailEvent.id.is_(None), Email.status.in_(("sent", "failed")))
            .all())
    # replied emails from before this rule: a reply proves it was opened
    for e in db.query(Email).filter(Email.replied_at.isnot(None), Email.opened_at.is_(None)).all():
        e.opened_at, e.opened_source = e.replied_at, "reply"
        record_event(db, e, "opened", "reply", e.replied_at, e.replied_message_id or "")
        n += 1

    for e in rows:
        if e.status == "failed":
            mark_failed(db, e, e.error_message or "")
            continue
        mark_sent(db, e, e.sent_at)
        if e.replied_at:
            e.replied_source = e.replied_source or "thread_sync"
            mark_replied(db, e, e.replied_at, e.replied_message_id or "")
        if e.bounced_at:
            mark_bounced(db, e, e.bounced_at, e.bounce_reason or "", "")
        n += 1
    db.commit()
    return n
