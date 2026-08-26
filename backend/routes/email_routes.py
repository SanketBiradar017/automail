import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator
from googleapiclient.errors import HttpError

from backend.config import SENDER_EMAIL
from backend.gemini_service import generate_email
from backend.gmail_sender import send_email
from backend.gmail_auth import (
    get_gmail_service,
    get_sender_status,
    SenderMismatchError,
    SenderNotAuthenticatedError,
    GmailVerificationError
)


router = APIRouter(
    prefix="/api/email",
    tags=["Email"]
)


EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validate_recipient(value: str) -> str:
    if not EMAIL_REGEX.match(value):
        raise ValueError("Invalid recipient email address.")
    return value


# Request for generating an email
class EmailGenerateRequest(BaseModel):
    recipient: str
    recipient_name: str
    subject: str
    context: str

    _validate_recipient = field_validator("recipient")(_validate_recipient)


# Request for sending an already-generated email
class EmailSendRequest(BaseModel):
    recipient: str
    subject: str
    body: str

    _validate_recipient = field_validator("recipient")(_validate_recipient)


@router.post("/generate")
def generate(request: EmailGenerateRequest):

    try:
        email_body = generate_email(
            recipient_name=request.recipient_name,
            subject=request.subject,
            context=request.context
        )

    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Gemini email generation failed: {exc}"
        )

    if not email_body:
        raise HTTPException(
            status_code=502,
            detail="Gemini returned an empty email."
        )

    return {
        "success": True,
        "email": email_body
    }


@router.get("/sender")
def sender_status():

    if not SENDER_EMAIL:
        return {
            "configured_sender": None,
            "authenticated_sender": None,
            "authenticated": False
        }

    matches, authenticated_email = get_sender_status(SENDER_EMAIL)

    return {
        "configured_sender": SENDER_EMAIL,
        "authenticated_sender": authenticated_email if matches else None,
        "authenticated": matches
    }


@router.post("/connect-sender")
def connect_sender():

    if not SENDER_EMAIL:
        raise HTTPException(
            status_code=400,
            detail="SENDER_EMAIL is missing from .env"
        )

    try:
        _, authenticated_email = get_gmail_service(SENDER_EMAIL, allow_oauth=True)

    except SenderMismatchError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    except GmailVerificationError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Gmail OAuth authorization failed: {exc}"
        )

    return {
        "success": True,
        "sender_email": authenticated_email,
        "message": "Gmail account connected successfully"
    }


@router.post("/send")
def send(request: EmailSendRequest):

    if not request.body.strip():
        raise HTTPException(
            status_code=422,
            detail="Email body cannot be empty."
        )

    if not SENDER_EMAIL:
        raise HTTPException(
            status_code=500,
            detail="SENDER_EMAIL is missing from .env"
        )

    try:
        result = send_email(
            recipient=request.recipient,
            subject=request.subject,
            body=request.body
        )

    except SenderNotAuthenticatedError as exc:
        raise HTTPException(status_code=401, detail=str(exc))

    except SenderMismatchError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    except GmailVerificationError as exc:
        raise HTTPException(status_code=401, detail=str(exc))

    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Gmail credentials not found: {exc}"
        )

    except HttpError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Gmail API rejected the request: {exc}"
        )

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to send email: {exc}"
        )

    return {
        "success": True,
        "message": "Email sent successfully",
        "gmail_message_id": result.get("id")
    }
