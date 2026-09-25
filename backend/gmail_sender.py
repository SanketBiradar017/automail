import base64
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from typing import List, Dict, Optional

from google.auth.exceptions import RefreshError

from backend.gmail_auth import get_gmail_service, SenderNotAuthenticatedError


def _build_message(
    recipient: str,
    subject: str,
    body: str,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
    attachments: Optional[List[Dict[str, str]]] = None
):
    if attachments:
        message = MIMEMultipart()
        message.attach(MIMEText(body))

        for att in attachments:
            filename = att.get("filename", "attachment")
            content_b64 = att.get("content_base64", "")
            mime_type = att.get("mime_type") or "application/octet-stream"

            part = MIMEBase(*mime_type.split("/", 1)) if "/" in mime_type else MIMEBase("application", "octet-stream")
            part.set_payload(base64.b64decode(content_b64))
            encoders.encode_base64(part)
            part.add_header(
                "Content-Disposition",
                f'attachment; filename="{filename}"'
            )
            message.attach(part)
    else:
        message = MIMEText(body)

    message["to"] = recipient
    message["subject"] = subject
    if cc:
        message["cc"] = cc
    if bcc:
        message["bcc"] = bcc

    # No "From" header is set - Gmail sends as the authenticated account.
    return message


def send_email(
    sender_email: str,
    recipient: str,
    subject: str,
    body: str,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
    attachments: Optional[List[Dict[str, str]]] = None
):

    # allow_oauth=False: sending must never block on an interactive OAuth
    # flow. Accounts are connected explicitly via the Gmail account manager.
    service, _ = get_gmail_service(sender_email, allow_oauth=False)

    message = _build_message(recipient, subject, body, cc, bcc, attachments)

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
            f"Gmail account '{sender_email}' needs to be reconnected: {exc}"
        ) from exc

    return result


def send_bulk_emails(
    sender_email: str,
    emails: List[Dict[str, str]],
    attachments: Optional[List[Dict[str, str]]] = None
) -> List[Dict[str, any]]:
    """
    Send multiple emails at once.

    Args:
        sender_email: the connected Gmail account to send from
        emails: List of dicts with keys: recipient, subject, body
        attachments: Optional list of attachments shared by every email
            in the batch (dicts with keys: filename, content_base64, mime_type)

    Returns:
        List of results with keys: recipient, gmail_message_id, status, error
    """

    service, _ = get_gmail_service(sender_email, allow_oauth=False)
    results = []

    for email_data in emails:
        recipient = email_data.get("recipient")
        subject = email_data.get("subject")
        body = email_data.get("body")

        result = {
            "recipient": recipient,
            "status": "sent",
            "gmail_message_id": None,
            "error": None
        }

        try:
            message = _build_message(recipient, subject, body, attachments=attachments)

            encoded_message = base64.urlsafe_b64encode(
                message.as_bytes()
            ).decode()

            send_result = service.users().messages().send(
                userId="me",
                body={"raw": encoded_message}
            ).execute()

            result["gmail_message_id"] = send_result.get("id")

        except RefreshError as exc:
            result["status"] = "failed"
            result["error"] = f"Gmail account needs to be reconnected: {exc}"

        except Exception as exc:
            result["status"] = "failed"
            result["error"] = str(exc)

        results.append(result)

    return results


def get_message_id_header(sender_email: str, gmail_message_id: str) -> Optional[str]:
    """Fetches the RFC822 'Message-ID' header for a message we sent, so a
    later follow-up can thread properly via In-Reply-To/References."""

    service, _ = get_gmail_service(sender_email, allow_oauth=False)

    message = service.users().messages().get(
        userId="me",
        id=gmail_message_id,
        format="metadata",
        metadataHeaders=["Message-ID"]
    ).execute()

    for header in message.get("payload", {}).get("headers", []):
        if header.get("name", "").lower() == "message-id":
            return header.get("value")

    return None


def thread_has_reply(sender_email: str, thread_id: str, after: datetime) -> bool:
    """True if the thread contains any message received (INBOX-labeled,
    i.e. not one we sent) with an internal timestamp after `after`."""

    service, _ = get_gmail_service(sender_email, allow_oauth=False)

    thread = service.users().threads().get(
        userId="me",
        id=thread_id,
        format="metadata",
        metadataHeaders=["From"]
    ).execute()

    after_ms = int(after.timestamp() * 1000)

    for message in thread.get("messages", []):
        label_ids = message.get("labelIds", []) or []
        internal_date = int(message.get("internalDate", "0"))

        if "INBOX" in label_ids and "SENT" not in label_ids and internal_date > after_ms:
            return True

    return False


def send_followup_email(
    sender_email: str,
    thread_id: str,
    in_reply_to_header: Optional[str],
    recipient: str,
    subject: str,
    body: str
):
    """Sends a follow-up as a reply within the original thread."""

    service, _ = get_gmail_service(sender_email, allow_oauth=False)

    reply_subject = subject if subject.strip().lower().startswith("re:") else f"Re: {subject}"

    message = MIMEText(body)
    message["to"] = recipient
    message["subject"] = reply_subject

    if in_reply_to_header:
        message["In-Reply-To"] = in_reply_to_header
        message["References"] = in_reply_to_header

    encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()

    try:
        result = service.users().messages().send(
            userId="me",
            body={
                "raw": encoded_message,
                "threadId": thread_id
            }
        ).execute()

    except RefreshError as exc:
        raise SenderNotAuthenticatedError(
            f"Gmail account '{sender_email}' needs to be reconnected: {exc}"
        ) from exc

    return result
