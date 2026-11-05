"""Write-side helpers: AI usage, error log, audit log, admin notifications.
Nothing here may ever raise into the caller - telemetry must not break sends."""

import logging
import re
import threading
import traceback
from datetime import datetime, timedelta
from typing import Optional

from backend.database import SessionLocal
from backend.admin.models import (AIUsageLog, AdminNotification, AppUser, AuditLog, ErrorLog)

_EMAIL = re.compile(r"([A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]*@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})")
_SECRETS = [
    re.compile(r"ya29\.[\w\-.]+"), re.compile(r"1//[\w\-]{20,}"), re.compile(r"AIza[\w\-]{20,}"),
    re.compile(r"(?i)bearer\s+[\w\-.=]{12,}"), re.compile(r"(?i)(access_token|refresh_token|client_secret|api[_-]?key)\s*[=:]\s*['\"]?[\w\-./]{6,}"),
    re.compile(r"eyJ[\w\-]{10,}\.[\w\-]{10,}\.[\w\-]{10,}"),
]


def redact(text, limit: int = 2000) -> str:
    """Strips tokens/keys and masks email addresses (a***@domain)."""
    s = "" if text is None else str(text)
    for rx in _SECRETS:
        s = rx.sub("[redacted]", s)
    s = _EMAIL.sub(lambda m: f"{m.group(1)}***@{m.group(2)}", s)
    return s[:limit]


def mask_email(addr: Optional[str]) -> str:
    if not addr or "@" not in addr:
        return addr or ""
    local, domain = addr.rsplit("@", 1)
    return f"{local[:1]}***@{domain}"


def _session():
    return SessionLocal()


# ------------------------------------------------------------------ AI

def log_ai_call(feature: str, model: str, usage: Optional[dict], latency_ms: int,
                success: bool, error: Optional[str] = None):
    try:
        from backend.gmail_auth import get_connected_email
        user = None
        try:
            user = get_connected_email()
        except Exception:
            pass
        usage = usage or {}
        db = _session()
        try:
            db.add(AIUsageLog(feature=feature, model=model,
                              input_tokens=int(usage.get("input_tokens") or 0),
                              output_tokens=int(usage.get("output_tokens") or 0),
                              latency_ms=int(latency_ms), success=bool(success),
                              error=redact(error, 300) if error else None, user_email=user))
            db.commit()
        finally:
            db.close()
        if not success:
            notify("ai_failure", "Gemini request failed", redact(error, 300), "warning", dedupe_key="ai_failure", window_minutes=30)
    except Exception:
        pass


# --------------------------------------------------------------- errors

_guard = threading.local()


def record_error(level: str, source: str, message: str, path: Optional[str] = None,
                 status_code: Optional[int] = None, exc: Optional[BaseException] = None):
    if getattr(_guard, "busy", False):
        return
    _guard.busy = True
    try:
        trace = None
        if exc is not None:
            trace = redact("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)), 6000)
        db = _session()
        try:
            db.add(ErrorLog(level=level, source=(source or "")[:80], message=redact(message, 2000),
                            path=(path or "")[:300] or None, status_code=status_code,
                            exc_type=type(exc).__name__ if exc else None, trace=trace))
            db.commit()
        finally:
            db.close()
        if level in ("ERROR", "CRITICAL"):
            notify("error", f"{level.title()} in {source or 'backend'}", redact(message, 300),
                   "critical" if level == "CRITICAL" else "warning", dedupe_key=f"err:{source}", window_minutes=30)
    except Exception:
        pass
    finally:
        _guard.busy = False


class ErrorLogHandler(logging.Handler):
    """Persists ERROR+ log records (except noisy access/sql loggers)."""
    def emit(self, record: logging.LogRecord):
        if record.levelno < logging.ERROR or record.name.startswith(("sqlalchemy", "uvicorn.access")):
            return
        try:
            record_error(record.levelname, record.name, record.getMessage(),
                         exc=record.exc_info[1] if record.exc_info else None)
        except Exception:
            pass


def install_error_logging():
    root = logging.getLogger()
    if not any(isinstance(h, ErrorLogHandler) for h in root.handlers):
        root.addHandler(ErrorLogHandler(level=logging.ERROR))


# ---------------------------------------------------------- audit/notify

def audit(db, admin, action: str, target_type: str = None, target_id=None, detail: str = None, ip: str = None):
    """Adds an audit row on the caller's session (committed with their change)."""
    db.add(AuditLog(admin_id=getattr(admin, "id", None), admin_email=getattr(admin, "email", None),
                    action=action, target_type=target_type,
                    target_id=str(target_id) if target_id is not None else None,
                    detail=redact(detail, 1000) if detail else None, ip=ip))


def audit_now(admin_email: Optional[str], action: str, detail: str = None, ip: str = None, admin_id=None):
    try:
        db = _session()
        try:
            db.add(AuditLog(admin_id=admin_id, admin_email=admin_email, action=action,
                            detail=redact(detail, 1000) if detail else None, ip=ip))
            db.commit()
        finally:
            db.close()
    except Exception:
        pass


def notify(kind: str, title: str, body: str = "", severity: str = "info",
           dedupe_key: Optional[str] = None, window_minutes: int = 60):
    try:
        db = _session()
        try:
            if dedupe_key:
                since = datetime.utcnow() - timedelta(minutes=window_minutes)
                if db.query(AdminNotification.id).filter(AdminNotification.dedupe_key == dedupe_key,
                                                         AdminNotification.created_at >= since).first():
                    return
            db.add(AdminNotification(kind=kind, severity=severity, title=title[:200],
                                     body=redact(body, 1000), dedupe_key=dedupe_key))
            db.commit()
        finally:
            db.close()
    except Exception:
        pass


# ----------------------------------------------------------------- users

def upsert_user_on_login(email: str, name: Optional[str] = None) -> Optional[AppUser]:
    """Called when a Google account completes sign-in. Returns the (detached) user row."""
    try:
        db = _session()
        try:
            u = db.query(AppUser).filter(AppUser.email == email).first()
            new = u is None
            if new:
                u = AppUser(email=email, name=name)
                db.add(u)
            u.name = name or u.name
            u.last_login_at = datetime.utcnow()
            u.login_count = (u.login_count or 0) + 1
            u.gmail_disconnected_at = None
            db.commit()
            db.refresh(u)
            db.expunge(u)
            if new:
                notify("new_user", "New user signed in", mask_email(email), "info")
            return u
        finally:
            db.close()
    except Exception:
        return None


def is_suspended(email: Optional[str]) -> bool:
    if not email:
        return False
    try:
        db = _session()
        try:
            u = db.query(AppUser.status).filter(AppUser.email == email).first()
            return bool(u and u[0] == "suspended")
        finally:
            db.close()
    except Exception:
        return False


def mark_gmail_disconnected(email: Optional[str], reason: str = "disconnected"):
    if not email:
        return
    try:
        db = _session()
        try:
            u = db.query(AppUser).filter(AppUser.email == email).first()
            if u:
                u.gmail_disconnected_at = datetime.utcnow()
                db.commit()
        finally:
            db.close()
        notify("gmail_disconnected", "Gmail connection lost", f"{mask_email(email)}: {reason}", "warning",
               dedupe_key=f"gmail:{email}", window_minutes=120)
    except Exception:
        pass


def backfill_users():
    """Creates AppUser rows for accounts that existed before the admin panel."""
    try:
        from backend.models import Email, GmailAccount
        db = _session()
        try:
            have = {e for (e,) in db.query(AppUser.email).all()}
            found = {}
            for acc in db.query(GmailAccount).all():
                found[acc.email] = (acc.display_name, acc.connected_at)
            for (e,) in db.query(Email.sender_email).filter(Email.sender_email.isnot(None)).distinct().all():
                found.setdefault(e, (None, None))
            for email, (name, since) in found.items():
                if email not in have:
                    db.add(AppUser(email=email, name=name, created_at=since or datetime.utcnow()))
            db.commit()
        finally:
            db.close()
    except Exception:
        pass
