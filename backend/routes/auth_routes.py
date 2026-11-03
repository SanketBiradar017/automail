from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from backend import avatar_service
from backend.config import OAUTH_REDIRECT_URI
from backend.gmail_auth import (
    logger,
    build_authorization_url,
    complete_authorization,
    disconnect_account,
    get_sender_status,
    get_account_profile,
    refresh_account_profile,
    GmailVerificationError
)


CONTACTS_SCOPE = "https://www.googleapis.com/auth/contacts.readonly"
OTHER_CONTACTS_SCOPE = "https://www.googleapis.com/auth/contacts.other.readonly"

router = APIRouter(
    prefix="/api/auth",
    tags=["Auth"]
)


@router.get("/status")
def auth_status():

    connected, email = get_sender_status()

    profile = None
    if connected:
        profile = get_account_profile() or {}
        if not profile.get("picture"):
            try:  # one-off backfill for accounts connected before avatars existed
                profile = refresh_account_profile() or profile
            except Exception:
                pass

    scopes = (profile or {}).get("scopes") or set()

    return {
        "connected": connected,
        "email": email,
        "name": (profile or {}).get("name"),
        "has_picture": bool((profile or {}).get("picture")),
        "sender_photos": bool(scopes & {CONTACTS_SCOPE, OTHER_CONTACTS_SCOPE})
    }


@router.get("/login")
def login(request: Request):
    """Starts Google OAuth: redirects the browser to Google's consent screen."""

    redirect_uri = OAUTH_REDIRECT_URI or str(request.url_for("auth_callback"))

    try:
        return RedirectResponse(build_authorization_url(redirect_uri))

    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/callback", name="auth_callback")
def callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None
):

    logger.info(
        "OAuth callback hit: error=%s has_code=%s has_state=%s",
        error, bool(code), bool(state)
    )

    if error or not code or not state:
        message = "Google sign-in was cancelled." if error == "access_denied" else (
            f"Google sign-in failed: {error or 'missing code'}"
        )
        return RedirectResponse("/?" + urlencode({"auth": "error", "msg": message}))

    try:
        email = complete_authorization(code, state)

    except GmailVerificationError as exc:
        logger.warning("OAuth callback rejected: %s", exc)
        return RedirectResponse("/?" + urlencode({"auth": "error", "msg": str(exc)}))

    except Exception as exc:
        logger.exception("OAuth callback failed")
        return RedirectResponse(
            "/?" + urlencode({"auth": "error", "msg": f"Gmail authorization failed: {exc}"})
        )

    return RedirectResponse("/?" + urlencode({"auth": "success", "email": email}))


@router.post("/logout")
def logout():

    removed = disconnect_account()
    avatar_service.clear_cache()

    return {
        "success": True,
        "message": "Gmail disconnected" if removed else "No Gmail account was connected"
    }
