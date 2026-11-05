"""Admin authentication + role-based access control.

* Separate from the customer Gmail sign-in: admins are rows in admin_users
  with a scrypt password hash.
* Sessions are HMAC-signed, expiring, HttpOnly + SameSite=Strict cookies.
  A password change/reset invalidates old sessions (hash fingerprint is signed in).
* Every state-changing request must carry the custom header X-Admin-Request
  (cannot be sent cross-site without CORS) on top of SameSite=Strict.
* Login is rate limited per (ip, email) and per ip.
Roles:  super_admin (everything)  >  admin (operate)  >  support (read-only)
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from typing import Optional

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.admin.models import AdminUser
from backend.admin.telemetry import audit_now

ROLES = ("super_admin", "admin", "support")
RANK = {"support": 1, "admin": 2, "super_admin": 3}
COOKIE = "admin_session"
SESSION_TTL = 8 * 3600
MIN_PASSWORD = 8

_secret_cache: Optional[bytes] = None


def _secret() -> bytes:
    global _secret_cache
    if _secret_cache is None:
        env = os.getenv("ADMIN_SECRET_KEY")
        if env:
            _secret_cache = env.encode()
        else:   # derive from the app's existing encryption key: stable across restarts, never stored in the DB
            from backend.token_crypto import _load_key
            _secret_cache = hashlib.sha256(b"automail-admin-session:" + _load_key()).digest()
    return _secret_cache


# ---------------------------------------------------------------- passwords

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(h).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_b, hash_b = stored.split("$")
        salt, want = base64.b64decode(salt_b), base64.b64decode(hash_b)
        got = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(got, want)
    except Exception:
        return False


_DUMMY = hash_password("not-a-real-password-for-timing")


def validate_password_strength(pw: str):
    if len(pw) < MIN_PASSWORD:
        raise HTTPException(422, f"Password must be at least {MIN_PASSWORD} characters.")


# ----------------------------------------------------------------- sessions

def _fingerprint(admin: AdminUser) -> str:
    return hashlib.sha256(admin.password_hash.encode()).hexdigest()[:16]


def make_token(admin: AdminUser) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(
        {"aid": admin.id, "exp": int(time.time()) + SESSION_TTL, "fp": _fingerprint(admin)}).encode()).decode()
    sig = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def read_token(token: str) -> Optional[dict]:
    try:
        payload, sig = token.rsplit(".", 1)
        good = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, good):
            return None
        data = json.loads(base64.urlsafe_b64decode(payload.encode()))
        return data if data.get("exp", 0) > time.time() else None
    except Exception:
        return None


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def set_session_cookie(response, request: Request, admin: AdminUser):
    response.set_cookie(COOKIE, make_token(admin), max_age=SESSION_TTL, httponly=True, samesite="strict",
                        secure=request.url.scheme == "https", path="/api/admin")


# ------------------------------------------------------------- rate limiting

_attempts = defaultdict(deque)
_lock = threading.Lock()
WINDOW, MAX_PER_KEY, MAX_PER_IP = 15 * 60, 5, 20


def _prune(q, now):
    while q and now - q[0] > WINDOW:
        q.popleft()


def check_rate_limit(ip: str, email: str):
    now = time.time()
    with _lock:
        for key, cap in (((ip, email), MAX_PER_KEY), ((ip, ""), MAX_PER_IP)):
            _prune(_attempts[key], now)
            if len(_attempts[key]) >= cap:
                raise HTTPException(429, "Too many failed sign-in attempts. Try again in 15 minutes.")


def record_failure(ip: str, email: str):
    now = time.time()
    with _lock:
        _attempts[(ip, email)].append(now)
        _attempts[(ip, "")].append(now)


def clear_failures(ip: str, email: str):
    with _lock:
        _attempts.pop((ip, email), None)


def authenticate(db: Session, email: str, password: str, ip: str) -> AdminUser:
    email = (email or "").strip().lower()
    check_rate_limit(ip, email)
    admin = db.query(AdminUser).filter(AdminUser.email == email).first()
    ok = verify_password(password or "", admin.password_hash if admin else _DUMMY)   # constant work either way
    if not (admin and ok and admin.is_active):
        record_failure(ip, email)
        audit_now(email, "admin.login_failed", ip=ip)
        raise HTTPException(401, "Invalid email or password.")
    clear_failures(ip, email)
    return admin


# --------------------------------------------------------------- dependencies

def current_admin(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    token = request.cookies.get(COOKIE)
    data = read_token(token) if token else None
    if not data:
        raise HTTPException(401, "Admin sign-in required.")
    admin = db.query(AdminUser).filter(AdminUser.id == data["aid"]).first()
    if not admin or not admin.is_active or data.get("fp") != _fingerprint(admin):
        raise HTTPException(401, "Admin sign-in required.")
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("x-admin-request") != "1":
        raise HTTPException(403, "Missing X-Admin-Request header.")
    request.state.admin = admin
    return admin


def require(role: str):
    """Dependency factory: caller must be `role` or higher."""
    def dep(admin: AdminUser = Depends(current_admin)) -> AdminUser:
        if RANK.get(admin.role, 0) < RANK[role]:
            raise HTTPException(403, f"Requires {role.replace('_', ' ')} access.")
        return admin
    return dep


def bootstrap_first_admin(db: Session):
    """Creates the first super admin from env vars if there are no admins.
    No default credentials exist: without these variables nobody can sign in
    until `python -m backend.admin.create_admin` is run."""
    if db.query(AdminUser.id).first():
        return
    email = (os.getenv("ADMIN_BOOTSTRAP_EMAIL") or "").strip().lower()
    password = os.getenv("ADMIN_BOOTSTRAP_PASSWORD") or ""
    if not email or not password:
        print("[admin] No admin accounts. Set ADMIN_BOOTSTRAP_EMAIL/ADMIN_BOOTSTRAP_PASSWORD "
              "or run: python -m backend.admin.create_admin")
        return
    try:
        validate_password_strength(password)
    except HTTPException as exc:
        print(f"[admin] bootstrap password rejected: {exc.detail}")
        return
    db.add(AdminUser(email=email, name="Super Admin", role="super_admin", password_hash=hash_password(password)))
    db.commit()
    print(f"[admin] created super admin {email}")
