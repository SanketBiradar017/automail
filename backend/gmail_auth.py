import json
import os
from datetime import datetime

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from backend.config import GMAIL_SCOPES, CLIENT_SECRET_FILE
from backend.database import SessionLocal
from backend.models import GmailAccount


USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v3/userinfo"


class SenderMismatchError(Exception):
    """Raised when the authenticated Gmail account doesn't match the requested one."""

    def __init__(self, configured_email: str, authenticated_email: str):
        self.configured_email = configured_email
        self.authenticated_email = authenticated_email

        super().__init__(
            f"Expected sender '{configured_email}' does not match "
            f"authenticated Gmail account '{authenticated_email}'."
        )


class SenderNotAuthenticatedError(Exception):
    """Raised when an account has no valid token and OAuth wasn't allowed."""
    pass


class GmailVerificationError(Exception):
    """Raised when the authenticated account's identity can't be verified."""
    pass


def _run_login_flow():

    if not os.path.exists(CLIENT_SECRET_FILE):
        raise FileNotFoundError(
            f"Gmail OAuth client secret not found at '{CLIENT_SECRET_FILE}'. "
            "Set CLIENT_SECRET_FILE in .env or add the file."
        )

    print("Opening browser for Gmail login...")

    flow = InstalledAppFlow.from_client_secrets_file(
        CLIENT_SECRET_FILE,
        GMAIL_SCOPES
    )

    return flow.run_local_server(port=0)


def _get_authenticated_email(creds) -> str:
    # Deliberately NOT build("oauth2", "v2", ...).userinfo() - that legacy
    # discovery-based API is backed by the "Legacy People API", which most
    # Cloud projects don't have enabled and fails with an uncaught 403.
    # The OpenID userinfo REST endpoint needs no per-project enablement and
    # only needs the userinfo.email/openid scopes already granted below.
    try:
        session = AuthorizedSession(creds)
        response = session.get(USERINFO_ENDPOINT, timeout=10)

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


def _get_authenticated_name(creds):
    try:
        session = AuthorizedSession(creds)
        response = session.get(USERINFO_ENDPOINT, timeout=10)
        if response.status_code == 200:
            return response.json().get("name")
    except Exception:
        pass
    return None


def _creds_from_account(account: GmailAccount):
    if not account or not account.token_json:
        return None
    try:
        return Credentials.from_authorized_user_info(json.loads(account.token_json), GMAIL_SCOPES)
    except Exception:
        return None


def _upsert_account(db, email: str, creds, display_name=None) -> GmailAccount:
    email = email.strip().lower()
    account = db.query(GmailAccount).filter(GmailAccount.email == email).first()

    if not account:
        is_first = db.query(GmailAccount).count() == 0
        account = GmailAccount(email=email, is_active=is_first, connected_at=datetime.utcnow())
        db.add(account)

    account.token_json = creds.to_json()
    if display_name:
        account.display_name = display_name
    account.status = "connected"
    account.last_error = None
    account.last_verified_at = datetime.utcnow()

    db.commit()
    db.refresh(account)
    return account


def get_gmail_service(sender_email: str, allow_oauth: bool = False):
    """
    Returns (service, authenticated_email) for an already-connected account
    identified by sender_email.

    allow_oauth=False (the normal send-time path): never opens a browser -
    raises SenderNotAuthenticatedError if the token is missing/invalid/
    under-scoped, so the caller can tell the user to reconnect that account.

    allow_oauth=True: used only to *reconnect* a specific, already-known
    account (e.g. a "Reconnect" button on an expired account) - re-runs the
    consent screen but still enforces that the reauthorized account matches
    sender_email (raises SenderMismatchError otherwise). Adding a brand new
    account (no expected email yet) goes through connect_new_gmail_account()
    instead.
    """

    if not sender_email:
        raise ValueError("sender_email is required.")

    sender_email = sender_email.strip().lower()

    db = SessionLocal()
    try:
        account = db.query(GmailAccount).filter(GmailAccount.email == sender_email).first()
        creds = _creds_from_account(account)

        if not creds or not creds.valid:

            if creds and creds.expired and creds.refresh_token:
                print(f"Refreshing Gmail token for {sender_email}...")
                try:
                    creds.refresh(Request())
                except RefreshError:
                    print("Refresh token expired/revoked.")
                    creds = None

            if not creds or not creds.valid:
                if not allow_oauth:
                    if account:
                        account.status = "needs_reconnect"
                        account.last_error = "Token invalid or expired."
                        db.commit()
                    raise SenderNotAuthenticatedError(
                        f"Gmail account '{sender_email}' is not connected. Please reconnect it."
                    )
                creds = _run_login_flow()

            account = _upsert_account(db, sender_email, creds)

        # A token loaded from the DB carries the scopes it was ORIGINALLY
        # consented with, not the current GMAIL_SCOPES config. If GMAIL_SCOPES
        # has grown (e.g. gmail.readonly added for follow-up reply detection)
        # after this token was issued, the refresh token can't silently
        # upgrade itself - Google just keeps returning tokens scoped to the
        # old consent, and a readonly API call would otherwise fail later
        # with an opaque 403. Catch that here instead, where we can ask for
        # reconnect explicitly.
        required_scopes = set(GMAIL_SCOPES)
        granted_scopes = set(getattr(creds, "scopes", None) or [])

        if not required_scopes.issubset(granted_scopes):
            if not allow_oauth:
                account.status = "needs_reconnect"
                account.last_error = "Needs reconnect to grant additional permissions."
                db.commit()
                raise SenderNotAuthenticatedError(
                    f"Gmail account '{sender_email}' needs to be reconnected to "
                    "grant additional permissions."
                )
            creds = _run_login_flow()
            account = _upsert_account(db, sender_email, creds)

        try:
            authenticated_email = _get_authenticated_email(creds)

        except RefreshError as exc:
            # googleapiclient's transport refreshes before every call regardless
            # of the expiry check above, so a token whose granted scope no
            # longer covers what we're requesting fails here, not in the
            # explicit refresh branch above.
            if not allow_oauth:
                account.status = "needs_reconnect"
                account.last_error = str(exc)
                db.commit()
                raise SenderNotAuthenticatedError(
                    f"Gmail account '{sender_email}' needs to be reconnected: {exc}"
                ) from exc

            creds = _run_login_flow()
            account = _upsert_account(db, sender_email, creds)
            authenticated_email = _get_authenticated_email(creds)

        if authenticated_email != sender_email:
            raise SenderMismatchError(sender_email, authenticated_email)

        account.status = "connected"
        account.last_error = None
        account.last_verified_at = datetime.utcnow()
        db.commit()

        service = build("gmail", "v1", credentials=creds)
        return service, authenticated_email

    finally:
        db.close()


def connect_new_gmail_account():
    """
    "+ Add Gmail": runs the OAuth consent screen with no pre-known target
    account - whichever Google account the user picks/consents with becomes
    the connected account (added new, or re-authorized if it already existed
    here). Returns (id, email, display_name).
    """
    creds = _run_login_flow()
    authenticated_email = _get_authenticated_email(creds)
    display_name = _get_authenticated_name(creds)

    db = SessionLocal()
    try:
        account = _upsert_account(db, authenticated_email, creds, display_name=display_name)
        return account.id, account.email, account.display_name
    finally:
        db.close()


def get_sender_status(sender_email: str):
    """
    Read-only check: never opens a browser. Refreshes an expired token if a
    refresh token is available. Returns (matches: bool, authenticated_email).
    """

    if not sender_email:
        return False, None

    sender_email = sender_email.strip().lower()

    db = SessionLocal()
    try:
        account = db.query(GmailAccount).filter(GmailAccount.email == sender_email).first()
        creds = _creds_from_account(account)

        if not creds:
            return False, None

        if not creds.valid:
            if creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    account.token_json = creds.to_json()
                    account.last_verified_at = datetime.utcnow()
                    db.commit()
                except RefreshError:
                    account.status = "needs_reconnect"
                    db.commit()
                    return False, None
            else:
                return False, None

        try:
            authenticated_email = _get_authenticated_email(creds)
        except Exception:
            return False, None

        matches = authenticated_email == sender_email

        if matches:
            account.status = "connected"
            account.last_verified_at = datetime.utcnow()
            db.commit()

        return matches, authenticated_email
    finally:
        db.close()


def list_gmail_accounts():
    db = SessionLocal()
    try:
        accounts = db.query(GmailAccount).order_by(GmailAccount.connected_at.asc()).all()
        return [
            {
                "id": a.id,
                "email": a.email,
                "display_name": a.display_name,
                "is_active": a.is_active,
                "status": a.status,
                "connected_at": a.connected_at,
                "last_verified_at": a.last_verified_at,
                "last_error": a.last_error,
            }
            for a in accounts
        ]
    finally:
        db.close()


def get_active_gmail_email() -> str:
    db = SessionLocal()
    try:
        account = db.query(GmailAccount).filter(GmailAccount.is_active.is_(True)).first()
        if not account:
            raise SenderNotAuthenticatedError(
                "No active Gmail account. Add a Gmail account and select it first."
            )
        return account.email
    finally:
        db.close()


def set_active_gmail_account(account_id: int) -> dict:
    db = SessionLocal()
    try:
        account = db.query(GmailAccount).filter(GmailAccount.id == account_id).first()
        if not account:
            raise ValueError("Gmail account not found.")

        db.query(GmailAccount).update({GmailAccount.is_active: False})
        account.is_active = True
        db.commit()
        db.refresh(account)

        return {
            "id": account.id,
            "email": account.email,
            "display_name": account.display_name,
            "is_active": account.is_active,
            "status": account.status,
        }
    finally:
        db.close()


def migrate_legacy_sender_token():
    """
    One-time startup migration: this app used to store exactly one Gmail
    token file (tokens/<slug>.json) for a single hard-coded SENDER_EMAIL.
    If that file still exists and hasn't been imported into gmail_accounts
    yet, import it so the already-connected account keeps working without
    forcing a reconnect.
    """
    from backend.config import SENDER_EMAIL, TOKEN_DIRECTORY
    import re

    if not SENDER_EMAIL:
        return

    email = SENDER_EMAIL.strip().lower()

    db = SessionLocal()
    try:
        if db.query(GmailAccount).filter(GmailAccount.email == email).first():
            return

        safe = re.sub(r"[^a-zA-Z0-9]+", "_", email).strip("_")
        token_path = os.path.join(TOKEN_DIRECTORY, f"{safe}.json")

        if not os.path.exists(token_path):
            return

        with open(token_path) as f:
            token_json = f.read()

        is_first = db.query(GmailAccount).count() == 0
        account = GmailAccount(
            email=email,
            token_json=token_json,
            is_active=is_first,
            status="connected",
            connected_at=datetime.utcnow(),
            last_verified_at=datetime.utcnow(),
        )
        db.add(account)
        db.commit()
        print(f"Migrated legacy Gmail token for {email} into gmail_accounts.")

    finally:
        db.close()


def disconnect_gmail_account(account_id: int):
    db = SessionLocal()
    try:
        account = db.query(GmailAccount).filter(GmailAccount.id == account_id).first()
        if not account:
            raise ValueError("Gmail account not found.")

        was_active = account.is_active
        db.delete(account)
        db.flush()

        if was_active:
            next_account = db.query(GmailAccount).order_by(GmailAccount.connected_at.asc()).first()
            if next_account:
                next_account.is_active = True

        db.commit()
    finally:
        db.close()
