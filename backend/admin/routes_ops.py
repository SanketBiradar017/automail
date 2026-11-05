"""Admin API (part 2): billing, follow-ups, system health, logs, notifications, admin accounts."""
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, func, or_, text
from sqlalchemy.orm import Session

from backend.admin import security as sec
from backend.admin.common import RANGE, Win, iso, like, paginate
from backend.admin.models import (AdminNotification, AdminUser, AppUser, AuditLog, ErrorLog, AIUsageLog,
                                  Payment, Plan, Subscription)
from backend.admin.routes_core import admin_dict, win
from backend.admin.telemetry import audit, mask_email, redact
from backend.database import DATABASE_URL, engine, get_db
from backend.models import Email, FollowUpSequence, GmailAccount

router = APIRouter(prefix="/api/admin", tags=["Admin"], dependencies=[Depends(sec.current_admin)])
STARTED = time.time()
ADMIN = Depends(sec.require("admin"))
SUPER = Depends(sec.require("super_admin"))


# -------------------------------------------------------------------- billing

class PlanIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    price_cents: int = Field(ge=0, le=100_000_000)
    currency: str = Field("USD", min_length=3, max_length=3)
    interval: str = Field("month", pattern="^(month|year)$")
    email_limit: Optional[int] = Field(None, ge=0)
    features: str = Field("", max_length=1000)
    is_active: bool = True


def _plan(p: Plan, subs: int = 0):
    return {"id": p.id, "name": p.name, "price_cents": p.price_cents, "currency": p.currency, "interval": p.interval,
            "email_limit": p.email_limit, "features": p.features or "", "is_active": p.is_active, "subscribers": subs}


@router.get("/plans")
def plans(db: Session = Depends(get_db)):
    counts = dict(db.query(Subscription.plan_id, func.count(Subscription.id)).filter(Subscription.status == "active").group_by(Subscription.plan_id).all())
    return {"success": True, "plans": [_plan(p, counts.get(p.id, 0)) for p in db.query(Plan).order_by(Plan.price_cents).all()]}


@router.post("/plans")
def create_plan(body: PlanIn, request: Request, admin: AdminUser = ADMIN, db: Session = Depends(get_db)):
    if db.query(Plan.id).filter(func.lower(Plan.name) == body.name.strip().lower()).first():
        raise HTTPException(409, "A plan with this name already exists.")
    p = Plan(**{**body.model_dump(), "name": body.name.strip(), "currency": body.currency.upper()})
    db.add(p); db.flush()
    audit(db, admin, "plan.create", "plan", p.id, f"{p.name} {p.price_cents}{p.currency}/{p.interval}", sec.client_ip(request))
    db.commit()
    return {"success": True, "plan": _plan(p)}


@router.put("/plans/{pid}")
def update_plan(pid: int, body: PlanIn, request: Request, admin: AdminUser = ADMIN, db: Session = Depends(get_db)):
    p = db.query(Plan).filter(Plan.id == pid).first()
    if not p:
        raise HTTPException(404, "Plan not found")
    dup = db.query(Plan.id).filter(func.lower(Plan.name) == body.name.strip().lower(), Plan.id != pid).first()
    if dup:
        raise HTTPException(409, "A plan with this name already exists.")
    before = f"{p.name} {p.price_cents}{p.currency}/{p.interval} active={p.is_active}"
    for k, v in body.model_dump().items():
        setattr(p, k, v.strip() if k == "name" else v.upper() if k == "currency" else v)
    audit(db, admin, "plan.update", "plan", p.id, f"{before} -> {p.name} {p.price_cents}{p.currency}/{p.interval} active={p.is_active}", sec.client_ip(request))
    db.commit()
    return {"success": True, "plan": _plan(p)}


@router.delete("/plans/{pid}")
def delete_plan(pid: int, request: Request, admin: AdminUser = SUPER, db: Session = Depends(get_db)):
    p = db.query(Plan).filter(Plan.id == pid).first()
    if not p:
        raise HTTPException(404, "Plan not found")
    if db.query(Subscription.id).filter(Subscription.plan_id == pid).first():
        raise HTTPException(409, "Plan has subscriptions. Deactivate it instead.")
    audit(db, admin, "plan.delete", "plan", pid, p.name, sec.client_ip(request))
    db.delete(p); db.commit()
    return {"success": True}


@router.get("/subscriptions")
def subscriptions(status: str = Query("", pattern="^(|active|canceled|past_due)$"), search: str = Query("", max_length=100),
                  page: int = Query(1, ge=1, le=100000), page_size: int = Query(25, ge=1, le=100), db: Session = Depends(get_db)):
    q = db.query(Subscription, AppUser, Plan).join(AppUser, AppUser.id == Subscription.user_id).join(Plan, Plan.id == Subscription.plan_id)
    if status:
        q = q.filter(Subscription.status == status)
    if search.strip():
        q = q.filter(AppUser.email.ilike(like(search.strip()), escape="\\"))
    rows, meta = paginate(q.order_by(Subscription.started_at.desc()), page, page_size)
    return {"success": True, **meta, "subscriptions": [{
        "id": s.id, "user_id": u.id, "user": u.email, "plan": p.name, "price_cents": p.price_cents, "currency": p.currency,
        "interval": p.interval, "status": s.status, "started_at": iso(s.started_at), "current_period_end": iso(s.current_period_end),
        "canceled_at": iso(s.canceled_at), "cancel_reason": s.cancel_reason} for s, u, p in rows]}


class SubIn(BaseModel):
    user_id: int
    plan_id: int


@router.post("/subscriptions")
def create_subscription(body: SubIn, request: Request, admin: AdminUser = ADMIN, db: Session = Depends(get_db)):
    u = db.query(AppUser).filter(AppUser.id == body.user_id).first()
    p = db.query(Plan).filter(Plan.id == body.plan_id).first()
    if not u or not p:
        raise HTTPException(404, "User or plan not found")
    if not p.is_active:
        raise HTTPException(409, "Plan is not active.")
    if db.query(Subscription.id).filter(Subscription.user_id == u.id, Subscription.status == "active").first():
        raise HTTPException(409, "User already has an active subscription. Cancel it first.")
    now = datetime.utcnow()
    s = Subscription(user_id=u.id, plan_id=p.id, started_at=now,
                     current_period_end=now + timedelta(days=365 if p.interval == "year" else 30))
    db.add(s); db.flush()
    audit(db, admin, "subscription.create", "subscription", s.id, f"user={u.id} plan={p.name}", sec.client_ip(request))
    db.commit()
    return {"success": True, "id": s.id}


class CancelIn(BaseModel):
    reason: str = Field("", max_length=300)


@router.post("/subscriptions/{sid}/cancel")
def cancel_subscription(sid: int, body: CancelIn, request: Request, admin: AdminUser = ADMIN, db: Session = Depends(get_db)):
    s = db.query(Subscription).filter(Subscription.id == sid).first()
    if not s:
        raise HTTPException(404, "Subscription not found")
    if s.status == "canceled":
        raise HTTPException(409, "Already canceled.")
    s.status, s.canceled_at, s.cancel_reason = "canceled", datetime.utcnow(), body.reason or None
    audit(db, admin, "subscription.cancel", "subscription", s.id, body.reason, sec.client_ip(request))
    db.commit()
    return {"success": True}


@router.get("/payments")
def payments(status: str = Query("", pattern="^(|succeeded|failed|refunded)$"), w: Win = Depends(win),
             page: int = Query(1, ge=1, le=100000), page_size: int = Query(25, ge=1, le=100), db: Session = Depends(get_db)):
    inw = and_(Payment.created_at >= w.start_utc, Payment.created_at < w.end_utc)
    q = db.query(Payment, AppUser).join(AppUser, AppUser.id == Payment.user_id).filter(inw)
    if status:
        q = q.filter(Payment.status == status)
    rows, meta = paginate(q.order_by(Payment.created_at.desc()), page, page_size)
    rev = {}
    for cur, cents in db.query(Payment.currency, func.sum(Payment.amount_cents)).filter(inw, Payment.status == "succeeded").group_by(Payment.currency).all():
        rev[cur] = int(cents)
    return {"success": True, "range": w.meta(), "revenue": rev, **meta, "payments": [{
        "id": p.id, "user_id": u.id, "user": u.email, "amount_cents": p.amount_cents, "currency": p.currency, "status": p.status,
        "method": p.method, "reference": p.reference, "note": p.note, "subscription_id": p.subscription_id,
        "created_at": iso(p.created_at)} for p, u in rows]}


class PayIn(BaseModel):
    user_id: int
    amount_cents: int = Field(gt=0, le=100_000_000)
    currency: str = Field("USD", min_length=3, max_length=3)
    status: str = Field("succeeded", pattern="^(succeeded|failed)$")
    method: str = Field("manual", max_length=30)
    reference: str = Field("", max_length=120)
    note: str = Field("", max_length=300)
    subscription_id: Optional[int] = None


@router.post("/payments")
def record_payment(body: PayIn, request: Request, admin: AdminUser = ADMIN, db: Session = Depends(get_db)):
    if not db.query(AppUser.id).filter(AppUser.id == body.user_id).first():
        raise HTTPException(404, "User not found")
    p = Payment(**{**body.model_dump(), "currency": body.currency.upper()})
    db.add(p); db.flush()
    audit(db, admin, "payment.record", "payment", p.id, f"user={p.user_id} {p.amount_cents}{p.currency} {p.status}", sec.client_ip(request))
    db.commit()
    return {"success": True, "id": p.id}


@router.post("/payments/{pid}/refund")
def refund_payment(pid: int, request: Request, admin: AdminUser = ADMIN, db: Session = Depends(get_db)):
    p = db.query(Payment).filter(Payment.id == pid).first()
    if not p:
        raise HTTPException(404, "Payment not found")
    if p.status != "succeeded":
        raise HTTPException(409, "Only succeeded payments can be refunded.")
    p.status = "refunded"
    audit(db, admin, "payment.refund", "payment", p.id, f"{p.amount_cents}{p.currency}", sec.client_ip(request))
    db.commit()
    return {"success": True}


# ------------------------------------------------------------------ follow-ups

@router.get("/followups")
def followups(status: str = Query("", max_length=20), w: Win = Depends(win),
              page: int = Query(1, ge=1, le=100000), page_size: int = Query(25, ge=1, le=100), db: Session = Depends(get_db)):
    by_status = dict(db.query(FollowUpSequence.status, func.count(FollowUpSequence.id)).group_by(FollowUpSequence.status).all())
    created = [r[0] for r in db.query(FollowUpSequence.created_at).filter(
        FollowUpSequence.created_at >= w.start_utc, FollowUpSequence.created_at < w.end_utc).all()]
    sent_total = db.query(func.coalesce(func.sum(FollowUpSequence.follow_ups_sent), 0)).scalar()
    q = db.query(FollowUpSequence)
    if status:
        q = q.filter(FollowUpSequence.status == status)
    rows, meta = paginate(q.order_by(FollowUpSequence.next_action_at.asc().nullslast(), FollowUpSequence.id.desc()), page, page_size)
    owners = {}
    ids = [r.source_email_id for r in rows if r.source_email_id]
    if ids:
        owners = dict(db.query(Email.id, Email.sender_email).filter(Email.id.in_(ids)).all())
    return {"success": True, "range": w.meta(), "by_status": by_status, "followups_sent_total": int(sent_total),
            "series": w.series(created=w.bucket(created)), **meta,
            "items": [{"id": r.id, "user": owners.get(r.source_email_id), "recipient": mask_email(r.recipient_email),
                       "status": r.status, "sent": r.follow_ups_sent, "max": r.max_follow_ups,
                       "next_action_at": iso(r.next_action_at), "last_checked_at": iso(r.last_checked_at),
                       "created_at": iso(r.created_at), "source": r.source_type,
                       "error": redact(r.last_error, 300) if r.last_error else None} for r in rows]}


# ------------------------------------------------------------------- system

_gmail_cache = {"at": 0, "value": None}


def _timed(fn):
    t = time.perf_counter()
    try:
        extra = fn() or {}
        return {"status": "ok", "latency_ms": round((time.perf_counter() - t) * 1000), **extra}
    except Exception as exc:
        return {"status": "down", "latency_ms": round((time.perf_counter() - t) * 1000), "error": redact(exc, 200)}


def _check_db():
    with engine.connect() as c:
        c.execute(text("SELECT 1"))
        size = None
        if DATABASE_URL.startswith("sqlite"):
            path = DATABASE_URL.replace("sqlite:///", "")
            size = os.path.getsize(path) if os.path.exists(path) else None
        elif DATABASE_URL.startswith("postgres"):
            size = c.execute(text("SELECT pg_database_size(current_database())")).scalar()
    return {"engine": engine.dialect.name, "size_bytes": size}


def _check_gmail():
    if time.time() - _gmail_cache["at"] < 60 and _gmail_cache["value"]:
        return {**_gmail_cache["value"], "cached": True}
    from backend.gmail_auth import get_gmail_service, SenderNotAuthenticatedError
    t = time.perf_counter()
    try:
        service, _ = get_gmail_service()
        service.users().getProfile(userId="me").execute()
        val = {"status": "ok", "latency_ms": round((time.perf_counter() - t) * 1000), "detail": "Gmail API reachable with the connected account."}
    except SenderNotAuthenticatedError:
        val = {"status": "no_account", "latency_ms": None, "detail": "No Gmail account is connected, so the API cannot be probed."}
    except Exception as exc:
        val = {"status": "down", "latency_ms": round((time.perf_counter() - t) * 1000), "error": redact(exc, 200)}
    _gmail_cache.update(at=time.time(), value=val)
    return val


def _check_scheduler():
    from backend.scheduler import scheduler
    jobs = scheduler.get_jobs() if scheduler.running else []
    nxt = min((j.next_run_time for j in jobs if j.next_run_time), default=None)
    return {"status": "ok" if scheduler.running else "down", "jobs": len(jobs), "next_run": iso(nxt.astimezone(timezone.utc).replace(tzinfo=None)) if nxt else None,
            "detail": "APScheduler runs reminders, follow-ups and analytics sync."}


def _check_celery():
    broker = os.getenv("CELERY_BROKER_URL")
    try:
        import celery  # noqa: F401
    except ImportError:
        celery = None
    if not (celery and broker):
        return {"status": "not_configured", "detail": "Celery is not used by this deployment. Background jobs run on APScheduler (see Scheduler)."}
    try:
        from celery import Celery
        pong = Celery(broker=broker).control.ping(timeout=2)
        return {"status": "ok" if pong else "down", "workers": len(pong or []), "detail": "Celery workers responded." if pong else "No worker answered."}
    except Exception as exc:
        return {"status": "down", "error": redact(exc, 200)}


def _check_gemini(db):
    since = datetime.utcnow() - timedelta(hours=24)
    ok, err = [int(x or 0) for x in db.query(
        func.sum(case((AIUsageLog.success.is_(True), 1), else_=0)), func.sum(case((AIUsageLog.success.is_(False), 1), else_=0))
    ).filter(AIUsageLog.created_at >= since).one()]
    last = db.query(AIUsageLog).order_by(AIUsageLog.id.desc()).first()
    configured = bool(os.getenv("GOOGLE_API_KEY"))
    status = "down" if not configured else "unknown" if not (ok + err) else ("degraded" if err and err / (ok + err) > .2 else "ok")
    return {"status": status, "configured": configured, "requests_24h": ok + err, "errors_24h": err,
            "last_call_at": iso(last.created_at) if last else None, "last_call_ok": last.success if last else None,
            "detail": "Based on real calls in the last 24h. Use 'Run live check' for a fresh probe."}


@router.get("/system")
def system(db: Session = Depends(get_db)):
    return {"success": True, "checked_at": iso(datetime.utcnow()), "services": {
        "fastapi": {"status": "ok", "uptime_seconds": round(time.time() - STARTED), "python": sys.version.split()[0], "pid": os.getpid()},
        "database": _timed(_check_db), "scheduler": _check_scheduler(), "celery": _check_celery(),
        "gmail_api": _check_gmail(), "gemini_api": _check_gemini(db)}}


@router.post("/system/gemini-check")
def gemini_check(request: Request, admin: AdminUser = ADMIN, db: Session = Depends(get_db)):
    from backend.gemini_service import ping
    res = _timed(ping)
    audit(db, admin, "system.gemini_check", detail=res["status"], ip=sec.client_ip(request))
    db.commit()
    return {"success": res["status"] == "ok", "result": res}


# -------------------------------------------------------------------- logs

@router.get("/logs/errors")
def error_logs(level: str = Query("", pattern="^(|WARNING|ERROR|CRITICAL)$"), search: str = Query("", max_length=100),
               w: Win = Depends(win), page: int = Query(1, ge=1, le=100000), page_size: int = Query(25, ge=1, le=100),
               db: Session = Depends(get_db)):
    q = db.query(ErrorLog).filter(ErrorLog.created_at >= w.start_utc, ErrorLog.created_at < w.end_utc)
    if level:
        q = q.filter(ErrorLog.level == level)
    if search.strip():
        q = q.filter(or_(ErrorLog.message.ilike(like(search.strip()), escape="\\"), ErrorLog.source.ilike(like(search.strip()), escape="\\"),
                         ErrorLog.path.ilike(like(search.strip()), escape="\\")))
    by_level = dict(db.query(ErrorLog.level, func.count(ErrorLog.id)).filter(
        ErrorLog.created_at >= w.start_utc, ErrorLog.created_at < w.end_utc).group_by(ErrorLog.level).all())
    rows, meta = paginate(q.order_by(ErrorLog.id.desc()), page, page_size)
    return {"success": True, "range": w.meta(), "by_level": by_level, **meta, "items": [{
        "id": e.id, "at": iso(e.created_at), "level": e.level, "source": e.source, "message": e.message[:400],
        "path": e.path, "status_code": e.status_code, "exc_type": e.exc_type, "has_trace": bool(e.trace)} for e in rows]}


@router.get("/logs/errors/{eid}")
def error_log(eid: int, db: Session = Depends(get_db)):
    e = db.query(ErrorLog).filter(ErrorLog.id == eid).first()
    if not e:
        raise HTTPException(404, "Not found")
    return {"success": True, "item": {"id": e.id, "at": iso(e.created_at), "level": e.level, "source": e.source,
                                      "message": e.message, "path": e.path, "status_code": e.status_code,
                                      "exc_type": e.exc_type, "trace": e.trace}}


@router.get("/logs/audit")
def audit_logs(action: str = Query("", max_length=60), admin_email: str = Query("", max_length=100), w: Win = Depends(win),
               page: int = Query(1, ge=1, le=100000), page_size: int = Query(25, ge=1, le=100), db: Session = Depends(get_db)):
    q = db.query(AuditLog).filter(AuditLog.created_at >= w.start_utc, AuditLog.created_at < w.end_utc)
    if action:
        q = q.filter(AuditLog.action.like(action + "%"))
    if admin_email.strip():
        q = q.filter(AuditLog.admin_email.ilike(like(admin_email.strip()), escape="\\"))
    rows, meta = paginate(q.order_by(AuditLog.id.desc()), page, page_size)
    actions = [a for (a,) in db.query(AuditLog.action).distinct().order_by(AuditLog.action).limit(100).all()]
    return {"success": True, "range": w.meta(), "actions": actions, **meta, "items": [{
        "id": a.id, "at": iso(a.created_at), "admin": a.admin_email, "action": a.action, "target_type": a.target_type,
        "target_id": a.target_id, "detail": a.detail, "ip": a.ip} for a in rows]}


# ----------------------------------------------------------- notifications

@router.get("/notifications")
def notifications(unread: bool = Query(False), page: int = Query(1, ge=1, le=100000), page_size: int = Query(20, ge=1, le=100),
                  db: Session = Depends(get_db)):
    q = db.query(AdminNotification)
    if unread:
        q = q.filter(AdminNotification.is_read.is_(False))
    rows, meta = paginate(q.order_by(AdminNotification.id.desc()), page, page_size)
    return {"success": True, **meta, "unread": db.query(func.count(AdminNotification.id)).filter(AdminNotification.is_read.is_(False)).scalar(),
            "items": [{"id": n.id, "at": iso(n.created_at), "kind": n.kind, "severity": n.severity, "title": n.title,
                       "body": n.body, "is_read": n.is_read} for n in rows]}


@router.post("/notifications/read-all")
def read_all(db: Session = Depends(get_db)):
    n = db.query(AdminNotification).filter(AdminNotification.is_read.is_(False)).update(
        {"is_read": True, "read_at": datetime.utcnow()}, synchronize_session=False)
    db.commit()
    return {"success": True, "updated": n}


@router.post("/notifications/{nid}/read")
def read_one(nid: int, db: Session = Depends(get_db)):
    n = db.query(AdminNotification).filter(AdminNotification.id == nid).first()
    if not n:
        raise HTTPException(404, "Not found")
    n.is_read, n.read_at = True, datetime.utcnow()
    db.commit()
    return {"success": True}


# ------------------------------------------------------------ admin accounts

class AdminIn(BaseModel):
    email: str = Field(min_length=3, max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    name: str = Field("", max_length=120)
    role: str = Field("support", pattern="^(super_admin|admin|support)$")
    password: str = Field(max_length=200)


class AdminPatch(BaseModel):
    name: Optional[str] = Field(None, max_length=120)
    role: Optional[str] = Field(None, pattern="^(super_admin|admin|support)$")
    is_active: Optional[bool] = None
    password: Optional[str] = Field(None, max_length=200)


@router.get("/admins")
def list_admins(admin: AdminUser = SUPER, db: Session = Depends(get_db)):
    return {"success": True, "admins": [admin_dict(a) for a in db.query(AdminUser).order_by(AdminUser.id).all()]}


@router.post("/admins")
def create_admin(body: AdminIn, request: Request, admin: AdminUser = SUPER, db: Session = Depends(get_db)):
    sec.validate_password_strength(body.password)
    email = body.email.strip().lower()
    if db.query(AdminUser.id).filter(AdminUser.email == email).first():
        raise HTTPException(409, "An admin with this email already exists.")
    a = AdminUser(email=email, name=body.name or None, role=body.role, password_hash=sec.hash_password(body.password))
    db.add(a); db.flush()
    audit(db, admin, "admin.create", "admin", a.id, f"{email} role={a.role}", sec.client_ip(request))
    db.commit()
    return {"success": True, "admin": admin_dict(a)}


@router.patch("/admins/{aid}")
def update_admin(aid: int, body: AdminPatch, request: Request, admin: AdminUser = SUPER, db: Session = Depends(get_db)):
    a = db.query(AdminUser).filter(AdminUser.id == aid).first()
    if not a:
        raise HTTPException(404, "Admin not found")
    demoting = (body.role and body.role != "super_admin" and a.role == "super_admin") or body.is_active is False
    if demoting and a.role == "super_admin":
        others = db.query(AdminUser.id).filter(AdminUser.role == "super_admin", AdminUser.is_active.is_(True), AdminUser.id != a.id).first()
        if not others:
            raise HTTPException(409, "There must be at least one active super admin.")
    if aid == admin.id and (body.is_active is False or (body.role and body.role != admin.role)):
        raise HTTPException(409, "You cannot change your own role or deactivate yourself.")
    changes = []
    if body.name is not None:
        a.name = body.name or None
    if body.role:
        changes.append(f"role {a.role}->{body.role}"); a.role = body.role
    if body.is_active is not None:
        changes.append(f"active={body.is_active}"); a.is_active = body.is_active
    if body.password:
        sec.validate_password_strength(body.password)
        a.password_hash = sec.hash_password(body.password)     # also invalidates that admin's sessions
        changes.append("password reset")
    audit(db, admin, "admin.update", "admin", a.id, f"{a.email}: {', '.join(changes) or 'name'}", sec.client_ip(request))
    db.commit()
    return {"success": True, "admin": admin_dict(a)}


# ---------------------------------------------------------------- settings

class GeminiKeyIn(BaseModel):
    key: str = Field(max_length=300)


def _key_status():
    from backend.admin.env_file import ENV_PATH, mask
    key = os.getenv("GOOGLE_API_KEY") or ""
    last = None
    return {"configured": bool(key), "masked": mask(key), "env_file": ENV_PATH.name, "env_writable": os.access(ENV_PATH if ENV_PATH.exists() else ENV_PATH.parent, os.W_OK)}


@router.get("/settings/gemini-key")
def gemini_key_status(admin: AdminUser = SUPER, db: Session = Depends(get_db)):
    """Never returns the key itself - only whether one is set and a masked hint."""
    last = db.query(AuditLog).filter(AuditLog.action == "settings.gemini_key").order_by(AuditLog.id.desc()).first()
    return {"success": True, **_key_status(), "updated_at": iso(last.created_at) if last else None, "updated_by": last.admin_email if last else None}


@router.put("/settings/gemini-key")
def set_gemini_key(body: GeminiKeyIn, request: Request, admin: AdminUser = SUPER, db: Session = Depends(get_db)):
    """Validates the key with a live Gemini call, writes it to .env, and switches the
    running app to it immediately. The key is never logged, audited or returned."""
    from backend.admin import env_file
    from backend import gemini_service
    from backend.admin.telemetry import notify
    key = (body.key or "").strip()
    if not env_file.valid_google_key(key):
        raise HTTPException(422, "That doesn't look like a Google API key (letters, digits, - and _ only, 20+ characters).")
    try:
        gemini_service.validate_key(key)
    except Exception as exc:
        raise HTTPException(400, "Google rejected this key: " + redact(exc, 200))
    try:
        env_file.set_env_var("GOOGLE_API_KEY", key)
    except Exception as exc:
        raise HTTPException(500, f"Could not write {env_file.ENV_PATH.name}: {redact(exc, 150)}")
    gemini_service.set_api_key(key)
    audit(db, admin, "settings.gemini_key", "setting", "GOOGLE_API_KEY", f"updated, now {env_file.mask(key)}", sec.client_ip(request))
    db.commit()
    notify("settings", "Gemini API key changed", f"by {admin.email}", "info")
    return {"success": True, **_key_status()}
