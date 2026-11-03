import base64
from datetime import datetime, timezone
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
    attachments: Optional[List[Dict[str, str]]] = None,
    pixel_url: Optional[str] = None,
    receipt_to: Optional[str] = None
):
    def _text_part():
        """Plain text; with an opt-in tracking pixel, text + html alternative."""
        if not pixel_url:
            return MIMEText(body)
        alt = MIMEMultipart("alternative")
        alt.attach(MIMEText(body, "plain"))
        safe = (body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace("\n", "<br>\n"))
        alt.attach(MIMEText(
            f'<div style="white-space:normal">{safe}</div>'
            f'<img src="{pixel_url}" width="1" height="1" alt="" style="display:none">',
            "html"))
        return alt

    if attachments:
        message = MIMEMultipart()
        message.attach(_text_part())

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
        message = _text_part()

    if receipt_to:
        # Standard read-receipt request: the recipient's client asks them first.
        message["Disposition-Notification-To"] = receipt_to
    message["to"] = recipient
    message["subject"] = subject
    if cc:
        message["cc"] = cc
    if bcc:
        message["bcc"] = bcc

    # No "From" header is set - Gmail sends as the authenticated account.
    return message


def send_email(
    recipient: str,
    subject: str,
    body: str,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
    attachments: Optional[List[Dict[str, str]]] = None,
    pixel_url: Optional[str] = None,
    request_receipt: bool = False
):

    service, sender = get_gmail_service()

    message = _build_message(recipient, subject, body, cc, bcc, attachments,
                             pixel_url=pixel_url,
                             receipt_to=sender if request_receipt else None)

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
            f"Gmail account '{sender}' needs to be reconnected: {exc}"
        ) from exc

    return result


def send_bulk_emails(
    emails: List[Dict[str, str]],
    attachments: Optional[List[Dict[str, str]]] = None
) -> List[Dict[str, any]]:
    """
    Send multiple emails at once.

    Args:
        emails: List of dicts with keys: recipient, subject, body
        attachments: Optional list of attachments shared by every email
            in the batch (dicts with keys: filename, content_base64, mime_type)

    Returns:
        List of results with keys: recipient, gmail_message_id, status, error
    """

    service, sender = get_gmail_service()
    results = []

    for email_data in emails:
        recipient = email_data.get("recipient")
        subject = email_data.get("subject")
        body = email_data.get("body")

        result = {
            "recipient": recipient,
            "status": "sent",
            "gmail_message_id": None,
            "thread_id": None,
            "error": None
        }

        try:
            message = _build_message(
                recipient, subject, body, attachments=attachments,
                pixel_url=email_data.get("pixel_url"),
                receipt_to=sender if email_data.get("request_receipt") else None
            )

            encoded_message = base64.urlsafe_b64encode(
                message.as_bytes()
            ).decode()

            send_result = service.users().messages().send(
                userId="me",
                body={"raw": encoded_message}
            ).execute()

            result["gmail_message_id"] = send_result.get("id")
            result["thread_id"] = send_result.get("threadId")

        except RefreshError as exc:
            result["status"] = "failed"
            result["error"] = f"Gmail account needs to be reconnected: {exc}"

        except Exception as exc:
            result["status"] = "failed"
            result["error"] = str(exc)

        results.append(result)

    return results


def get_message_id_header(gmail_message_id: str) -> Optional[str]:
    """Fetches the RFC822 'Message-ID' header for a message we sent, so a
    later follow-up can thread properly via In-Reply-To/References."""

    service, sender = get_gmail_service()

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


def thread_has_reply(thread_id: str, after: datetime) -> bool:
    """True if the thread contains any message received (INBOX-labeled,
    i.e. not one we sent) with an internal timestamp after `after`."""

    service, sender = get_gmail_service()

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
    thread_id: str,
    in_reply_to_header: Optional[str],
    recipient: str,
    subject: str,
    body: str
):
    """Sends a follow-up as a reply within the original thread."""

    service, sender = get_gmail_service()

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
            f"Gmail account '{sender}' needs to be reconnected: {exc}"
        ) from exc

    return result


_BOUNCE_SENDERS = ("mailer-daemon", "postmaster")
_BOUNCE_SUBJECTS = (
    "delivery status notification",
    "undeliverable",
    "delivery has failed",
    "mail delivery failed",
    "returned mail",
)


def get_thread_message_id(service, gmail_message_id: str) -> Optional[str]:
    """Resolves a Gmail message id to its thread id (backfill for old rows)."""
    msg = service.users().messages().get(
        userId="me", id=gmail_message_id, format="minimal"
    ).execute()
    return msg.get("threadId")


_DSN_SUCCESS = ("(success)", "successfully delivered", "was delivered", "delivered:", "has been delivered")
_DSN_DELAY = ("(delay)", "delayed", "delivery incomplete", "warning:")
_AUTO_SUBJECTS = ("automatic reply", "auto:", "autoreply", "auto-reply", "out of office", "out-of-office")


def _classify(headers: dict, snippet: str) -> str:
    """bounce | dsn_ok | dsn_delay | read_receipt | auto_reply | reply"""
    sender = headers.get("from", "").lower()
    subject = headers.get("subject", "")
    sl = subject.lower()
    ctype = headers.get("content-type", "").lower()
    from_daemon = any(t in sender for t in _BOUNCE_SENDERS)

    if "report-type=disposition-notification" in ctype:
        return "read_receipt"
    if from_daemon or "report-type=delivery-status" in ctype or any(t in sl for t in _BOUNCE_SUBJECTS):
        if any(t in sl for t in _DSN_SUCCESS):
            return "dsn_ok"
        if any(t in sl for t in _DSN_DELAY):
            return "dsn_delay"
        return "bounce"
    auto = headers.get("auto-submitted", "").strip().lower()
    if (auto and auto != "no") or headers.get("precedence", "").lower() in ("auto_reply", "bulk", "junk") \
            or "x-autoreply" in headers or any(sl.startswith(t) for t in _AUTO_SUBJECTS):
        return "auto_reply"
    return "reply"


def inspect_thread(service, thread_id: str, after: datetime) -> Dict[str, object]:
    """Reads a thread we started and classifies inbound messages newer than
    `after` (naive UTC) into evidence:

      replied_at/replied_message_id   first *human* reply
      bounced_at/bounce_reason        delivery-failure notice
      delivered: [(when, source, id)] success DSN or auto-reply (out-of-office)
      opened:    [(when, 'read_receipt', id)] MDN read receipts

    Auto-replies are deliberately NOT counted as replies."""

    thread = service.users().threads().get(
        userId="me",
        id=thread_id,
        format="metadata",
        metadataHeaders=["From", "Subject", "Content-Type", "Auto-Submitted", "Precedence", "X-Autoreply"]
    ).execute()

    after_ms = int(after.replace(tzinfo=timezone.utc).timestamp() * 1000)
    out = {"replied_at": None, "replied_message_id": None,
           "bounced_at": None, "bounce_reason": None, "bounce_message_id": None,
           "delivered": [], "opened": []}

    for message in thread.get("messages", []):
        labels = message.get("labelIds", []) or []
        internal = int(message.get("internalDate", "0"))

        if "SENT" in labels or internal <= after_ms:
            continue

        headers = {
            h.get("name", "").lower(): h.get("value", "")
            for h in message.get("payload", {}).get("headers", [])
        }
        when = datetime.fromtimestamp(internal / 1000, tz=timezone.utc).replace(tzinfo=None)
        mid = message.get("id", "")
        kind = _classify(headers, message.get("snippet", ""))

        if kind == "bounce":
            if out["bounced_at"] is None:
                out["bounced_at"] = when
                out["bounce_reason"] = (message.get("snippet") or headers.get("subject", ""))[:500]
                out["bounce_message_id"] = mid
        elif kind == "dsn_ok":
            out["delivered"].append((when, "dsn", mid))
        elif kind == "auto_reply":
            out["delivered"].append((when, "auto_reply", mid))
        elif kind == "read_receipt":
            out["opened"].append((when, "read_receipt", mid))
        elif kind == "reply" and out["replied_at"] is None:
            out["replied_at"] = when
            out["replied_message_id"] = mid

    return out
