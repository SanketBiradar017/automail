import base64
import json
import mimetypes
import os
from datetime import datetime, timezone as dt_timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from backend.database import DATABASE_URL, SessionLocal
from backend.models import ScheduledEmail, EmailCampaign, Email
from backend.gmail_sender import send_email
from backend.gmail_auth import SenderNotAuthenticatedError

scheduler = BackgroundScheduler(
    jobstores={"default": SQLAlchemyJobStore(url=DATABASE_URL, tablename="apscheduler_jobs")},
    job_defaults={"coalesce": True, "misfire_grace_time": 3600}
)


def start_scheduler():
    if not scheduler.running:
        scheduler.start()


def shutdown_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)


def _build_trigger(schedule: ScheduledEmail):
    tz = schedule.timezone or "UTC"
    end_date = schedule.recurrence_end_at

    if schedule.recurrence_type == "none":
        return DateTrigger(run_date=schedule.scheduled_at_utc, timezone="UTC")

    local_dt = schedule.scheduled_at_utc  # naive UTC; cron fields derived after conversion below
    # Convert stored UTC time to the target timezone to derive matching wall-clock fields
    from zoneinfo import ZoneInfo
    aware_utc = schedule.scheduled_at_utc.replace(tzinfo=dt_timezone.utc)
    local = aware_utc.astimezone(ZoneInfo(tz))

    if schedule.recurrence_type == "daily":
        return CronTrigger(hour=local.hour, minute=local.minute, timezone=tz, end_date=end_date)

    if schedule.recurrence_type == "weekly":
        return CronTrigger(
            day_of_week=local.weekday(),
            hour=local.hour,
            minute=local.minute,
            timezone=tz,
            end_date=end_date
        )

    if schedule.recurrence_type == "monthly":
        return CronTrigger(day=local.day, hour=local.hour, minute=local.minute, timezone=tz, end_date=end_date)

    if schedule.recurrence_type == "custom":
        interval_days = schedule.recurrence_interval_days or 1
        return IntervalTrigger(
            days=interval_days,
            start_date=schedule.scheduled_at_utc,
            end_date=end_date,
            timezone="UTC"
        )

    raise ValueError(f"Unknown recurrence_type: {schedule.recurrence_type}")


def register_job(schedule: ScheduledEmail) -> str:
    job_id = f"scheduled-email-{schedule.id}"
    trigger = _build_trigger(schedule)

    scheduler.add_job(
        execute_scheduled_email,
        trigger=trigger,
        args=[schedule.id],
        id=job_id,
        replace_existing=True,
        max_instances=1
    )
    return job_id


def cancel_job(job_id: str):
    if not job_id:
        return
    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass


def _load_attachments(schedule: ScheduledEmail):
    if not schedule.attachment_dir or not os.path.isdir(schedule.attachment_dir):
        return None

    attachments = []
    for filename in json.loads(schedule.attachment_names or "[]"):
        path = os.path.join(schedule.attachment_dir, filename)
        if not os.path.isfile(path):
            continue
        with open(path, "rb") as f:
            content_b64 = base64.b64encode(f.read()).decode()
        mime_type, _ = mimetypes.guess_type(filename)
        attachments.append({
            "filename": filename,
            "content_base64": content_b64,
            "mime_type": mime_type or "application/octet-stream"
        })
    return attachments or None


def execute_scheduled_email(schedule_id: int):
    db = SessionLocal()
    try:
        schedule = db.query(ScheduledEmail).filter(ScheduledEmail.id == schedule_id).first()

        if not schedule or schedule.status == "cancelled":
            return

        schedule.status = "processing"
        db.commit()

        attachments = _load_attachments(schedule)

        try:
            result = send_email(
                recipient=schedule.recipient_email,
                subject=schedule.subject,
                body=schedule.body,
                cc=schedule.cc,
                bcc=schedule.bcc,
                attachments=attachments
            )

            schedule.status = "sent"
            schedule.gmail_message_id = result.get("id")
            schedule.last_sent_at = datetime.utcnow()
            schedule.send_count = (schedule.send_count or 0) + 1
            schedule.last_error = None

            email_record = _record_history(db, schedule)

            if schedule.enable_followup:
                db.flush()
                from backend.followup_service import create_followup_sequence
                create_followup_sequence(
                    db,
                    source_type="scheduled",
                    source_email_id=email_record.id,
                    source_scheduled_email_id=schedule.id,
                    recipient_email=schedule.recipient_email,
                    recipient_name=schedule.recipient_name or "",
                    subject=schedule.subject,
                    body=schedule.body,
                    gmail_message_id=result.get("id"),
                    thread_id=result.get("threadId"),
                    sent_at=schedule.last_sent_at,
                    wait_hours=schedule.followup_wait_hours or 48,
                    max_follow_ups=schedule.followup_max or 2,
                    interval_hours=schedule.followup_interval_hours or 72
                )

        except SenderNotAuthenticatedError as exc:
            schedule.status = "failed"
            schedule.last_error = str(exc)

        except Exception as exc:
            schedule.status = "failed"
            schedule.last_error = str(exc)

        # A recurring series stays "scheduled" between occurrences so it keeps
        # showing under the Scheduled tab; only a one-time send keeps its
        # terminal sent/failed status.
        if schedule.is_recurring:
            job = scheduler.get_job(schedule.job_id) if schedule.job_id else None
            if job is not None and job.next_run_time is not None:
                schedule.status = "scheduled"
                schedule.scheduled_at_utc = job.next_run_time.astimezone(dt_timezone.utc).replace(tzinfo=None)

        db.commit()

    finally:
        db.close()


def _record_history(db, schedule: ScheduledEmail):
    campaign = EmailCampaign(
        name=f"Scheduled: {schedule.subject}"[:255],
        subject=schedule.subject,
        body_template=""
    )
    db.add(campaign)
    db.flush()

    email_record = Email(
        campaign_id=campaign.id,
        recipient_email=schedule.recipient_email,
        recipient_name=schedule.recipient_name or "",
        cc=schedule.cc,
        bcc=schedule.bcc,
        attachment_names=schedule.attachment_names,
        subject=schedule.subject,
        body=schedule.body,
        gmail_message_id=schedule.gmail_message_id,
        status="sent",
        sent_at=schedule.last_sent_at
    )
    db.add(email_record)
    db.flush()
    return email_record
