import base64
from email.mime.text import MIMEText

from google.auth.exceptions import RefreshError

from backend.config import SENDER_EMAIL
from backend.gmail_auth import get_gmail_service, SenderNotAuthenticatedError


def send_email(
    recipient: str,
    subject: str,
    body: str
):

    if not SENDER_EMAIL:
        raise ValueError("SENDER_EMAIL is missing from .env")

    # allow_oauth=False: sending must never block on an interactive OAuth
    # flow. The sender account is connected explicitly via /connect-sender.
    service, _ = get_gmail_service(SENDER_EMAIL, allow_oauth=False)

    message = MIMEText(body)

    message["to"] = recipient
    message["subject"] = subject

    # No "From" header is set - Gmail sends as the authenticated account.

    encoded_message = base64.urlsafe_b64encode(
        message.as_bytes()
    ).decode()

    try:
        result = service.users().messages().send(
            userId="me",
            body={
                "raw": encoded_message
            }
        ).execute()

    except RefreshError as exc:
        raise SenderNotAuthenticatedError(
            f"Gmail account '{SENDER_EMAIL}' needs to be reconnected: {exc}"
        ) from exc

    return result
