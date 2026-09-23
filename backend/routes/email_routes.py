import json
import re
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session
from googleapiclient.errors import HttpError

from backend.config import SENDER_EMAIL
from backend.gemini_service import generate_email
from backend.gmail_sender import send_email, send_bulk_emails
from backend.gmail_auth import (
    get_gmail_service,
    get_sender_status,
    SenderMismatchError,
    SenderNotAuthenticatedError,
    GmailVerificationError
)
from backend.database import get_db
from backend.models import EmailCampaign, Email
from backend.followup_service import create_followup_sequence


router = APIRouter(
    prefix="/api/email",
    tags=["Email"]
)


EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validate_recipient(value: str) -> str:
    if not EMAIL_REGEX.match(value):
        raise ValueError("Invalid recipient email address.")
    return value


def _validate_email_list(value: Optional[str]) -> Optional[str]:
    if not value or not value.strip():
        return None
    addresses = [addr.strip() for addr in value.split(",") if addr.strip()]
    for addr in addresses:
        if not EMAIL_REGEX.match(addr):
            raise ValueError(f"Invalid email address in CC/BCC: {addr}")
    return ", ".join(addresses)


class AttachmentItem(BaseModel):
    filename: str
    content_base64: str
    mime_type: Optional[str] = "application/octet-stream"


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
    recipient_name: Optional[str] = ""
    cc: Optional[str] = None
    bcc: Optional[str] = None
    subject: str
    body: str
    attachments: Optional[List[AttachmentItem]] = None

    enable_followup: bool = False
    followup_wait_hours: Optional[int] = 48
    followup_max: Optional[int] = 2
    followup_interval_hours: Optional[int] = 72

    _validate_recipient = field_validator("recipient")(_validate_recipient)
    _validate_cc = field_validator("cc")(_validate_email_list)
    _validate_bcc = field_validator("bcc")(_validate_email_list)


# Single email for bulk sending
class BulkEmailItem(BaseModel):
    recipient: str
    recipient_name: str
    subject: str
    body: str

    _validate_recipient = field_validator("recipient")(_validate_recipient)


# Request for bulk email sending
class BulkEmailSendRequest(BaseModel):
    campaign_name: str
    emails: List[BulkEmailItem]
    attachments: Optional[List[AttachmentItem]] = None

    @field_validator("emails")
    @classmethod
    def validate_emails(cls, v):
        if not v or len(v) == 0:
            raise ValueError("At least one email must be provided")
        if len(v) > 500:
            raise ValueError("Maximum 500 emails per campaign")
        return v


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
def send(request: EmailSendRequest, db: Session = Depends(get_db)):

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

    campaign = EmailCampaign(
        name=f"Single: {request.subject}"[:255],
        subject=request.subject,
        body_template=""
    )
    db.add(campaign)
    db.flush()

    attachments_payload = (
        [att.model_dump() for att in request.attachments]
        if request.attachments else None
    )

    email_record = Email(
        campaign_id=campaign.id,
        recipient_email=request.recipient,
        recipient_name=request.recipient_name or "",
        cc=request.cc,
        bcc=request.bcc,
        attachment_names=(
            json.dumps([att.filename for att in request.attachments])
            if request.attachments else None
        ),
        subject=request.subject,
        body=request.body,
        status="pending"
    )
    db.add(email_record)
    db.flush()

    try:
        result = send_email(
            recipient=request.recipient,
            subject=request.subject,
            body=request.body,
            cc=request.cc,
            bcc=request.bcc,
            attachments=attachments_payload
        )

    except SenderNotAuthenticatedError as exc:
        db.rollback()
        raise HTTPException(status_code=401, detail=str(exc))

    except SenderMismatchError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc))

    except GmailVerificationError as exc:
        db.rollback()
        raise HTTPException(status_code=401, detail=str(exc))

    except FileNotFoundError as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Gmail credentials not found: {exc}"
        )

    except HttpError as exc:
        email_record.status = "failed"
        email_record.error_message = str(exc)
        db.commit()
        raise HTTPException(
            status_code=502,
            detail=f"Gmail API rejected the request: {exc}"
        )

    except Exception as exc:
        email_record.status = "failed"
        email_record.error_message = str(exc)
        db.commit()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to send email: {exc}"
        )

    email_record.status = "sent"
    email_record.gmail_message_id = result.get("id")
    email_record.sent_at = datetime.utcnow()
    db.commit()
    db.refresh(email_record)

    if request.enable_followup:
        create_followup_sequence(
            db,
            source_type="send",
            source_email_id=email_record.id,
            source_scheduled_email_id=None,
            recipient_email=request.recipient,
            recipient_name=request.recipient_name or "",
            subject=request.subject,
            body=request.body,
            gmail_message_id=result.get("id"),
            thread_id=result.get("threadId"),
            sent_at=email_record.sent_at,
            wait_hours=request.followup_wait_hours or 48,
            max_follow_ups=request.followup_max or 2,
            interval_hours=request.followup_interval_hours or 72
        )

    return {
        "success": True,
        "message": "Email sent successfully",
        "gmail_message_id": result.get("id")
    }


@router.post("/send-bulk")
def send_bulk(
    request: BulkEmailSendRequest,
    db: Session = Depends(get_db)
):
    if not SENDER_EMAIL:
        raise HTTPException(
            status_code=500,
            detail="SENDER_EMAIL is missing from .env"
        )

    try:
        campaign = EmailCampaign(
            name=request.campaign_name,
            subject=request.emails[0].subject if request.emails else "",
            body_template=""
        )
        db.add(campaign)
        db.flush()

        attachment_names_json = (
            json.dumps([att.filename for att in request.attachments])
            if request.attachments else None
        )
        attachments_payload = (
            [att.model_dump() for att in request.attachments]
            if request.attachments else None
        )

        emails_to_send = []
        email_records = []

        for email_item in request.emails:
            emails_to_send.append({
                "recipient": email_item.recipient,
                "subject": email_item.subject,
                "body": email_item.body
            })

            email_record = Email(
                campaign_id=campaign.id,
                recipient_email=email_item.recipient,
                recipient_name=email_item.recipient_name,
                attachment_names=attachment_names_json,
                subject=email_item.subject,
                body=email_item.body,
                status="pending"
            )
            email_records.append(email_record)
            db.add(email_record)

        db.flush()

        results = send_bulk_emails(emails_to_send, attachments=attachments_payload)

        for i, result in enumerate(results):
            email_records[i].status = result["status"]
            email_records[i].gmail_message_id = result["gmail_message_id"]
            if result["error"]:
                email_records[i].error_message = result["error"]
            if result["status"] == "sent":
                email_records[i].sent_at = datetime.utcnow()

        db.commit()

        sent_count = sum(1 for r in results if r["status"] == "sent")
        failed_count = len(results) - sent_count

        return {
            "success": True,
            "campaign_id": campaign.id,
            "campaign_name": campaign.name,
            "total_emails": len(results),
            "sent": sent_count,
            "failed": failed_count,
            "results": results
        }

    except SenderNotAuthenticatedError as exc:
        db.rollback()
        raise HTTPException(status_code=401, detail=str(exc))

    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Bulk email send failed: {exc}"
        )


@router.get("/campaigns")
def list_campaigns(db: Session = Depends(get_db)):
    campaigns = db.query(EmailCampaign).all()
    return {
        "success": True,
        "campaigns": [
            {
                "id": c.id,
                "name": c.name,
                "subject": c.subject,
                "created_at": c.created_at,
                "email_count": len(c.emails),
                "sent_count": sum(1 for e in c.emails if e.status == "sent"),
                "failed_count": sum(1 for e in c.emails if e.status == "failed")
            }
            for c in campaigns
        ]
    }


@router.get("/campaigns/{campaign_id}")
def get_campaign(campaign_id: int, db: Session = Depends(get_db)):
    campaign = db.query(EmailCampaign).filter(
        EmailCampaign.id == campaign_id
    ).first()

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    return {
        "success": True,
        "campaign": {
            "id": campaign.id,
            "name": campaign.name,
            "subject": campaign.subject,
            "created_at": campaign.created_at,
            "emails": [
                {
                    "id": e.id,
                    "recipient_email": e.recipient_email,
                    "recipient_name": e.recipient_name,
                    "cc": e.cc,
                    "bcc": e.bcc,
                    "attachment_names": json.loads(e.attachment_names) if e.attachment_names else [],
                    "subject": e.subject,
                    "body": e.body,
                    "status": e.status,
                    "sent_at": e.sent_at,
                    "error": e.error_message
                }
                for e in campaign.emails
            ]
        }
    }


@router.delete("/campaigns/{campaign_id}")
def delete_campaign(campaign_id: int, db: Session = Depends(get_db)):
    campaign = db.query(EmailCampaign).filter(
        EmailCampaign.id == campaign_id
    ).first()

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    db.delete(campaign)
    db.commit()

    return {
        "success": True,
        "message": f"Campaign '{campaign.name}' deleted successfully"
    }


@router.delete("/emails/{email_id}")
def delete_email(email_id: int, db: Session = Depends(get_db)):
    email = db.query(Email).filter(Email.id == email_id).first()

    if not email:
        raise HTTPException(status_code=404, detail="Email not found")

    db.delete(email)
    db.commit()

    return {
        "success": True,
        "message": "Email deleted from history"
    }
