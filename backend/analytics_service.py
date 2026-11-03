"""Email analytics: Gmail reply/bounce sync plus aggregate reporting.

Definitions
- sent:      Gmail accepted the message (status == "sent")
- bounced:   a delivery-failure notice (mailer-daemon/postmaster) arrived in the thread
- delivered: sent - bounced (Gmail does not give per-recipient delivery receipts,
             so "delivered" means "sent and no bounce seen")
- replied:   a non-bounce inbound message arrived in the thread after sending
- reply rate = replied / delivered
Open tracking is intentionally not implemented (pixels are unreliable and
privacy-hostile).
"""
import csv
import io
import re
import threading
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from backend.database import SessionLocal
from backend.gmail_auth import get_gmail_service, SenderNotAuthenticatedError
from backend.gmail_sender import inspect_thread, get_thread_message_id
from backend.models import Email, EmailCampaign, FollowUpSequence
from backend import email_status

TRACK_WINDOW_DAYS = 30        # keep checking for replies/bounces this long
RECHECK_MINUTES = 10          # min gap between Gmail checks for one email
MAX_PER_SYNC = 200

_sync_lock = threading.Lock()


# ---------------------------------------------------------------- sync

def sync_tracking(max_items: int = MAX_PER_SYNC) -> dict:
    """Checks Gmail for replies/bounces on unresolved sent emails. Safe to call
    often: skips if a sync is already running, throttles per email."""

    if not _sync_lock.acquire(blocking=False):
        return {"checked": 0, "skipped": "sync already running"}

    db = SessionLocal()
    try:
        try:
            service, _sender = get_gmail_service()
        except SenderNotAuthenticatedError as exc:
            return {"checked": 0, "skipped": str(exc)}

        now = datetime.utcnow()
        rows = (
            db.query(Email)
            .filter(
                Email.status == "sent",
                Email.sent_at.isnot(None),
                Email.sent_at >= now - timedelta(days=TRACK_WINDOW_DAYS),
                Email.replied_at.is_(None),
                Email.bounced_at.is_(None),
                or_(Email.sender_email.is_(None), Email.sender_email == _sender),
                or_(Email.tracked_at.is_(None),
                    Email.tracked_at < now - timedelta(minutes=RECHECK_MINUTES)),
            )
            .order_by(Email.tracked_at.asc().nullsfirst())
            .limit(max_items)
            .all()
        )

        checked = replied = bounced = opened = delivered = errors = 0
        for email in rows:
            try:
                if not email.thread_id and email.gmail_message_id:
                    email.thread_id = get_thread_message_id(service, email.gmail_message_id)
                if email.thread_id:
                    info = inspect_thread(service, email.thread_id, email.sent_at)
                    got = email_status.apply_thread_findings(db, email, info)
                    replied += got["replied"]; bounced += got["bounced"]
                    opened += got["opened"]; delivered += got["delivered"]
                checked += 1
                email.tracked_at = now
            except Exception:
                # e.g. message from a previously connected account (404) or a
                # transient API error: leave tracked_at untouched on API errors
                # so it is retried next run, but never abort the whole sync.
                errors += 1
                email.tracked_at = now - timedelta(minutes=RECHECK_MINUTES // 2)

        db.commit()
        return {"checked": checked, "new_replies": replied, "new_bounces": bounced,
                "new_opens": opened, "new_delivered": delivered, "errors": errors}
    finally:
        db.close()
        _sync_lock.release()


# ---------------------------------------------------------- aggregation

def _kind(name: str) -> str:
    if name.startswith("Single:"):
        return "single"
    if name.startswith("Scheduled:"):
        return "scheduled"
    return "bulk"


def _rate(num: int, den: int) -> float:
    return round(100.0 * num / den, 1) if den else 0.0


def _norm_subject(subject: str) -> str:
    s = re.sub(r"^\s*(re|fwd?):\s*", "", subject or "", flags=re.I)
    return re.sub(r"\s+", " ", s).strip().lower()


class _Counts:
    __slots__ = ("total", "sent", "failed", "bounced", "replied")

    def __init__(self):
        self.total = self.sent = self.failed = self.bounced = self.replied = 0

    def add(self, e: Email):
        self.total += 1
        if e.status == "failed":
            self.failed += 1
        elif e.status == "sent":
            self.sent += 1
            if e.bounced_at:
                self.bounced += 1
            if e.replied_at:
                self.replied += 1

    def as_dict(self) -> dict:
        delivered = self.sent - self.bounced
        return {
            "total": self.total,
            "sent": self.sent,
            "failed": self.failed,
            "delivered": delivered,
            "bounced": self.bounced,
            "replied": self.replied,
            "bounce_rate": _rate(self.bounced, self.sent),
            "reply_rate": _rate(self.replied, delivered),
        }


def _load(db: Session, days: Optional[int], kind: str):
    q = db.query(Email, EmailCampaign).join(
        EmailCampaign, Email.campaign_id == EmailCampaign.id
    )
    if days:
        since = datetime.utcnow() - timedelta(days=days)
        q = q.filter(Email.created_at >= since)
    rows = q.all()
    if kind != "all":
        rows = [(e, c) for e, c in rows if _kind(c.name) == kind]
    return rows


def build_overview(db: Session, days: Optional[int] = 30, kind: str = "all") -> dict:
    rows = _load(db, days, kind)

    totals = _Counts()
    by_campaign = defaultdict(_Counts)
    campaign_meta = {}
    by_subject = defaultdict(_Counts)
    subject_label = {}
    by_day = defaultdict(lambda: {"sent": 0, "replied": 0})

    for e, c in rows:
        totals.add(e)
        by_campaign[c.id].add(e)
        campaign_meta[c.id] = c
        key = _norm_subject(e.subject)
        by_subject[key].add(e)
        subject_label.setdefault(key, e.subject)
        if e.status == "sent" and e.sent_at:
            by_day[e.sent_at.date().isoformat()]["sent"] += 1
        if e.replied_at:
            by_day[e.replied_at.date().isoformat()]["replied"] += 1

    campaigns = []
    for cid, counts in by_campaign.items():
        c = campaign_meta[cid]
        campaigns.append({
            "id": cid, "name": c.name, "subject": c.subject,
            "kind": _kind(c.name), "created_at": c.created_at,
            **counts.as_dict(),
        })
    campaigns.sort(key=lambda x: x["created_at"] or datetime.min, reverse=True)

    subjects = [
        {"subject": subject_label[k], **v.as_dict()}
        for k, v in by_subject.items() if v.sent > 0
    ]
    # Rank by reply rate, break ties by sample size.
    subjects.sort(key=lambda x: (x["reply_rate"], x["delivered"]), reverse=True)

    timeline = [
        {"date": d, **by_day[d]} for d in sorted(by_day)
    ]

    return {
        "days": days, "kind": kind,
        "totals": totals.as_dict(),
        "campaigns": campaigns,
        "subjects": subjects[:25],
        "timeline": timeline,
        "followups": _followup_effectiveness(db, rows),
    }


def _followup_effectiveness(db: Session, rows) -> dict:
    email_by_id = {e.id: e for e, _c in rows}
    seqs = db.query(FollowUpSequence).filter(
        FollowUpSequence.source_email_id.in_(list(email_by_id) or [0])
    ).all()

    sequences = len(seqs)
    with_followup = [s for s in seqs if (s.follow_ups_sent or 0) > 0]
    replied_before = replied_after = 0
    followups_sent = sum(s.follow_ups_sent or 0 for s in seqs)

    for s in seqs:
        e = email_by_id.get(s.source_email_id)
        if not e or not e.replied_at:
            continue
        if s.first_followup_at and e.replied_at >= s.first_followup_at:
            replied_after += 1
        else:
            replied_before += 1

    # Baseline: reply rate of delivered emails that had NO follow-up sequence.
    seq_email_ids = {s.source_email_id for s in seqs}
    base = _Counts()
    for e, _c in rows:
        if e.id not in seq_email_ids:
            base.add(e)
    base_d = base.as_dict()

    fu_counts = _Counts()
    for s in with_followup:
        e = email_by_id.get(s.source_email_id)
        if e:
            fu_counts.add(e)
    fu_d = fu_counts.as_dict()

    return {
        "sequences": sequences,
        "sequences_with_followup_sent": len(with_followup),
        "followups_sent": followups_sent,
        "replied_before_followup": replied_before,
        "replied_after_followup": replied_after,
        "followup_reply_rate": fu_d["reply_rate"],
        "no_followup_reply_rate": base_d["reply_rate"],
        "no_followup_delivered": base_d["delivered"],
    }


# ------------------------------------------------------------------ CSV

def _safe(value) -> str:
    """Neutralise spreadsheet formula injection in user-controlled text."""
    s = "" if value is None else str(value)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def campaigns_csv(db: Session, days: Optional[int], kind: str) -> str:
    data = build_overview(db, days, kind)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["campaign_id", "campaign", "type", "subject", "created_at",
                "total", "sent", "failed", "bounced", "delivered", "replied",
                "bounce_rate_pct", "reply_rate_pct"])
    for c in data["campaigns"]:
        w.writerow([c["id"], _safe(c["name"]), c["kind"], _safe(c["subject"]),
                    c["created_at"].isoformat() if c["created_at"] else "",
                    c["total"], c["sent"], c["failed"], c["bounced"],
                    c["delivered"], c["replied"], c["bounce_rate"], c["reply_rate"]])
    return out.getvalue()


def emails_csv(db: Session, days: Optional[int], kind: str) -> str:
    rows = _load(db, days, kind)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["email_id", "campaign", "recipient", "subject", "status",
                "sent_at", "replied", "replied_at", "bounced", "bounce_reason"])
    for e, c in sorted(rows, key=lambda r: r[0].created_at or datetime.min, reverse=True):
        w.writerow([e.id, _safe(c.name), _safe(e.recipient_email), _safe(e.subject),
                    e.status,
                    e.sent_at.isoformat() if e.sent_at else "",
                    "yes" if e.replied_at else "no",
                    e.replied_at.isoformat() if e.replied_at else "",
                    "yes" if e.bounced_at else "no",
                    _safe(e.bounce_reason)])
    return out.getvalue()
