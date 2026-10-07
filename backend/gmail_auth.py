import json
import logging
import os
import threading
import time
from datetime import datetime
from typing import Optional

import requests as http_requests
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from backend.config import (
    GMAIL_SCOPES,
    CLIENT_SECRET_FILE,
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET
)
from backend.database import SessionLocal
from backend.models import GmailAccount
from backend.token_crypto import encrypt, decrypt


USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v3/userinfo"
AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
REVOKE_URI = "https://oauth2.googleapis.com/revoke"

STATE_TTL_SECONDS = 600

logger = logging.getLogger("automail.oauth")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s:     [oauth] %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

_SCOPE_ALIASES = {
    "email": "https://www.googleapis.com/auth/userinfo.email",
    "profile": "https://www.googleapis.com/auth/userinfo.profile"
}


def _normalize_scopes(raw) -> set:
    """Google returns the token's `scope` as a space-separated string (or
    oauthlib turns it into a list), and may use short aliases like 'email'.
    Returns a set of canonical scope strings."""

    if not raw:
        return set()

    items = raw.split() if isinstance(raw, str) else list(raw)

    return {_SCOPE_ALIASES.get(item.strip(), item.strip()) for item in items if item.strip()}


class SenderNotAuthenticatedError(Exception):
    """Raised when no Gmail account is connected or its access was revoked."""
    pass


class GmailVerificationError(Exception):
    """Raised when the authenticated account's identity can't be verified."""
    pass


# ---------- OAuth client config ----------

def _client_config() -> dict:
    client_id = GOOGLE_CLIENT_ID
    client_secret = GOOGLE_CLIENT_SECRET

    if not (client_id and client_secret) and os.path.exists(CLIENT_SECRET_FILE):
        with open(CLIENT_SECRET_FILE) as f:
            data = json.load(f)
        info = data.get("web") or data.get("installed") or {}
        client_id = client_id or info.get("client_id")
        client_secret = client_secret or info.get("client_secret")

    if not (client_id and client_secret):
        raise FileNotFoundError(
            "Google OAuth client not configured. Set GOOGLE_CLIENT_ID and "
            "GOOGLE_CLIENT_SECRET in .env."
        )

    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": AUTH_URI,
            "token_uri": TOKEN_URI
        }
    }


# ---------- Login flow ----------

# state -> (redirect_uri, code_verifier, created_at). In-memory is fine: the
# window between /login and /callback is seconds, and a restart in between
# just means the user clicks "Continue with Google" again.
_pending = {}
_pending_lock = threading.Lock()


def build_authorization_url(redirect_uri: str) -> str:
    flow = Flow.from_client_config(
        _client_config(),
        scopes=GMAIL_SCOPES,
        redirect_uri=redirect_uri,
        autogenerate_code_verifier=True
    )

    # offline + consent: guarantees Google returns a refresh token.
    url, state = flow.authorization_url(
        access_type="offline",
        prompt="consent select_account"
    )

    now = time.time()
    with _pending_lock:
        for key in [k for k, v in _pending.items() if now - v[2] > STATE_TTL_SECONDS]:
            del _pending[key]
        _pending[state] = (redirect_uri, flow.code_verifier, now)

    return url


def complete_authorization(code: str, state: str) -> str:
    """Exchanges the callback code, stores the account, returns its email."""

    with _pending_lock:
        pending = _pending.pop(state, None)

    if not pending or time.time() - pending[2] > STATE_TTL_SECONDS:
        raise GmailVerificationError(
            "Sign-in session expired or invalid. Please try again."
        )

    redirect_uri, code_verifier, _ = pending

    flow = Flow.from_client_config(
        _client_config(),
        scopes=None,  # accept whatever Google reports; checked below
        redirect_uri=redirect_uri,
        code_verifier=code_verifier
    )
    flow.fetch_token(code=code)

    # Read the scopes Google actually granted from the raw token response.
    # flow.credentials.scopes is NOT reliable: it is None when the Flow was
    # built without scopes, and echoes the *requested* scopes otherwise.
    token = flow.oauth2session.token
    granted = _normalize_scopes(token.get("scope"))
    required = _normalize_scopes(GMAIL_SCOPES)
    missing = required - granted

    logger.info(
        "OAuth callback: requested=%s granted=%s missing=%s "
        "token_response_keys=%s has_refresh_token=%s",
        sorted(required), sorted(granted), sorted(missing),
        sorted(token.keys()), bool(token.get("refresh_token"))
    )

    if missing:
        raise GmailVerificationError(
            "Required Gmail permissions were not granted "
            f"(missing: {', '.join(sorted(missing))}). Please try again "
            "and allow all requested permissions."
        )

    creds = flow.credentials
    creds._scopes = sorted(granted)  # persist what was actually granted

    email = _get_authenticated_email(creds)
    _save_account(email, creds)

    return email


def _get_authenticated_email(creds) -> str:
    # The OpenID userinfo REST endpoint needs no per-project API enablement,
    # unlike the legacy discovery-based oauth2 v2 API.
    try:
        response = AuthorizedSession(creds).get(USERINFO_ENDPOINT, timeout=10)

    except RefreshError:
        raise

    except Exception as exc:
        raise GmailVerificationError(
            f"Could not reach Google's userinfo endpoint: {exc}"
        ) from exc

    if response.status_code != 200:
        raise GmailVerificationError(
            f"Google userinfo request failed ({response.status_code}): {response.text}"
        )

    email = response.json().get("email", "")

    if not email:
        raise GmailVerificationError(
            "Google's userinfo response did not include an email address."
        )

    return email.strip().lower()


# ---------- Storage ----------

def _save_account(email: str, creds):
    db = SessionLocal()
    try:
        existing = db.query(GmailAccount).filter(GmailAccount.email == email).first()

        refresh_token = creds.refresh_token
        if not refresh_token and existing and existing.refresh_token_enc:
            refresh_token = decrypt(existing.refresh_token_enc)

        # Only one account is "connected" at a time.
        db.query(GmailAccount).filter(GmailAccount.email != email).delete()

        row = existing or GmailAccount(email=email)
        row.access_token_enc = encrypt(creds.token)
        row.refresh_token_enc = encrypt(refresh_token) if refresh_token else None
        row.token_expiry = creds.expiry
        row.scopes = " ".join(creds.scopes or GMAIL_SCOPES)
        row.connected_at = datetime.utcnow() if not existing else row.connected_at

        db.add(row)
        db.commit()
    finally:
        db.close()


def _load_account(db) -> Optional[GmailAccount]:
    return db.query(GmailAccount).order_by(GmailAccount.updated_at.desc()).first()


def _creds_from_row(row: GmailAccount) -> Credentials:
    config = _client_config()["web"]

    return Credentials(
        token=decrypt(row.access_token_enc),
        refresh_token=decrypt(row.refresh_token_enc) if row.refresh_token_enc else None,
        token_uri=TOKEN_URI,
        client_id=config["client_id"],
        client_secret=config["client_secret"],
        scopes=(row.scopes or "").split() or GMAIL_SCOPES,
        expiry=row.token_expiry
    )


# ---------- Public API ----------

def get_gmail_service():
    """
    Returns (service, email) for the currently connected Gmail account.
    Expired access tokens are refreshed automatically and persisted.

    Raises SenderNotAuthenticatedError if no account is connected or its
    refresh token was revoked/expired (the stale row is removed so the UI
    falls back to "Continue with Google").
    """

    db = SessionLocal()
    try:
        row = _load_account(db)

        if not row:
            raise SenderNotAuthenticatedError(
                "No Gmail account connected. Please connect your Gmail account."
            )

        creds = _creds_from_row(row)

        if not creds.valid:

            if not creds.refresh_token:
                db.delete(row)
                db.commit()
                raise SenderNotAuthenticatedError(
                    "Gmail session expired. Please reconnect your Gmail account."
                )

            try:
                creds.refresh(Request())

            except RefreshError as exc:
                db.delete(row)
                db.commit()
                raise SenderNotAuthenticatedError(
                    f"Gmail access for '{row.email}' was revoked or expired. "
                    f"Please reconnect: {exc}"
                ) from exc

            row.access_token_enc = encrypt(creds.token)
            row.token_expiry = creds.expiry
            db.commit()

        email = row.email

    finally:
        db.close()

    service = build("gmail", "v1", credentials=creds, cache_discovery=False)

    return service, email


def get_connected_email() -> Optional[str]:
    """Read-only: the connected account's email, or None. No network calls."""

    db = SessionLocal()
    try:
        row = _load_account(db)
        return row.email if row else None
    finally:
        db.close()


def get_sender_status():
    """Returns (connected: bool, email | None). Refreshes the token if it is
    expired, so a revoked account is reported as disconnected."""

    if not get_connected_email():
        return False, None

    try:
        _, email = get_gmail_service()
        return True, email

    except Exception:
        return False, None


def disconnect_account() -> bool:
    """Revokes the token at Google (best effort) and deletes it locally."""

    db = SessionLocal()
    try:
        row = _load_account(db)

        if not row:
            return False

        token = (
            decrypt(row.refresh_token_enc)
            if row.refresh_token_enc
            else decrypt(row.access_token_enc)
        )

        try:
            http_requests.post(
                REVOKE_URI,
                params={"token": token},
                headers={"content-type": "application/x-www-form-urlencoded"},
                timeout=10
            )
        except Exception:
            pass  # still remove local access

        db.delete(row)
        db.commit()
        return True

    finally:
        db.close()
