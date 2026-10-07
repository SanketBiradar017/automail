from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from backend.config import OAUTH_REDIRECT_URI
from backend.gmail_auth import (
    logger,
    build_authorization_url,
    complete_authorization,
    disconnect_account,
    get_sender_status,
    GmailVerificationError
)


router = APIRouter(
    prefix="/api/auth",
    tags=["Auth"]
)


@router.get("/status")
def auth_status():

    connected, email = get_sender_status()

    return {
        "connected": connected,
        "email": email
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

    return {
        "success": True,
        "message": "Gmail disconnected" if removed else "No Gmail account was connected"
    }
