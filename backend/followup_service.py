import json
from datetime import datetime, timedelta

from apscheduler.triggers.date import DateTrigger

from backend.database import SessionLocal
from backend.models import FollowUpSequence
from backend.scheduler import scheduler
from backend.gmail_sender import (
    get_message_id_header,
    thread_has_reply,
    send_followup_email
)
from backend.gmail_auth import get_active_gmail_email, SenderNotAuthenticatedError
from backend.gemini_service import generate_followup_email


def register_followup_job(seq: FollowUpSequence) -> str:
    job_id = f"followup-{seq.id}"

    scheduler.add_job(
        process_followup_check,
        trigger=DateTrigger(run_date=seq.next_action_at, timezone="UTC"),
        args=[seq.id],
        id=job_id,
        replace_existing=True,
        max_instances=1
    )
    return job_id


def cancel_followup_job(job_id: str):
    if not job_id:
        return
    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass


def create_followup_sequence(
    db,
    *,
    source_type: str,
    source_email_id=None,
    source_scheduled_email_id=None,
    sender_email: str,
    recipient_email: str,
    recipient_name: str,
    subject: str,
    body: str,
    gmail_message_id: str,
    thread_id: str,
    sent_at: datetime,
    wait_hours: int,
    max_follow_ups: int,
    interval_hours: int
) -> FollowUpSequence:
    """Creates and activates a follow-up sequence for an email that was just
    sent (immediately or via the scheduler). Called right after a successful
    send, using that send's real Gmail message/thread id and the account it
    was sent from."""

    try:
        message_id_header = get_message_id_header(sender_email, gmail_message_id)
    except Exception:
        message_id_header = None

    seq = FollowUpSequence(
        source_type=source_type,
        source_email_id=source_email_id,
        source_scheduled_email_id=source_scheduled_email_id,
        recipient_email=recipient_email,
        recipient_name=recipient_name,
        original_subject=subject,
        original_body=body,
        thread_id=thread_id,
        original_message_id_header=message_id_header,
        original_sent_at=sent_at,
        wait_hours=wait_hours,
        max_follow_ups=max_follow_ups,
        interval_hours=interval_hours,
        status="waiting",
        next_action_at=sent_at + timedelta(hours=wait_hours)
    )
    db.add(seq)
    db.flush()

    # Commit before touching APScheduler's jobstore (separate SQLite
    # connection) - see schedule_routes.py for why holding an open write
    # transaction here causes "database is locked".
    db.commit()
    db.refresh(seq)

    seq.job_id = register_followup_job(seq)
    db.commit()
    db.refresh(seq)

    return seq


def process_followup_check(sequence_id: int):
    db = SessionLocal()
    try:
        seq = db.query(FollowUpSequence).filter(FollowUpSequence.id == sequence_id).first()

        if not seq or seq.status in ("cancelled", "stopped", "replied"):
            return

        seq.status = "due"
        seq.last_checked_at = datetime.utcnow()
        db.commit()

        # Resolved live (not fixed at sequence-creation time) so a later
        # account switch/disconnect is respected, per the "must use the
        # selected active Gmail account" requirement.
        try:
            active_email = get_active_gmail_email()
        except SenderNotAuthenticatedError as exc:
            seq.status = "failed"
            seq.last_error = str(exc)
            db.commit()
            return

        try:
            replied = thread_has_reply(active_email, seq.thread_id, after=seq.original_sent_at)
        except Exception as exc:
            seq.status = "failed"
            seq.last_error = str(exc)
            db.commit()
            return

        if replied:
            seq.status = "replied"
            seq.job_id = None
            db.commit()
            return

        if seq.follow_ups_sent >= seq.max_follow_ups:
            seq.status = "sent"
            seq.job_id = None
            db.commit()
            return

        previous = json.loads(seq.previous_followup_bodies or "[]")

        try:
            followup_body = generate_followup_email(
                recipient_name=seq.recipient_name or "there",
                original_subject=seq.original_subject,
                original_body=seq.original_body,
                previous_followups=previous,
                followup_number=seq.follow_ups_sent + 1,
                max_follow_ups=seq.max_follow_ups
            )

            send_followup_email(
                sender_email=active_email,
                thread_id=seq.thread_id,
                in_reply_to_header=seq.original_message_id_header,
                recipient=seq.recipient_email,
                subject=seq.original_subject,
                body=followup_body
            )

            seq.follow_ups_sent += 1
            previous.append(followup_body)
            seq.previous_followup_bodies = json.dumps(previous)
            seq.last_error = None

            if seq.follow_ups_sent >= seq.max_follow_ups:
                seq.status = "sent"
                seq.job_id = None
                db.commit()
            else:
                seq.status = "waiting"
                seq.next_action_at = datetime.utcnow() + timedelta(hours=seq.interval_hours)
                db.commit()

                new_job_id = register_followup_job(seq)
                seq.job_id = new_job_id
                db.commit()

        except Exception as exc:
            seq.status = "failed"
            seq.last_error = str(exc)
            db.commit()

    finally:
        db.close()
