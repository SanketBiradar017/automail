import base64
import json
import os
import re
import shutil
from datetime import datetime, timezone as dt_timezone
from typing import List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import ScheduledEmail
from backend.routes.email_routes import AttachmentItem, _validate_email_list
from backend.scheduler import register_job, cancel_job, execute_scheduled_email, scheduler


router = APIRouter(
    prefix="/api/schedule",
    tags=["Schedule"]
)


EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
RECURRENCE_TYPES = {"none", "daily", "weekly", "monthly", "custom"}
STATUSES = {"draft", "scheduled", "pending", "processing", "sent", "failed", "cancelled"}

ATTACHMENTS_ROOT = "scheduled_attachments"


class RecurrenceConfig(BaseModel):
    type: str = "none"
    interval_days: Optional[int] = None
    end_at: Optional[str] = None  # local ISO datetime string, same timezone as scheduled_at

    @field_validator("type")
    @classmethod
    def validate_type(cls, v):
        if v not in RECURRENCE_TYPES:
            raise ValueError(f"recurrence.type must be one of {sorted(RECURRENCE_TYPES)}")
        return v


class ScheduleCreateRequest(BaseModel):
    recipient: str
    recipient_name: Optional[str] = ""
    cc: Optional[str] = None
    bcc: Optional[str] = None
    subject: str
    body: str
    attachments: Optional[List[AttachmentItem]] = None
    timezone: str = "UTC"
    scheduled_at: Optional[str] = None  # local ISO datetime string, e.g. "2026-10-01T09:30"
    recurrence: Optional[RecurrenceConfig] = None
    save_as_draft: bool = False

    enable_followup: bool = False
    followup_wait_hours: Optional[int] = 48
    followup_max: Optional[int] = 2
    followup_interval_hours: Optional[int] = 72

    @field_validator("recipient")
    @classmethod
    def validate_recipient(cls, v):
        if not EMAIL_REGEX.match(v):
            raise ValueError("Invalid recipient email address.")
        return v

    _validate_cc = field_validator("cc")(_validate_email_list)
    _validate_bcc = field_validator("bcc")(_validate_email_list)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, v):
        try:
            ZoneInfo(v)
        except ZoneInfoNotFoundError:
            raise ValueError(f"Unknown timezone: {v}")
        return v


class ScheduleUpdateRequest(BaseModel):
    recipient: Optional[str] = None
    recipient_name: Optional[str] = None
    cc: Optional[str] = None
    bcc: Optional[str] = None
    subject: Optional[str] = None
    body: Optional[str] = None
    attachments: Optional[List[AttachmentItem]] = None
    timezone: Optional[str] = None
    scheduled_at: Optional[str] = None
    recurrence: Optional[RecurrenceConfig] = None
    save_as_draft: Optional[bool] = None

    enable_followup: Optional[bool] = None
    followup_wait_hours: Optional[int] = None
    followup_max: Optional[int] = None
    followup_interval_hours: Optional[int] = None

    _validate_cc = field_validator("cc")(_validate_email_list)
    _validate_bcc = field_validator("bcc")(_validate_email_list)


def _to_utc_naive(local_iso: str, tz_name: str) -> datetime:
    naive = datetime.fromisoformat(local_iso)
    aware = naive.replace(tzinfo=ZoneInfo(tz_name))
    return aware.astimezone(dt_timezone.utc).replace(tzinfo=None)


def _save_attachments(schedule_id: int, attachments: List[AttachmentItem]) -> tuple[str, str]:
    directory = os.path.join(ATTACHMENTS_ROOT, str(schedule_id))
    os.makedirs(directory, exist_ok=True)

    filenames = []
    for att in attachments:
        path = os.path.join(directory, att.filename)
        with open(path, "wb") as f:
            f.write(base64.b64decode(att.content_base64))
        filenames.append(att.filename)

    return directory, json.dumps(filenames)


def _clear_attachments(schedule: ScheduledEmail):
    if schedule.attachment_dir and os.path.isdir(schedule.attachment_dir):
        shutil.rmtree(schedule.attachment_dir, ignore_errors=True)
    schedule.attachment_dir = None
    schedule.attachment_names = None


def _serialize(s: ScheduledEmail) -> dict:
    return {
        "id": s.id,
        "recipient_email": s.recipient_email,
        "recipient_name": s.recipient_name,
        "cc": s.cc,
        "bcc": s.bcc,
        "subject": s.subject,
        "body": s.body,
        "attachment_names": json.loads(s.attachment_names) if s.attachment_names else [],
        "timezone": s.timezone,
        "scheduled_at_utc": s.scheduled_at_utc.isoformat() + "Z" if s.scheduled_at_utc else None,
        "recurrence_type": s.recurrence_type,
        "recurrence_interval_days": s.recurrence_interval_days,
        "recurrence_end_at": s.recurrence_end_at.isoformat() + "Z" if s.recurrence_end_at else None,
        "is_recurring": s.is_recurring,
        "status": s.status,
        "send_count": s.send_count,
        "last_sent_at": s.last_sent_at.isoformat() + "Z" if s.last_sent_at else None,
        "last_error": s.last_error,
        "gmail_message_id": s.gmail_message_id,
        "enable_followup": s.enable_followup,
        "followup_wait_hours": s.followup_wait_hours,
        "followup_max": s.followup_max,
        "followup_interval_hours": s.followup_interval_hours,
        "created_at": s.created_at.isoformat() + "Z" if s.created_at else None,
        "updated_at": s.updated_at.isoformat() + "Z" if s.updated_at else None,
    }


def _apply_schedule_fields(schedule: ScheduledEmail, request, db: Session):
    """Applies recurrence/scheduled_at/attachments from a create or update
    request onto `schedule`. Does not touch status or job registration.
    Simple content fields (recipient/subject/body/...) are applied by the
    caller since create vs. partial-update need different semantics."""

    if request.recurrence is not None:
        schedule.recurrence_type = request.recurrence.type
        schedule.is_recurring = request.recurrence.type != "none"
        schedule.recurrence_interval_days = request.recurrence.interval_days
        schedule.recurrence_end_at = (
            _to_utc_naive(request.recurrence.end_at, schedule.timezone)
            if request.recurrence.end_at else None
        )

    if request.scheduled_at is not None:
        schedule.scheduled_at_utc = _to_utc_naive(request.scheduled_at, schedule.timezone)

    if request.attachments is not None:
        _clear_attachments(schedule)
        db.flush()
        if request.attachments:
            directory, names_json = _save_attachments(schedule.id, request.attachments)
            schedule.attachment_dir = directory
            schedule.attachment_names = names_json


@router.post("")
def create_schedule(request: ScheduleCreateRequest, db: Session = Depends(get_db)):
    if not request.save_as_draft and not request.scheduled_at:
        raise HTTPException(status_code=422, detail="scheduled_at is required unless save_as_draft is true.")

    schedule = ScheduledEmail(
        recipient_email=request.recipient,
        recipient_name=request.recipient_name or "",
        cc=request.cc,
        bcc=request.bcc,
        subject=request.subject,
        body=request.body,
        timezone=request.timezone,
        enable_followup=request.enable_followup,
        followup_wait_hours=request.followup_wait_hours,
        followup_max=request.followup_max,
        followup_interval_hours=request.followup_interval_hours,
        status="draft"
    )
    db.add(schedule)
    db.flush()

    _apply_schedule_fields(schedule, request, db)
    schedule.status = "draft" if request.save_as_draft else "scheduled"

    # Commit our own write transaction before touching APScheduler's jobstore
    # (a separate SQLite connection) - otherwise the two writers deadlock
    # each other with "database is locked".
    db.commit()
    db.refresh(schedule)

    if not request.save_as_draft:
        schedule.job_id = register_job(schedule)
        db.commit()
        db.refresh(schedule)

    return {"success": True, "schedule": _serialize(schedule)}


@router.get("")
def list_schedules(status: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(ScheduledEmail)
    if status:
        if status not in STATUSES:
            raise HTTPException(status_code=422, detail=f"status must be one of {sorted(STATUSES)}")
        query = query.filter(ScheduledEmail.status == status)

    schedules = query.order_by(ScheduledEmail.scheduled_at_utc.asc().nullslast()).all()
    return {"success": True, "schedules": [_serialize(s) for s in schedules]}


@router.get("/{schedule_id}")
def get_schedule(schedule_id: int, db: Session = Depends(get_db)):
    schedule = db.query(ScheduledEmail).filter(ScheduledEmail.id == schedule_id).first()
    if not schedule:
        raise HTTPException(status_code=404, detail="Scheduled email not found")
    return {"success": True, "schedule": _serialize(schedule)}


@router.put("/{schedule_id}")
def update_schedule(schedule_id: int, request: ScheduleUpdateRequest, db: Session = Depends(get_db)):
    schedule = db.query(ScheduledEmail).filter(ScheduledEmail.id == schedule_id).first()
    if not schedule:
        raise HTTPException(status_code=404, detail="Scheduled email not found")

    if schedule.status in ("processing", "sent", "cancelled"):
        raise HTTPException(status_code=409, detail=f"Cannot edit a schedule with status '{schedule.status}'.")

    # Recipient/cc/bcc validation happens via Pydantic on the request already
    # for the fields it received; re-validate recipient format manually since
    # ScheduleUpdateRequest allows partial updates.
    if request.recipient is not None and not EMAIL_REGEX.match(request.recipient):
        raise HTTPException(status_code=422, detail="Invalid recipient email address.")

    if request.timezone is not None:
        try:
            ZoneInfo(request.timezone)
        except ZoneInfoNotFoundError:
            raise HTTPException(status_code=422, detail=f"Unknown timezone: {request.timezone}")

    was_scheduled = schedule.status == "scheduled" and schedule.job_id
    if was_scheduled:
        cancel_job(schedule.job_id)
        schedule.job_id = None

    fields_set = request.model_fields_set
    if "recipient" in fields_set:
        schedule.recipient_email = request.recipient
    if "recipient_name" in fields_set:
        schedule.recipient_name = request.recipient_name
    if "cc" in fields_set:
        schedule.cc = request.cc
    if "bcc" in fields_set:
        schedule.bcc = request.bcc
    if "subject" in fields_set:
        schedule.subject = request.subject
    if "body" in fields_set:
        schedule.body = request.body
    if "timezone" in fields_set:
        schedule.timezone = request.timezone
    if "enable_followup" in fields_set:
        schedule.enable_followup = request.enable_followup
    if "followup_wait_hours" in fields_set:
        schedule.followup_wait_hours = request.followup_wait_hours
    if "followup_max" in fields_set:
        schedule.followup_max = request.followup_max
    if "followup_interval_hours" in fields_set:
        schedule.followup_interval_hours = request.followup_interval_hours

    _apply_schedule_fields(schedule, request, db)

    target_draft = request.save_as_draft if request.save_as_draft is not None else (schedule.status == "draft")

    if target_draft:
        schedule.status = "draft"
    elif not schedule.scheduled_at_utc:
        raise HTTPException(status_code=422, detail="scheduled_at is required to activate this schedule.")
    else:
        schedule.status = "scheduled"

    # Commit our own write transaction before touching APScheduler's jobstore
    # (a separate SQLite connection) - otherwise the two writers deadlock
    # each other with "database is locked".
    db.commit()
    db.refresh(schedule)

    if not target_draft:
        schedule.job_id = register_job(schedule)
        db.commit()
        db.refresh(schedule)

    return {"success": True, "schedule": _serialize(schedule)}


@router.post("/{schedule_id}/cancel")
def cancel_schedule(schedule_id: int, db: Session = Depends(get_db)):
    schedule = db.query(ScheduledEmail).filter(ScheduledEmail.id == schedule_id).first()
    if not schedule:
        raise HTTPException(status_code=404, detail="Scheduled email not found")

    if schedule.job_id:
        cancel_job(schedule.job_id)
        schedule.job_id = None

    schedule.status = "cancelled"
    db.commit()
    db.refresh(schedule)

    return {"success": True, "schedule": _serialize(schedule)}


@router.post("/{schedule_id}/retry")
def retry_schedule(schedule_id: int, db: Session = Depends(get_db)):
    schedule = db.query(ScheduledEmail).filter(ScheduledEmail.id == schedule_id).first()
    if not schedule:
        raise HTTPException(status_code=404, detail="Scheduled email not found")

    if schedule.status != "failed":
        raise HTTPException(status_code=409, detail="Only failed schedules can be retried.")

    db.commit()  # release row lock before the synchronous send below

    execute_scheduled_email(schedule.id)

    db.refresh(schedule)
    return {"success": True, "schedule": _serialize(schedule)}


@router.post("/{schedule_id}/duplicate")
def duplicate_schedule(schedule_id: int, db: Session = Depends(get_db)):
    original = db.query(ScheduledEmail).filter(ScheduledEmail.id == schedule_id).first()
    if not original:
        raise HTTPException(status_code=404, detail="Scheduled email not found")

    copy = ScheduledEmail(
        recipient_email=original.recipient_email,
        recipient_name=original.recipient_name,
        cc=original.cc,
        bcc=original.bcc,
        subject=original.subject,
        body=original.body,
        timezone=original.timezone,
        scheduled_at_utc=original.scheduled_at_utc,
        recurrence_type=original.recurrence_type,
        recurrence_interval_days=original.recurrence_interval_days,
        recurrence_end_at=original.recurrence_end_at,
        is_recurring=original.is_recurring,
        status="draft"
    )
    db.add(copy)
    db.flush()

    if original.attachment_dir and os.path.isdir(original.attachment_dir):
        new_dir = os.path.join(ATTACHMENTS_ROOT, str(copy.id))
        shutil.copytree(original.attachment_dir, new_dir)
        copy.attachment_dir = new_dir
        copy.attachment_names = original.attachment_names

    db.commit()
    db.refresh(copy)

    return {"success": True, "schedule": _serialize(copy)}


@router.delete("/{schedule_id}")
def delete_schedule(schedule_id: int, db: Session = Depends(get_db)):
    schedule = db.query(ScheduledEmail).filter(ScheduledEmail.id == schedule_id).first()
    if not schedule:
        raise HTTPException(status_code=404, detail="Scheduled email not found")

    if schedule.job_id:
        cancel_job(schedule.job_id)

    _clear_attachments(schedule)

    db.delete(schedule)
    db.commit()

    return {"success": True, "message": "Scheduled email deleted"}
