from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from googleapiclient.errors import HttpError
from pydantic import BaseModel, Field

from backend import gmail_inbox as inbox
from backend.gemini_service import analyze_thread, generate_reply, REPLY_TONES
from backend.gmail_auth import SenderNotAuthenticatedError

router = APIRouter(prefix="/api/replies", tags=["Reply Assistant"])


class DraftRequest(BaseModel):
    tone: str = "professional"
    instructions: str = Field("", max_length=600)
    questions: Optional[list[str]] = None


class SendRequest(BaseModel):
    body: str = Field(..., min_length=1, max_length=20000)


def _gmail_call(fn, *args):
    try:
        return fn(*args)
    except SenderNotAuthenticatedError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except HttpError as exc:
        status = 404 if getattr(exc.resp, "status", None) == 404 else 502
        raise HTTPException(status_code=status, detail=f"Gmail API error: {exc}")


def _ai(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/inbox")
def list_inbox(
    q: str = Query("", max_length=200),
    limit: int = Query(20, ge=1, le=50),
    needs_reply: bool = False,
):
    items = _gmail_call(inbox.list_inbox, q, limit)
    if needs_reply:
        items = [i for i in items if i["awaiting_my_reply"]]
    return {"success": True, "threads": items}


@router.get("/thread/{thread_id}")
def get_thread(thread_id: str):
    thread = _gmail_call(inbox.get_thread, thread_id)
    target = inbox.reply_target(thread)
    return {"success": True, "thread": thread, "reply_to": target["to"] if target else None}


@router.post("/thread/{thread_id}/analyze")
def analyze(thread_id: str):
    thread = _gmail_call(inbox.get_thread, thread_id)
    result = _ai(analyze_thread, thread["subject"], inbox.thread_to_prompt_text(thread))
    return {"success": True, **result}


@router.post("/thread/{thread_id}/draft")
def draft(thread_id: str, req: DraftRequest):
    if req.tone not in REPLY_TONES:
        raise HTTPException(status_code=422, detail=f"tone must be one of {sorted(REPLY_TONES)}")
    thread = _gmail_call(inbox.get_thread, thread_id)
    body = _ai(
        generate_reply, thread["subject"], inbox.thread_to_prompt_text(thread),
        req.tone, req.instructions, req.questions,
    )
    if not body:
        raise HTTPException(status_code=502, detail="Gemini returned an empty reply.")
    return {"success": True, "draft": body}


@router.post("/thread/{thread_id}/send")
def send(thread_id: str, req: SendRequest):
    """Only ever called by an explicit user click on the edited draft."""
    if not req.body.strip():
        raise HTTPException(status_code=422, detail="Reply body cannot be empty.")
    thread = _gmail_call(inbox.get_thread, thread_id)
    try:
        result = _gmail_call(inbox.send_thread_reply, thread, req.body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"success": True, "message": "Reply sent", "gmail_message_id": result.get("id")}
