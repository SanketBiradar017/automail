from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from backend.database import Base


class GmailAccount(Base):
    __tablename__ = "gmail_accounts"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    display_name = Column(String(255), nullable=True)

    # Serialized google.oauth2.credentials.Credentials (to_json()). Never
    # returned by any API response - routes only ever expose id/email/
    # display_name/status/timestamps.
    token_json = Column(Text, nullable=False)

    is_active = Column(Boolean, default=False)

    # connected, needs_reconnect
    status = Column(String(20), nullable=False, default="connected")
    last_error = Column(Text, nullable=True)

    connected_at = Column(DateTime, default=datetime.utcnow)
    last_verified_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class EmailCampaign(Base):
    __tablename__ = "email_campaigns"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    subject = Column(String(500), nullable=False)
    body_template = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = Column(Boolean, default=True)

    emails = relationship("Email", back_populates="campaign", cascade="all, delete-orphan")


class Email(Base):
    __tablename__ = "emails"

    id = Column(Integer, primary_key=True, index=True)
    campaign_id = Column(Integer, ForeignKey("email_campaigns.id"), nullable=False)
    recipient_email = Column(String(255), nullable=False)
    recipient_name = Column(String(255), nullable=False)
    cc = Column(String(1000), nullable=True)
    bcc = Column(String(1000), nullable=True)
    attachment_names = Column(Text, nullable=True)  # JSON list of filenames
    subject = Column(String(500), nullable=False)
    body = Column(Text, nullable=False)
    gmail_message_id = Column(String(255), nullable=True)
    status = Column(String(50), default="pending")  # pending, sent, failed
    sent_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    campaign = relationship("EmailCampaign", back_populates="emails")


class ScheduledEmail(Base):
    __tablename__ = "scheduled_emails"

    id = Column(Integer, primary_key=True, index=True)

    recipient_email = Column(String(255), nullable=False)
    recipient_name = Column(String(255), nullable=True)
    cc = Column(String(1000), nullable=True)
    bcc = Column(String(1000), nullable=True)
    subject = Column(String(500), nullable=False)
    body = Column(Text, nullable=False)

    attachment_names = Column(Text, nullable=True)   # JSON list of filenames
    attachment_dir = Column(String(500), nullable=True)  # disk path holding attachment files

    timezone = Column(String(100), nullable=False, default="UTC")
    scheduled_at_utc = Column(DateTime, nullable=True)  # next fire time, UTC. Null while draft.

    recurrence_type = Column(String(20), nullable=False, default="none")  # none, daily, weekly, monthly, custom
    recurrence_interval_days = Column(Integer, nullable=True)  # used when recurrence_type == custom
    recurrence_end_at = Column(DateTime, nullable=True)  # UTC, optional
    is_recurring = Column(Boolean, default=False)

    # draft, scheduled, pending, processing, sent, failed, cancelled
    status = Column(String(20), nullable=False, default="draft")

    job_id = Column(String(100), nullable=True)  # APScheduler job id
    send_count = Column(Integer, default=0)
    last_sent_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    gmail_message_id = Column(String(255), nullable=True)

    # Follow-up automation, requested at schedule-creation time but only
    # acted on once this scheduled email actually sends (see
    # backend/scheduler.py: execute_scheduled_email -> creates a
    # FollowUpSequence at that point, using the real send's thread/message id).
    enable_followup = Column(Boolean, default=False)
    followup_max = Column(Integer, nullable=True)
    followup_wait_hours = Column(Integer, nullable=True)
    followup_interval_hours = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class FollowUpSequence(Base):
    __tablename__ = "followup_sequences"

    id = Column(Integer, primary_key=True, index=True)

    # Where this sequence came from - informational only, no FK/cascade since
    # the source email/schedule row can outlive or be deleted independently
    # of the follow-up automation tracking it.
    source_type = Column(String(20), nullable=False, default="send")  # send, scheduled
    source_email_id = Column(Integer, nullable=True)
    source_scheduled_email_id = Column(Integer, nullable=True)

    recipient_email = Column(String(255), nullable=False)
    recipient_name = Column(String(255), nullable=True)
    original_subject = Column(String(500), nullable=False)
    original_body = Column(Text, nullable=False)

    thread_id = Column(String(255), nullable=False)
    original_message_id_header = Column(String(500), nullable=True)  # RFC822 Message-ID, for In-Reply-To/References
    original_sent_at = Column(DateTime, nullable=False)

    wait_hours = Column(Integer, nullable=False, default=48)
    max_follow_ups = Column(Integer, nullable=False, default=2)
    interval_hours = Column(Integer, nullable=False, default=72)

    follow_ups_sent = Column(Integer, default=0)
    previous_followup_bodies = Column(Text, nullable=True)  # JSON list, most recent last

    # waiting, due, sent, replied, stopped, failed, cancelled, paused
    status = Column(String(20), nullable=False, default="waiting")

    job_id = Column(String(100), nullable=True)  # APScheduler job id for the next check/send
    next_action_at = Column(DateTime, nullable=True)  # UTC
    last_checked_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
