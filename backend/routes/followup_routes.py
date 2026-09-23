import json
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import FollowUpSequence
from backend.followup_service import (
    register_followup_job,
    cancel_followup_job,
    process_followup_check
)


router = APIRouter(
    prefix="/api/followups",
    tags=["Follow-ups"]
)


STATUSES = {"waiting", "due", "sent", "replied", "stopped", "failed", "cancelled"}
TERMINAL_STATUSES = {"replied", "cancelled"}


class FollowUpUpdateRequest(BaseModel):
    wait_hours: Optional[int] = None
    max_follow_ups: Optional[int] = None
    interval_hours: Optional[int] = None


def _serialize(seq: FollowUpSequence) -> dict:
    return {
        "id": seq.id,
        "source_type": seq.source_type,
        "source_email_id": seq.source_email_id,
        "source_scheduled_email_id": seq.source_scheduled_email_id,
        "recipient_email": seq.recipient_email,
        "recipient_name": seq.recipient_name,
        "original_subject": seq.original_subject,
        "original_body": seq.original_body,
        "thread_id": seq.thread_id,
        "original_sent_at": seq.original_sent_at.isoformat() + "Z" if seq.original_sent_at else None,
        "wait_hours": seq.wait_hours,
        "max_follow_ups": seq.max_follow_ups,
        "interval_hours": seq.interval_hours,
        "follow_ups_sent": seq.follow_ups_sent,
        "previous_followup_bodies": json.loads(seq.previous_followup_bodies) if seq.previous_followup_bodies else [],
        "status": seq.status,
        "next_action_at": seq.next_action_at.isoformat() + "Z" if seq.next_action_at else None,
        "last_checked_at": seq.last_checked_at.isoformat() + "Z" if seq.last_checked_at else None,
        "last_error": seq.last_error,
        "created_at": seq.created_at.isoformat() + "Z" if seq.created_at else None,
        "updated_at": seq.updated_at.isoformat() + "Z" if seq.updated_at else None,
    }


@router.get("")
def list_followups(status: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(FollowUpSequence)
    if status:
        if status not in STATUSES:
            raise HTTPException(status_code=422, detail=f"status must be one of {sorted(STATUSES)}")
        query = query.filter(FollowUpSequence.status == status)

    sequences = query.order_by(FollowUpSequence.next_action_at.asc().nullslast()).all()
    return {"success": True, "followups": [_serialize(s) for s in sequences]}


@router.get("/{followup_id}")
def get_followup(followup_id: int, db: Session = Depends(get_db)):
    seq = db.query(FollowUpSequence).filter(FollowUpSequence.id == followup_id).first()
    if not seq:
        raise HTTPException(status_code=404, detail="Follow-up sequence not found")
    return {"success": True, "followup": _serialize(seq)}


@router.put("/{followup_id}")
def update_followup(followup_id: int, request: FollowUpUpdateRequest, db: Session = Depends(get_db)):
    seq = db.query(FollowUpSequence).filter(FollowUpSequence.id == followup_id).first()
    if not seq:
        raise HTTPException(status_code=404, detail="Follow-up sequence not found")

    if seq.status in TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail=f"Cannot edit a follow-up with status '{seq.status}'.")

    if request.wait_hours is not None:
        seq.wait_hours = request.wait_hours
        # Only the still-pending first check is meaningfully rescheduled by
        # a wait_hours change - once follow-ups have started, timing is
        # driven by interval_hours instead.
        if seq.follow_ups_sent == 0 and seq.status == "waiting":
            seq.next_action_at = seq.original_sent_at + timedelta(hours=seq.wait_hours)
            db.commit()
            seq.job_id = register_followup_job(seq)

    if request.max_follow_ups is not None:
        seq.max_follow_ups = request.max_follow_ups

    if request.interval_hours is not None:
        seq.interval_hours = request.interval_hours

    db.commit()
    db.refresh(seq)

    return {"success": True, "followup": _serialize(seq)}


@router.post("/{followup_id}/pause")
def pause_followup(followup_id: int, db: Session = Depends(get_db)):
    seq = db.query(FollowUpSequence).filter(FollowUpSequence.id == followup_id).first()
    if not seq:
        raise HTTPException(status_code=404, detail="Follow-up sequence not found")

    if seq.status not in ("waiting", "due"):
        raise HTTPException(status_code=409, detail=f"Cannot pause a follow-up with status '{seq.status}'.")

    cancel_followup_job(seq.job_id)
    seq.job_id = None
    seq.status = "stopped"
    db.commit()
    db.refresh(seq)

    return {"success": True, "followup": _serialize(seq)}


@router.post("/{followup_id}/resume")
def resume_followup(followup_id: int, db: Session = Depends(get_db)):
    seq = db.query(FollowUpSequence).filter(FollowUpSequence.id == followup_id).first()
    if not seq:
        raise HTTPException(status_code=404, detail="Follow-up sequence not found")

    if seq.status != "stopped":
        raise HTTPException(status_code=409, detail="Only a paused (stopped) follow-up can be resumed.")

    now = datetime.utcnow()
    if not seq.next_action_at or seq.next_action_at < now:
        seq.next_action_at = now

    seq.status = "waiting"
    db.commit()

    seq.job_id = register_followup_job(seq)
    db.commit()
    db.refresh(seq)

    return {"success": True, "followup": _serialize(seq)}


@router.post("/{followup_id}/cancel")
def cancel_followup(followup_id: int, db: Session = Depends(get_db)):
    seq = db.query(FollowUpSequence).filter(FollowUpSequence.id == followup_id).first()
    if not seq:
        raise HTTPException(status_code=404, detail="Follow-up sequence not found")

    if seq.status in TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail=f"Follow-up is already '{seq.status}'.")

    cancel_followup_job(seq.job_id)
    seq.job_id = None
    seq.status = "cancelled"
    db.commit()
    db.refresh(seq)

    return {"success": True, "followup": _serialize(seq)}


@router.post("/{followup_id}/retry")
def retry_followup(followup_id: int, db: Session = Depends(get_db)):
    seq = db.query(FollowUpSequence).filter(FollowUpSequence.id == followup_id).first()
    if not seq:
        raise HTTPException(status_code=404, detail="Follow-up sequence not found")

    if seq.status != "failed":
        raise HTTPException(status_code=409, detail="Only a failed follow-up can be retried.")

    db.commit()  # release row lock before the synchronous check/send below

    process_followup_check(seq.id)

    db.refresh(seq)
    return {"success": True, "followup": _serialize(seq)}


@router.delete("/{followup_id}")
def delete_followup(followup_id: int, db: Session = Depends(get_db)):
    seq = db.query(FollowUpSequence).filter(FollowUpSequence.id == followup_id).first()
    if not seq:
        raise HTTPException(status_code=404, detail="Follow-up sequence not found")

    cancel_followup_job(seq.job_id)

    db.delete(seq)
    db.commit()

    return {"success": True, "message": "Follow-up sequence deleted"}
