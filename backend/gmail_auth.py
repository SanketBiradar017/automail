import os
import re

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from backend.config import (
    GMAIL_SCOPES,
    CLIENT_SECRET_FILE,
    TOKEN_DIRECTORY
)


USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v3/userinfo"


class SenderMismatchError(Exception):
    """Raised when the authenticated Gmail account doesn't match SENDER_EMAIL."""

    def __init__(self, configured_email: str, authenticated_email: str):
        self.configured_email = configured_email
        self.authenticated_email = authenticated_email

        super().__init__(
            f"Configured sender '{configured_email}' does not match "
            f"authenticated Gmail account '{authenticated_email}'."
        )


class SenderNotAuthenticatedError(Exception):
    """Raised when SENDER_EMAIL has no valid token and OAuth wasn't allowed."""
    pass


class GmailVerificationError(Exception):
    """Raised when the authenticated account's identity can't be verified."""
    pass


def _token_filename(email: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9]+", "_", email.strip().lower()).strip("_")
    return f"{safe}.json"


def _token_path(email: str) -> str:
    return os.path.join(TOKEN_DIRECTORY, _token_filename(email))


def _load_creds(token_path: str):
    if not os.path.exists(token_path):
        return None

    return Credentials.from_authorized_user_file(
        token_path,
        GMAIL_SCOPES
    )


def _save_creds(creds, token_path: str):
    os.makedirs(os.path.dirname(token_path), exist_ok=True)

    with open(token_path, "w") as token_file:
        token_file.write(creds.to_json())


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


def get_gmail_service(sender_email: str, allow_oauth: bool = True):
    """
    Returns (service, authenticated_email) for sender_email, using an
    account-specific token file. Refreshes an expired token when possible.

    If no valid token exists:
      - allow_oauth=True  -> opens the browser OAuth consent screen.
      - allow_oauth=False -> raises SenderNotAuthenticatedError.

    Raises SenderMismatchError if the token's Gmail account doesn't match
    sender_email.
    """

    if not sender_email:
        raise ValueError("SENDER_EMAIL is not configured.")

    token_path = _token_path(sender_email)
    creds = _load_creds(token_path)

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
                raise SenderNotAuthenticatedError(
                    f"Gmail account '{sender_email}' is not connected. "
                    "Please connect the configured sender account."
                )

            creds = _run_login_flow()

        _save_creds(creds, token_path)

        print("Gmail authentication successful.")

    try:
        authenticated_email = _get_authenticated_email(creds)

    except RefreshError as exc:
        # googleapiclient's transport refreshes before every call regardless
        # of the expiry check above, so a token whose granted scope no
        # longer covers what we're requesting (e.g. after adding a new
        # scope) fails here, not in the explicit refresh branch above.
        if not allow_oauth:
            raise SenderNotAuthenticatedError(
                f"Gmail account '{sender_email}' needs to be reconnected: {exc}"
            ) from exc

        # connect-sender: re-run the consent screen so the user can grant
        # the currently required scopes, instead of failing silently.
        creds = _run_login_flow()
        _save_creds(creds, token_path)

        authenticated_email = _get_authenticated_email(creds)

    if authenticated_email != sender_email.strip().lower():
        raise SenderMismatchError(sender_email, authenticated_email)

    service = build(
        "gmail",
        "v1",
        credentials=creds
    )

    return service, authenticated_email


def get_sender_status(sender_email: str):
    """
    Read-only check: never opens a browser or refreshes interactively.
    Returns (matches: bool, authenticated_email: str | None).
    """

    if not sender_email:
        return False, None

    token_path = _token_path(sender_email)
    creds = _load_creds(token_path)

    if not creds:
        return False, None

    if not creds.valid:

        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                _save_creds(creds, token_path)

            except RefreshError:
                return False, None
        else:
            return False, None

    try:
        authenticated_email = _get_authenticated_email(creds)

    except Exception:
        return False, None

    matches = authenticated_email == sender_email.strip().lower()

    return matches, authenticated_email
