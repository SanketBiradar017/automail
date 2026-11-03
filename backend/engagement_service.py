"""Email engagement analytics, computed from real email rows / evidence only.

Cohort = emails sent (or attempted, for failures) inside the selected window,
for the connected account only. For each email we look at its *current*
evidence (delivered_at / opened_at / replied_at / bounced_at), so an email is
counted at most once per metric regardless of how many events were logged.

Honesty rules
  * delivered / opened only count when there is evidence (see email_status);
    a reply counts as evidence of both.
  * Unknown is not confirmed: rates use None (not 0%) when the denominator is 0.
  * delivery rate = delivered / sent        (confirmed deliveries only)
  * open rate     = opened / sent emails that had opt-in tracking
  * reply rate    = replied / (sent - bounced)
"""

from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from backend.gmail_auth import get_connected_email
from backend.models import Email
from backend import email_status

MAX_DAYS = 366
METRICS = ("all", "sent", "delivered", "opened", "replied", "bounced", "failed")


class RangeError(ValueError):
    pass


def _tz(name: Optional[str]) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def resolve_window(range_key: str, start: Optional[str], end: Optional[str], tzname: Optional[str]):
    """-> (start_utc, end_utc, first_local_date, last_local_date, tz). Naive UTC bounds, end exclusive."""
    tz = _tz(tzname)
    today = datetime.now(tz).date()

    if range_key == "today":
        first = last = today
    elif range_key == "7d":
        first, last = today - timedelta(days=6), today
    elif range_key == "30d":
        first, last = today - timedelta(days=29), today
    elif range_key == "custom":
        try:
            first, last = date.fromisoformat(start or ""), date.fromisoformat(end or "")
        except ValueError:
            raise RangeError("Custom range needs valid start and end dates (YYYY-MM-DD).")
        if first > last:
            raise RangeError("Start date must be on or before the end date.")
        if (last - first).days + 1 > MAX_DAYS:
            raise RangeError(f"Custom range can span at most {MAX_DAYS} days.")
    else:
        raise RangeError("range must be today, 7d, 30d or custom.")

    to_utc = lambda d: datetime(d.year, d.month, d.day, tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
    return to_utc(first), to_utc(last + timedelta(days=1)), first, last, tz


def _scope(q, account: Optional[str]):
    # Only this account's emails (legacy rows with no owner are claimed by backfill).
    return q.filter(Email.sender_email == account) if account else q.filter(False)


def _ts(e) -> datetime:
    return e.sent_at or e.created_at


def _cohort(db: Session, start_utc, end_utc, account):
    when = func.coalesce(Email.sent_at, Email.created_at)
    q = db.query(Email).filter(Email.status.in_(("sent", "failed")), when >= start_utc, when < end_utc)
    return _scope(q, account).order_by(when.desc()).all()


def _rate(num: int, den: int) -> Optional[float]:
    return round(100.0 * num / den, 1) if den else None


def _is(metric: str, e: Email) -> bool:
    ok = e.status == "sent"
    if metric == "all":
        return True
    if metric == "failed":
        return e.status == "failed"
    if metric == "sent":
        return ok
    if metric == "bounced":
        return ok and bool(e.bounced_at)
    if not ok or e.bounced_at:
        return False
    return {"delivered": bool(e.delivered_at), "opened": bool(e.opened_at), "replied": bool(e.replied_at)}[metric]


def _iso(d):
    return d.isoformat() + "Z" if d else None


def _row(e: Email) -> dict:
    p = email_status.status_payload(e)
    return {
        "id": e.id, "recipient": e.recipient_email, "name": e.recipient_name, "subject": e.subject,
        "sent_at": _iso(e.sent_at or e.created_at), "state": p["state"], "label": p["label"], "tick": p["tick"],
        "tip": p["tip"], "delivered_at": p["delivered_at"], "delivered_source": p["delivered_source"],
        "opened_at": p["opened_at"], "opened_source": p["opened_source"], "replied_at": p["replied_at"],
        "bounced_at": p["bounced_at"], "open_tracking": p["open_tracking"], "error": p["error"],
    }


def _daterange(first: date, last: date):
    d = first
    while d <= last:
        yield d
        d += timedelta(days=1)


def build_engagement(db: Session, range_key="30d", start=None, end=None, tzname=None) -> dict:
    start_utc, end_utc, first, last, tz = resolve_window(range_key, start, end, tzname)
    account = get_connected_email()
    rows = _cohort(db, start_utc, end_utc, account)

    sent = [e for e in rows if e.status == "sent"]
    failed = [e for e in rows if e.status == "failed"]
    bounced = [e for e in sent if e.bounced_at]
    live = [e for e in sent if not e.bounced_at]
    delivered = [e for e in live if e.delivered_at]
    opened = [e for e in live if e.opened_at]
    replied = [e for e in live if e.replied_at]
    tracked = [e for e in sent if e.open_tracking]
    tracked_opened = [e for e in tracked if e.opened_at and not e.bounced_at]

    counts = OrderedDict(sent=len(sent), delivered=len(delivered), opened=len(opened),
                         replied=len(replied), bounced=len(bounced), failed=len(failed))

    # exclusive breakdown (each email exactly once, by its current status)
    breakdown = OrderedDict((k, 0) for k in ("sent", "delivered", "opened", "replied", "bounced", "failed"))
    for e in rows:
        s = email_status.derive_status(e)
        if s in breakdown:
            breakdown[s] += 1

    # trends: event time bucketed in the user's timezone
    day_of = lambda dt: dt.replace(tzinfo=timezone.utc).astimezone(tz).date()
    days = {d: {"sent": 0, "delivered": 0, "opened": 0, "replied": 0} for d in _daterange(first, last)}
    for e in sent:
        d = day_of(_ts(e))
        if d in days:
            days[d]["sent"] += 1
    for key, group, attr in (("delivered", delivered, "delivered_at"), ("opened", opened, "opened_at"),
                             ("replied", replied, "replied_at")):
        for e in group:
            d = day_of(getattr(e, attr))
            if d in days:            # events after the window end still belong to this cohort; show them on their day if visible
                days[d][key] += 1
    daily = [{"date": d.isoformat(), **v} for d, v in days.items()]

    weeks: Dict[date, dict] = OrderedDict()
    for d, v in days.items():
        monday = d - timedelta(days=d.weekday())
        w = weeks.setdefault(monday, {"sent": 0, "delivered": 0, "opened": 0, "replied": 0})
        for k in w:
            w[k] += v[k]
    weekly = [{"date": d.isoformat(), **v} for d, v in weeks.items()]

    stamp = _scope(db.query(func.max(Email.status_updated_at)), account).scalar()

    return {
        "success": True,
        "range": {"key": range_key, "start": first.isoformat(), "end": last.isoformat(),
                  "tz": str(tz), "start_utc": _iso(start_utc), "end_utc": _iso(end_utc)},
        "counts": counts,
        "rates": {
            "delivery_rate": _rate(len(delivered), len(sent)),
            "open_rate": _rate(len(tracked_opened), len(tracked)),
            "reply_rate": _rate(len(replied), len(live)),
            "open_rate_base": len(tracked),
            "reply_rate_base": len(live),
        },
        "funnel": [
            {"key": "sent", "label": "Sent", "count": len(sent)},
            {"key": "delivered", "label": "Delivered", "count": len(delivered)},
            {"key": "opened", "label": "Opened", "count": len(opened)},
            {"key": "replied", "label": "Replied", "count": len(replied)},
        ],
        "breakdown": breakdown,
        "daily": daily,
        "weekly": weekly,
        "recent": [_row(e) for e in rows[:25]],
        "as_of": _iso(stamp),
    }


def list_metric_emails(db: Session, metric: str, range_key, start, end, tzname, page=1, limit=25) -> dict:
    if metric not in METRICS:
        raise RangeError(f"metric must be one of {', '.join(METRICS)}.")
    start_utc, end_utc, *_ = resolve_window(range_key, start, end, tzname)
    rows = [e for e in _cohort(db, start_utc, end_utc, get_connected_email()) if _is(metric, e)]
    page = max(1, page)
    sl = rows[(page - 1) * limit: page * limit]
    return {"success": True, "metric": metric, "total": len(rows), "page": page, "limit": limit,
            "emails": [_row(e) for e in sl]}


def stamp(db: Session) -> dict:
    """Cheap change detector for auto-refresh: moves whenever a status event is recorded."""
    account = get_connected_email()
    s = _scope(db.query(func.max(Email.status_updated_at)), account).scalar()
    n = _scope(db.query(func.count(Email.id)), account).scalar()
    return {"as_of": _iso(s), "emails": n}
