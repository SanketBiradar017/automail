"""Admin API (part 1): auth, overview, users, email activity, Gmail connections, AI usage.

Privacy rules enforced here (not just in the UI):
  * no email subject/body, no Gmail/OAuth tokens, no API keys are ever selected
  * recipient addresses are masked (a***@domain); customer account emails are shown
  * free-text errors pass through telemetry.redact
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import Session

from backend import config, email_status
from backend.admin import security as sec
from backend.admin.common import RANGE, Win, iso, like, paginate
from backend.admin.models import (AIUsageLog, AdminUser, AppUser, AuditLog, Payment, Plan, Subscription)
from backend.admin.telemetry import audit, mask_email, redact
from backend.database import get_db
from backend.models import Email, EmailCampaign, GmailAccount, ScheduledEmail

auth_router = APIRouter(prefix="/api/admin/auth", tags=["Admin Auth"])
router = APIRouter(prefix="/api/admin", tags=["Admin"], dependencies=[Depends(sec.current_admin)])


def win(range: str = Query("30d", pattern=RANGE), start: Optional[str] = Query(None, max_length=10),
        end: Optional[str] = Query(None, max_length=10), tz: str = Query("UTC", max_length=64)) -> Win:
    return Win(range, start, end, tz)


def admin_dict(a: AdminUser):
    return {"id": a.id, "email": a.email, "name": a.name, "role": a.role, "is_active": a.is_active,
            "last_login_at": iso(a.last_login_at), "created_at": iso(a.created_at)}


# ----------------------------------------------------------------------- auth

class LoginIn(BaseModel):
    email: str = Field(max_length=255)
    password: str = Field(max_length=200)


@auth_router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = sec.client_ip(request)
    admin = sec.authenticate(db, body.email, body.password, ip)
    admin.last_login_at = datetime.utcnow()
    audit(db, admin, "admin.login", "admin", admin.id, ip=ip)
    db.commit()
    sec.set_session_cookie(response, request, admin)
    return {"success": True, "admin": admin_dict(admin)}


@auth_router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    response.delete_cookie(sec.COOKIE, path="/api/admin", httponly=True, samesite="strict")
    return {"success": True}


@auth_router.get("/me")
def me(admin: AdminUser = Depends(sec.current_admin)):
    return {"success": True, "admin": admin_dict(admin)}


# ------------------------------------------------------------------- overview

GEN_FEATURES = ("generate_email", "generate_followup", "reply_draft", "template_generate")


def _email_ts():
    return func.coalesce(Email.sent_at, Email.created_at)


@router.get("/overview")
def overview(w: Win = Depends(win), db: Session = Depends(get_db)):
    ts = _email_ts()
    in_win = and_(ts >= w.start_utc, ts < w.end_utc)

    sent_ts = [r[0] for r in db.query(ts).filter(Email.status == "sent", in_win).all()]
    failed_ts = [r[0] for r in db.query(ts).filter(Email.status == "failed", in_win).all()]
    gen_ts = [r[0] for r in db.query(AIUsageLog.created_at).filter(
        AIUsageLog.feature.in_(GEN_FEATURES), AIUsageLog.success.is_(True),
        AIUsageLog.created_at >= w.start_utc, AIUsageLog.created_at < w.end_utc).all()]
    new_users_ts = [r[0] for r in db.query(AppUser.created_at).filter(
        AppUser.created_at >= w.start_utc, AppUser.created_at < w.end_utc).all()]

    pay = db.query(Payment.created_at, Payment.amount_cents, Payment.currency).filter(
        Payment.status == "succeeded", Payment.created_at >= w.start_utc, Payment.created_at < w.end_utc).all()
    revenue = {}
    for _, cents, cur in pay:
        revenue[cur] = revenue.get(cur, 0) + cents
    main_cur = max(revenue, key=revenue.get) if revenue else "USD"
    rev_by_day = {}
    for at, cents, cur in pay:
        if cur == main_cur:
            d = at.replace(tzinfo=timezone.utc).astimezone(w.tz).date()
            rev_by_day[d] = rev_by_day.get(d, 0) + cents

    active_senders = {r[0] for r in db.query(Email.sender_email).filter(
        Email.sender_email.isnot(None), Email.status == "sent", in_win).distinct().all()}
    active_login = {r[0] for r in db.query(AppUser.email).filter(
        AppUser.last_login_at >= w.start_utc, AppUser.last_login_at < w.end_utc).all()}

    subs_active = db.query(func.count(Subscription.id)).filter(Subscription.status == "active").scalar()
    mrr = {}
    for price, interval, cur in db.query(Plan.price_cents, Plan.interval, Plan.currency).join(
            Subscription, Subscription.plan_id == Plan.id).filter(Subscription.status == "active").all():
        mrr[cur] = mrr.get(cur, 0) + (price if interval == "month" else round(price / 12))

    counts = {
        "users_total": db.query(func.count(AppUser.id)).scalar(),
        "users_new": len(new_users_ts),
        "users_active": len(active_senders | active_login),
        "users_suspended": db.query(func.count(AppUser.id)).filter(AppUser.status == "suspended").scalar(),
        "emails_generated": len(gen_ts),
        "emails_sent": len(sent_ts),
        "emails_failed": len(failed_ts),
        "emails_scheduled": db.query(func.count(ScheduledEmail.id)).filter(ScheduledEmail.status == "scheduled").scalar(),
        "emails_pending": (db.query(func.count(Email.id)).filter(Email.status == "pending").scalar()
                           + db.query(func.count(ScheduledEmail.id)).filter(
                               ScheduledEmail.status.in_(("pending", "processing"))).scalar()),
        "subscriptions_active": subs_active,
        "subscriptions_canceled": db.query(func.count(Subscription.id)).filter(
            Subscription.canceled_at >= w.start_utc, Subscription.canceled_at < w.end_utc).scalar(),
        "gmail_connected": db.query(func.count(GmailAccount.id)).scalar(),
    }
    return {"success": True, "range": w.meta(), "counts": counts,
            "revenue": revenue, "mrr": mrr, "revenue_currency": main_cur,
            "series": w.series(sent=w.bucket(sent_ts), failed=w.bucket(failed_ts), generated=w.bucket(gen_ts),
                               new_users=w.bucket(new_users_ts), revenue_cents=rev_by_day),
            "note": "Emails generated is counted from AI usage logs, which start when this panel was deployed."}


# ---------------------------------------------------------------------- users

def _sub_counts(db, emails):
    if not emails:
        return {}
    rows = db.query(Email.sender_email, Email.status, func.count(Email.id), func.max(_email_ts())).filter(
        Email.sender_email.in_(emails)).group_by(Email.sender_email, Email.status).all()
    out = {e: {"sent": 0, "failed": 0, "last_sent_at": None} for e in emails}
    for e, st, n, last in rows:
        if st in ("sent", "failed"):
            out[e][st] += n
        if st == "sent" and last and (out[e]["last_sent_at"] is None or last > out[e]["last_sent_at"]):
            out[e]["last_sent_at"] = last
    return out


@router.get("/users")
def users(search: str = Query("", max_length=100), status: str = Query("", pattern="^(|active|suspended)$"),
          gmail: str = Query("", pattern="^(|connected|disconnected)$"),
          sort: str = Query("created", pattern="^(created|last_login|emails|email)$"),
          direction: str = Query("desc", pattern="^(asc|desc)$"),
          page: int = Query(1, ge=1, le=100000), page_size: int = Query(25, ge=1, le=100),
          db: Session = Depends(get_db)):
    q = db.query(AppUser)
    if search.strip():
        q = q.filter(or_(AppUser.email.ilike(like(search.strip()), escape="\\"), AppUser.name.ilike(like(search.strip()), escape="\\")))
    if status:
        q = q.filter(AppUser.status == status)
    connected_emails = db.query(GmailAccount.email)
    if gmail == "connected":
        q = q.filter(AppUser.email.in_(connected_emails))
    elif gmail == "disconnected":
        q = q.filter(~AppUser.email.in_(connected_emails))
    total = q.count()
    if sort == "emails":
        agg = db.query(Email.sender_email.label("e"), func.count(Email.id).label("n")).group_by(Email.sender_email).subquery()
        q = q.outerjoin(agg, agg.c.e == AppUser.email)
        col = func.coalesce(agg.c.n, 0)
    else:
        col = {"created": AppUser.created_at, "last_login": AppUser.last_login_at, "email": AppUser.email}[sort]
    q = q.order_by(col.asc() if direction == "asc" else col.desc(), AppUser.id.desc())
    rows, meta = paginate(q, page, page_size, total)
    counts = _sub_counts(db, [u.email for u in rows])
    connected = {e for (e,) in db.query(GmailAccount.email).filter(GmailAccount.email.in_([u.email for u in rows] or [""])).all()}
    return {"success": True, **meta, "users": [{
        "id": u.id, "email": u.email, "name": u.name, "status": u.status, "created_at": iso(u.created_at),
        "last_login_at": iso(u.last_login_at), "gmail_connected": u.email in connected,
        "emails_sent": counts[u.email]["sent"], "emails_failed": counts[u.email]["failed"]} for u in rows]}


def _user_or_404(db, uid):
    u = db.query(AppUser).filter(AppUser.id == uid).first()
    if not u:
        raise HTTPException(404, "User not found")
    return u


def gmail_state(acc: Optional[GmailAccount]) -> str:
    if not acc:
        return "disconnected"
    expired = acc.token_expiry is not None and acc.token_expiry < datetime.utcnow()
    if not acc.refresh_token_enc and expired:
        return "reauth_required"
    return "connected"


@router.get("/users/{uid}")
def user_detail(uid: int, db: Session = Depends(get_db)):
    u = _user_or_404(db, uid)
    acc = db.query(GmailAccount).filter(GmailAccount.email == u.email).first()
    c = _sub_counts(db, [u.email])[u.email]
    st = dict(db.query(Email.status, func.count(Email.id)).filter(Email.sender_email == u.email).group_by(Email.status).all())
    replied = db.query(func.count(Email.id)).filter(Email.sender_email == u.email, Email.replied_at.isnot(None)).scalar()
    bounced = db.query(func.count(Email.id)).filter(Email.sender_email == u.email, Email.bounced_at.isnot(None)).scalar()
    ai = db.query(func.count(AIUsageLog.id), func.coalesce(func.sum(AIUsageLog.input_tokens), 0),
                  func.coalesce(func.sum(AIUsageLog.output_tokens), 0)).filter(AIUsageLog.user_email == u.email).one()
    sub = (db.query(Subscription, Plan).join(Plan, Plan.id == Subscription.plan_id)
           .filter(Subscription.user_id == u.id).order_by(Subscription.started_at.desc()).first())
    pays = db.query(Payment).filter(Payment.user_id == u.id).order_by(Payment.created_at.desc()).limit(10).all()
    recent = (db.query(Email, EmailCampaign.name).join(EmailCampaign, EmailCampaign.id == Email.campaign_id)
              .filter(Email.sender_email == u.email).order_by(_email_ts().desc()).limit(10).all())
    log = db.query(AuditLog).filter(AuditLog.target_type == "user", AuditLog.target_id == str(u.id)).order_by(AuditLog.id.desc()).limit(10).all()
    return {"success": True, "user": {
        "id": u.id, "email": u.email, "name": u.name, "status": u.status, "suspended_at": iso(u.suspended_at),
        "suspended_reason": u.suspended_reason, "created_at": iso(u.created_at), "last_login_at": iso(u.last_login_at),
        "login_count": u.login_count, "gmail_disconnected_at": iso(u.gmail_disconnected_at)},
        "gmail": {"state": gmail_state(acc), "connected_at": iso(acc.connected_at) if acc else None,
                  "token_expiry": iso(acc.token_expiry) if acc else None,
                  "has_refresh_token": bool(acc and acc.refresh_token_enc),
                  "scopes": [s.rsplit("/", 1)[-1] for s in (acc.scopes or "").split()] if acc else []},
        "emails": {"sent": c["sent"], "failed": c["failed"], "pending": st.get("pending", 0), "replied": replied,
                   "bounced": bounced, "last_sent_at": iso(c["last_sent_at"])},
        "ai": {"requests": ai[0], "input_tokens": int(ai[1]), "output_tokens": int(ai[2])},
        "subscription": ({"id": sub[0].id, "plan": sub[1].name, "status": sub[0].status, "started_at": iso(sub[0].started_at),
                          "current_period_end": iso(sub[0].current_period_end)} if sub else None),
        "payments": [{"id": p.id, "amount_cents": p.amount_cents, "currency": p.currency, "status": p.status,
                      "created_at": iso(p.created_at)} for p in pays],
        "recent_emails": [{"id": e.id, "kind": _kind(name), "status": email_status.derive_status(e),
                           "at": iso(e.sent_at or e.created_at), "recipient": mask_email(e.recipient_email)} for e, name in recent],
        "audit": [{"action": a.action, "admin": a.admin_email, "at": iso(a.created_at), "detail": a.detail} for a in log]}


class ReasonIn(BaseModel):
    reason: str = Field("", max_length=300)


@router.post("/users/{uid}/suspend")
def suspend_user(uid: int, body: ReasonIn, request: Request, admin: AdminUser = Depends(sec.require("admin")),
                 db: Session = Depends(get_db)):
    u = _user_or_404(db, uid)
    u.status, u.suspended_at, u.suspended_reason = "suspended", datetime.utcnow(), body.reason or None
    audit(db, admin, "user.suspend", "user", u.id, body.reason, sec.client_ip(request))
    db.commit()
    return {"success": True}


@router.post("/users/{uid}/reactivate")
def reactivate_user(uid: int, request: Request, admin: AdminUser = Depends(sec.require("admin")), db: Session = Depends(get_db)):
    u = _user_or_404(db, uid)
    u.status, u.suspended_at, u.suspended_reason = "active", None, None
    audit(db, admin, "user.reactivate", "user", u.id, ip=sec.client_ip(request))
    db.commit()
    return {"success": True}


@router.post("/users/{uid}/disconnect-gmail")
def disconnect_user_gmail(uid: int, request: Request, admin: AdminUser = Depends(sec.require("admin")), db: Session = Depends(get_db)):
    """Removes the stored Gmail credentials (the user must sign in again). Tokens are deleted, never shown."""
    u = _user_or_404(db, uid)
    n = db.query(GmailAccount).filter(GmailAccount.email == u.email).delete()
    u.gmail_disconnected_at = datetime.utcnow()
    audit(db, admin, "user.disconnect_gmail", "user", u.id, f"removed={n}", sec.client_ip(request))
    db.commit()
    return {"success": True, "removed": n}


# -------------------------------------------------------------- email activity

def _kind(name: Optional[str]) -> str:
    name = name or ""
    return "single" if name.startswith("Single:") else "scheduled" if name.startswith("Scheduled:") else "bulk"


@router.get("/emails")
def emails(source: str = Query("sent", pattern="^(sent|scheduled)$"), status: str = Query("", max_length=20),
           search: str = Query("", max_length=100), w: Win = Depends(win),
           page: int = Query(1, ge=1, le=100000), page_size: int = Query(25, ge=1, le=100), db: Session = Depends(get_db)):
    ts = _email_ts()
    in_win = and_(ts >= w.start_utc, ts < w.end_utc)
    sched_win = and_(func.coalesce(ScheduledEmail.scheduled_at_utc, ScheduledEmail.created_at) >= w.start_utc,
                     func.coalesce(ScheduledEmail.scheduled_at_utc, ScheduledEmail.created_at) < w.end_utc)
    summary = {
        "sent": db.query(func.count(Email.id)).filter(Email.status == "sent", in_win).scalar(),
        "failed": db.query(func.count(Email.id)).filter(Email.status == "failed", in_win).scalar(),
        "scheduled": db.query(func.count(ScheduledEmail.id)).filter(ScheduledEmail.status == "scheduled").scalar(),
        "pending": (db.query(func.count(Email.id)).filter(Email.status == "pending").scalar()
                    + db.query(func.count(ScheduledEmail.id)).filter(ScheduledEmail.status.in_(("pending", "processing"))).scalar()),
    }
    s = search.strip()
    if source == "sent":
        q = (db.query(Email, EmailCampaign.name).join(EmailCampaign, EmailCampaign.id == Email.campaign_id).filter(in_win))
        if status in ("sent", "failed", "pending"):
            q = q.filter(Email.status == status)
        elif status == "replied":
            q = q.filter(Email.status == "sent", Email.replied_at.isnot(None))
        elif status == "bounced":
            q = q.filter(Email.bounced_at.isnot(None))
        if s:
            q = q.filter(or_(Email.sender_email.ilike(like(s), escape="\\"), Email.recipient_email.ilike(like(s), escape="\\")))
        rows, meta = paginate(q.order_by(ts.desc(), Email.id.desc()), page, page_size)
        items = [{"id": e.id, "user": e.sender_email, "recipient": mask_email(e.recipient_email), "kind": _kind(name),
                  "status": email_status.derive_status(e), "at": iso(e.sent_at or e.created_at),
                  "attachments": len(__import__("json").loads(e.attachment_names)) if e.attachment_names else 0,
                  "error": redact(e.error_message, 300) if e.status == "failed" else None} for e, name in rows]
    else:
        q = db.query(ScheduledEmail).filter(sched_win)
        if status:
            q = q.filter(ScheduledEmail.status == status)
        if s:
            q = q.filter(ScheduledEmail.recipient_email.ilike(like(s), escape="\\"))
        rows, meta = paginate(q.order_by(ScheduledEmail.scheduled_at_utc.desc().nullslast(), ScheduledEmail.id.desc()), page, page_size)
        items = [{"id": r.id, "user": None, "recipient": mask_email(r.recipient_email),
                  "kind": "recurring" if r.is_recurring else "scheduled", "status": r.status,
                  "at": iso(r.scheduled_at_utc), "timezone": r.timezone, "send_count": r.send_count,
                  "error": redact(r.last_error, 300) if r.last_error else None} for r in rows]
    return {"success": True, "range": w.meta(), "summary": summary, "source": source, **meta, "items": items}


# ------------------------------------------------------------ gmail connections

@router.get("/gmail")
def gmail_connections(db: Session = Depends(get_db)):
    accounts = {a.email: a for a in db.query(GmailAccount).all()}
    users_ = db.query(AppUser).order_by(AppUser.last_login_at.desc().nullslast()).limit(500).all()
    rows, seen = [], set()
    for u in users_:
        acc = accounts.get(u.email)
        seen.add(u.email)
        rows.append(_gm_row(u.email, u, acc))
    for email, acc in accounts.items():          # connected accounts with no AppUser row yet
        if email not in seen:
            rows.append(_gm_row(email, None, acc))
    states = [r["state"] for r in rows]
    return {"success": True,
            "summary": {"connected": states.count("connected"), "reauth_required": states.count("reauth_required"),
                        "disconnected": states.count("disconnected"), "total": len(rows)},
            "connections": rows}


def _gm_row(email, user, acc):
    return {"user_id": user.id if user else None, "email": email, "state": gmail_state(acc),
            "connected_at": iso(acc.connected_at) if acc else None,
            "token_expiry": iso(acc.token_expiry) if acc else None,
            "has_refresh_token": bool(acc and acc.refresh_token_enc),
            "scopes": [s.rsplit("/", 1)[-1] for s in (acc.scopes or "").split()] if acc else [],
            "disconnected_at": iso(user.gmail_disconnected_at) if user else None,
            "suspended": bool(user and user.status == "suspended")}


# ------------------------------------------------------------------- AI usage

def _cost(i: int, o: int) -> float:
    return round(i / 1e6 * config.AI_PRICE_INPUT_PER_M + o / 1e6 * config.AI_PRICE_OUTPUT_PER_M, 4)


@router.get("/ai")
def ai_usage(w: Win = Depends(win), db: Session = Depends(get_db)):
    base = and_(AIUsageLog.created_at >= w.start_utc, AIUsageLog.created_at < w.end_utc)
    t = db.query(func.count(AIUsageLog.id), func.coalesce(func.sum(AIUsageLog.input_tokens), 0),
                 func.coalesce(func.sum(AIUsageLog.output_tokens), 0), func.coalesce(func.avg(AIUsageLog.latency_ms), 0),
                 func.coalesce(func.sum(case((AIUsageLog.success.is_(True), 1), else_=0)), 0)).filter(base).one()
    req, i_tok, o_tok, lat, ok = int(t[0]), int(t[1]), int(t[2]), float(t[3]), int(t[4])
    by_feature = [{"feature": f, "requests": n, "input_tokens": int(a), "output_tokens": int(b), "errors": int(e),
                   "cost": _cost(int(a), int(b))}
                  for f, n, a, b, e in db.query(AIUsageLog.feature, func.count(AIUsageLog.id), func.sum(AIUsageLog.input_tokens),
                                                func.sum(AIUsageLog.output_tokens),
                                                func.sum(case((AIUsageLog.success.is_(False), 1), else_=0)))
                  .filter(base).group_by(AIUsageLog.feature).order_by(func.count(AIUsageLog.id).desc()).all()]
    by_model = [{"model": m or "unknown", "requests": n} for m, n in db.query(AIUsageLog.model, func.count(AIUsageLog.id))
                .filter(base).group_by(AIUsageLog.model).all()]
    rows = db.query(AIUsageLog.created_at, AIUsageLog.input_tokens, AIUsageLog.output_tokens, AIUsageLog.success).filter(base).all()
    from collections import Counter
    rq, it, ot, er = Counter(), Counter(), Counter(), Counter()
    for at, a, b, s in rows:
        d = at.replace(tzinfo=timezone.utc).astimezone(w.tz).date()
        rq[d] += 1; it[d] += a or 0; ot[d] += b or 0
        if not s:
            er[d] += 1
    series = w.series(requests=rq, input_tokens=it, output_tokens=ot, errors=er)
    for r in series:
        r["cost"] = _cost(r["input_tokens"], r["output_tokens"])
    errors = [{"at": iso(e.created_at), "feature": e.feature, "error": e.error}
              for e in db.query(AIUsageLog).filter(base, AIUsageLog.success.is_(False)).order_by(AIUsageLog.id.desc()).limit(10).all()]
    return {"success": True, "range": w.meta(),
            "totals": {"requests": req, "success": ok, "errors": req - ok, "input_tokens": i_tok, "output_tokens": o_tok,
                       "avg_latency_ms": round(lat), "estimated_cost": _cost(i_tok, o_tok),
                       "error_rate": round(100 * (req - ok) / req, 1) if req else None},
            "pricing": {"input_per_million": config.AI_PRICE_INPUT_PER_M, "output_per_million": config.AI_PRICE_OUTPUT_PER_M,
                        "currency": "USD", "note": "Estimate only. Set AI_PRICE_INPUT_PER_M / AI_PRICE_OUTPUT_PER_M in .env to your model's real rates."},
            "by_feature": by_feature, "by_model": by_model, "series": series, "recent_errors": errors}
