"""Tables that only the admin panel needs. Imported by backend.main so
Base.metadata.create_all creates them."""
from datetime import datetime

from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, Integer, String, Text, Index)

from backend.database import Base


class AdminUser(Base):
    __tablename__ = "admin_users"
    id = Column(Integer, primary_key=True)
    email = Column(String(255), nullable=False, unique=True, index=True)
    name = Column(String(120), nullable=True)
    password_hash = Column(String(300), nullable=False)
    role = Column(String(20), nullable=False, default="support")   # super_admin | admin | support
    is_active = Column(Boolean, default=True, nullable=False)
    last_login_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class AppUser(Base):
    """An AutoMail customer = a Google account that has signed in. Kept even
    after the Gmail connection is removed, so history and billing survive."""
    __tablename__ = "app_users"
    id = Column(Integer, primary_key=True)
    email = Column(String(255), nullable=False, unique=True, index=True)
    name = Column(String(255), nullable=True)
    status = Column(String(20), nullable=False, default="active")   # active | suspended
    suspended_at = Column(DateTime, nullable=True)
    suspended_reason = Column(String(300), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    last_login_at = Column(DateTime, nullable=True)
    login_count = Column(Integer, default=0)
    gmail_disconnected_at = Column(DateTime, nullable=True)


class AIUsageLog(Base):
    __tablename__ = "ai_usage_logs"
    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True, nullable=False)
    feature = Column(String(40), nullable=False, index=True)
    model = Column(String(80), nullable=True)
    input_tokens = Column(Integer, default=0)
    output_tokens = Column(Integer, default=0)
    latency_ms = Column(Integer, default=0)
    success = Column(Boolean, default=True, nullable=False)
    error = Column(String(300), nullable=True)       # redacted, never the prompt or the reply
    user_email = Column(String(255), nullable=True, index=True)


class Plan(Base):
    __tablename__ = "plans"
    id = Column(Integer, primary_key=True)
    name = Column(String(80), nullable=False, unique=True)
    price_cents = Column(Integer, nullable=False, default=0)
    currency = Column(String(3), nullable=False, default="USD")
    interval = Column(String(10), nullable=False, default="month")   # month | year
    email_limit = Column(Integer, nullable=True)                     # per interval, null = unlimited
    features = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Subscription(Base):
    __tablename__ = "subscriptions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False, index=True)
    plan_id = Column(Integer, ForeignKey("plans.id"), nullable=False, index=True)
    status = Column(String(20), nullable=False, default="active")    # active | canceled | past_due
    started_at = Column(DateTime, default=datetime.utcnow, index=True)
    current_period_end = Column(DateTime, nullable=True)
    canceled_at = Column(DateTime, nullable=True)
    cancel_reason = Column(String(300), nullable=True)


class Payment(Base):
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False, index=True)
    subscription_id = Column(Integer, ForeignKey("subscriptions.id", ondelete="SET NULL"), nullable=True)
    amount_cents = Column(Integer, nullable=False)
    currency = Column(String(3), nullable=False, default="USD")
    status = Column(String(20), nullable=False, default="succeeded")  # succeeded | failed | refunded
    method = Column(String(30), nullable=True)
    reference = Column(String(120), nullable=True)
    note = Column(String(300), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class ErrorLog(Base):
    __tablename__ = "error_logs"
    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True, nullable=False)
    level = Column(String(10), nullable=False, index=True)
    source = Column(String(80), nullable=True)
    message = Column(Text, nullable=False)           # redacted
    path = Column(String(300), nullable=True)
    status_code = Column(Integer, nullable=True)
    exc_type = Column(String(120), nullable=True)
    trace = Column(Text, nullable=True)              # redacted, truncated


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True, nullable=False)
    admin_id = Column(Integer, nullable=True)
    admin_email = Column(String(255), nullable=True, index=True)
    action = Column(String(60), nullable=False, index=True)
    target_type = Column(String(30), nullable=True)
    target_id = Column(String(60), nullable=True)
    detail = Column(Text, nullable=True)
    ip = Column(String(64), nullable=True)


class AdminNotification(Base):
    __tablename__ = "admin_notifications"
    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True, nullable=False)
    kind = Column(String(40), nullable=False)
    severity = Column(String(10), nullable=False, default="info")   # info | warning | critical
    title = Column(String(200), nullable=False)
    body = Column(Text, nullable=True)
    dedupe_key = Column(String(120), nullable=True, index=True)
    is_read = Column(Boolean, default=False, nullable=False, index=True)
    read_at = Column(DateTime, nullable=True)
