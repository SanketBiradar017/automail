from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime
from backend.database import Base


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

    # Analytics tracking (filled by backend/analytics_service.py)
    thread_id = Column(String(255), nullable=True)
    replied_at = Column(DateTime, nullable=True)       # first inbound human reply, UTC
    bounced_at = Column(DateTime, nullable=True)       # delivery-failure notice, UTC
    bounce_reason = Column(Text, nullable=True)
    tracked_at = Column(DateTime, nullable=True)       # last time Gmail was checked

    # Status tracking (backend/email_status.py). A column is only ever set when
    # there is evidence for it; *_source records what that evidence was.
    sender_email = Column(String(255), nullable=True, index=True)   # connected account that sent it
    delivered_at = Column(DateTime, nullable=True)
    delivered_source = Column(String(30), nullable=True)   # dsn | reply | auto_reply | read_receipt | open_pixel
    opened_at = Column(DateTime, nullable=True)
    opened_source = Column(String(30), nullable=True)      # read_receipt | open_pixel
    replied_source = Column(String(30), nullable=True)     # thread_sync
    replied_message_id = Column(String(255), nullable=True)  # Gmail id of the first human reply
    open_tracking = Column(Boolean, default=False)         # sender opted in for this email
    track_token = Column(String(64), nullable=True, index=True)
    status_updated_at = Column(DateTime, nullable=True)

    campaign = relationship("EmailCampaign", back_populates="emails")
    events = relationship("EmailEvent", back_populates="email", cascade="all, delete-orphan")


class EmailEvent(Base):
    """Append-only log of status evidence. (email_id, event_type, external_id)
    is unique, so re-syncing or retrying can never double count an event."""
    __tablename__ = "email_events"
    __table_args__ = (UniqueConstraint("email_id", "event_type", "external_id", name="uq_email_event"),)

    id = Column(Integer, primary_key=True, index=True)
    email_id = Column(Integer, ForeignKey("emails.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type = Column(String(20), nullable=False, index=True)   # sent failed delivered opened replied bounced
    source = Column(String(30), nullable=False)                   # evidence source
    external_id = Column(String(255), nullable=False, default="")  # gmail message id / token hit, for dedupe
    occurred_at = Column(DateTime, nullable=False, index=True)    # naive UTC
    detail = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    email = relationship("Email", back_populates="events")


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
    first_followup_at = Column(DateTime, nullable=True)  # when follow-up #1 went out, UTC
    last_checked_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class GmailAccount(Base):
    """The currently connected Gmail account. At most one row; connecting a
    different account replaces it, disconnecting deletes it. Tokens are
    Fernet-encrypted at rest (see backend/token_crypto.py)."""
    __tablename__ = "gmail_accounts"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), nullable=False, unique=True)
    access_token_enc = Column(Text, nullable=False)
    refresh_token_enc = Column(Text, nullable=True)
    token_expiry = Column(DateTime, nullable=True)  # naive UTC
    scopes = Column(Text, nullable=True)  # space separated
    display_name = Column(String(255), nullable=True)
    picture_url = Column(Text, nullable=True)  # Google profile photo (public URL, not a secret)
    connected_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class EmailTemplate(Base):
    """A user-created template. Built-in templates live in code
    (backend/template_library.py) and are read-only. Ownership is the
    connected Gmail account's address (the app's user identity)."""
    __tablename__ = "email_templates"

    id = Column(Integer, primary_key=True, index=True)
    owner_email = Column(String(255), nullable=False, index=True)
    name = Column(String(120), nullable=False)
    category = Column(String(50), nullable=False, default="Other")
    subject = Column(String(300), nullable=False, default="")
    body = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TemplateFavorite(Base):
    """Per-user favourite. `ref` is "builtin:<key>" or "custom:<id>"."""
    __tablename__ = "template_favorites"

    id = Column(Integer, primary_key=True, index=True)
    owner_email = Column(String(255), nullable=False, index=True)
    ref = Column(String(100), nullable=False)
