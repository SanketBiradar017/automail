"""Read-only inbox/thread access plus threaded reply sending for the AI Reply Assistant."""
import base64
import html
import re
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.utils import parseaddr
from typing import Dict, List, Optional

from google.auth.exceptions import RefreshError

from backend.gmail_auth import get_gmail_service, SenderNotAuthenticatedError

MAX_BODY_CHARS = 8000


def _headers(payload: dict) -> Dict[str, str]:
    return {h.get("name", "").lower(): h.get("value", "") for h in payload.get("headers", [])}


def _b64(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")


def _html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style).*?</\1>", "", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>", "\n", raw)
    return html.unescape(re.sub(r"<[^>]+>", "", raw))


def _attachment_names(payload: dict) -> List[str]:
    names = []

    def walk(part):
        if part.get("filename"):
            names.append(part["filename"])
        for sub in part.get("parts", []) or []:
            walk(sub)

    walk(payload)
    return names


def _extract_body(payload: dict) -> str:
    """Prefers text/plain; falls back to stripped text/html."""
    plain, rich = [], []

    def walk(part):
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if part.get("filename"):
            return  # attachment
        if mime == "text/plain" and data:
            plain.append(_b64(data))
        elif mime == "text/html" and data:
            rich.append(_html_to_text(_b64(data)))
        for sub in part.get("parts", []) or []:
            walk(sub)

    walk(payload)
    text = "\n".join(plain) if plain else "\n".join(rich)
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:MAX_BODY_CHARS]


def strip_quoted(text: str) -> str:
    """Drops quoted history ('> ...' lines and 'On ... wrote:' tails) so the
    model sees each message's own content once."""
    out = []
    for line in text.split("\n"):
        if re.match(r"^\s*On .+wrote:\s*$", line) or re.match(r"^-{2,}\s*Original Message", line, re.I):
            break
        if line.lstrip().startswith(">"):
            continue
        out.append(line)
    return "\n".join(out).strip()


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).replace(tzinfo=None).isoformat()


def list_threads(query: str = "in:inbox", max_results: int = 20, page_token: Optional[str] = None):
    """Returns (items, next_page_token) for any Gmail search query."""
    service, me = get_gmail_service()
    kwargs = {"userId": "me", "q": query, "maxResults": max_results}
    if page_token:
        kwargs["pageToken"] = page_token
    listing = service.users().threads().list(**kwargs).execute()

    items = []
    for t in listing.get("threads", []):
        th = service.users().threads().get(
            userId="me", id=t["id"], format="metadata",
            metadataHeaders=["From", "To", "Subject"],
        ).execute()
        msgs = th.get("messages", [])
        if not msgs:
            continue
        first, last = msgs[0], msgs[-1]
        last_h = _headers(last.get("payload", {}))
        first_h = _headers(first.get("payload", {}))
        last_labels = last.get("labelIds", []) or []
        items.append({
            "thread_id": t["id"],
            "subject": first_h.get("subject") or "(no subject)",
            "from": last_h.get("from", ""),
            "to": last_h.get("to", ""),
            "snippet": html.unescape(last.get("snippet", "")),
            "message_count": len(msgs),
            "last_at": _iso(int(last.get("internalDate", "0"))),
            "unread": any("UNREAD" in (m.get("labelIds") or []) for m in msgs),
            "starred": any("STARRED" in (m.get("labelIds") or []) for m in msgs),
            "awaiting_my_reply": "SENT" not in last_labels,
        })
    items.sort(key=lambda i: i["last_at"], reverse=True)
    return items, listing.get("nextPageToken")


def list_inbox(query: str = "", max_results: int = 20) -> List[dict]:
    return list_threads(f"in:inbox {query}".strip(), max_results)[0]


def get_thread(thread_id: str) -> dict:
    service, me = get_gmail_service()
    th = service.users().threads().get(userId="me", id=thread_id, format="full").execute()

    messages = []
    for m in th.get("messages", []):
        h = _headers(m.get("payload", {}))
        labels = m.get("labelIds", []) or []
        messages.append({
            "id": m["id"],
            "from": h.get("from", ""),
            "to": h.get("to", ""),
            "cc": h.get("cc", ""),
            "reply_to": h.get("reply-to", ""),
            "subject": h.get("subject", ""),
            "message_id": h.get("message-id", ""),
            "references": h.get("references", ""),
            "date": _iso(int(m.get("internalDate", "0"))),
            "is_me": "SENT" in labels,
            "attachments": _attachment_names(m.get("payload", {})),
            "body": _extract_body(m.get("payload", {})) or (m.get("snippet") or ""),
        })

    subject = next((m["subject"] for m in messages if m["subject"]), "(no subject)")
    return {"thread_id": thread_id, "subject": subject, "me": me, "messages": messages}


def thread_to_prompt_text(thread: dict, max_chars: int = 12000, max_messages: int = 12) -> str:
    """Chronological transcript; keeps the most recent messages if too long."""
    chunks = []
    for m in thread["messages"][-max_messages:]:
        who = "ME" if m["is_me"] else (parseaddr(m["from"])[0] or m["from"])
        chunks.append(f"[{m['date']}] FROM: {who}\n{strip_quoted(m['body'])}")
    text = "\n\n---\n\n".join(chunks)
    return text[-max_chars:]


def reply_target(thread: dict) -> Optional[dict]:
    """The last inbound message; we reply to its Reply-To (or From)."""
    inbound = [m for m in thread["messages"] if not m["is_me"]]
    if not inbound:
        return None
    last = inbound[-1]
    addr = parseaddr(last["reply_to"] or last["from"])[1]
    return {"to": addr, "message": last} if addr else None


def send_thread_reply(thread: dict, body: str) -> dict:
    target = reply_target(thread)
    if not target:
        raise ValueError("No incoming message in this thread to reply to.")

    last = target["message"]
    subject = last["subject"] or thread["subject"]
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"

    msg = MIMEText(body)
    msg["to"] = target["to"]
    msg["subject"] = subject
    if last["message_id"]:
        msg["In-Reply-To"] = last["message_id"]
        msg["References"] = f"{last['references']} {last['message_id']}".strip()

    service, sender = get_gmail_service()
    try:
        return service.users().messages().send(
            userId="me",
            body={"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode(),
                  "threadId": thread["thread_id"]},
        ).execute()
    except RefreshError as exc:
        raise SenderNotAuthenticatedError(
            f"Gmail account '{sender}' needs to be reconnected: {exc}") from exc
