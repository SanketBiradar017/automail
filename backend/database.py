from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import declarative_base, sessionmaker
import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./mailgenie.db")

_IS_SQLITE = DATABASE_URL.startswith("sqlite")

engine = create_engine(
    DATABASE_URL,
    **({"connect_args": {"check_same_thread": False, "timeout": 15}} if _IS_SQLITE else {"pool_pre_ping": True})
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    Base.metadata.create_all(bind=engine)
    _migrate_add_missing_columns()


def _migrate_add_missing_columns():
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    tables_and_columns = {
        "emails": {
            "cc": "VARCHAR(1000)",
            "bcc": "VARCHAR(1000)",
            "attachment_names": "TEXT",
            "thread_id": "VARCHAR(255)",
            "replied_at": "DATETIME",
            "bounced_at": "DATETIME",
            "bounce_reason": "TEXT",
            "tracked_at": "DATETIME",
            "sender_email": "VARCHAR(255)",
            "delivered_at": "DATETIME",
            "delivered_source": "VARCHAR(30)",
            "opened_at": "DATETIME",
            "opened_source": "VARCHAR(30)",
            "replied_source": "VARCHAR(30)",
            "replied_message_id": "VARCHAR(255)",
            "open_tracking": "BOOLEAN DEFAULT 0",
            "track_token": "VARCHAR(64)",
            "status_updated_at": "DATETIME"
        },
        "followup_sequences": {
            "first_followup_at": "DATETIME"
        },
        "gmail_accounts": {
            "display_name": "VARCHAR(255)",
            "picture_url": "TEXT"
        },
        "scheduled_emails": {
            "enable_followup": "BOOLEAN",
            "followup_max": "INTEGER",
            "followup_wait_hours": "INTEGER",
            "followup_interval_hours": "INTEGER"
        }
    }

    with engine.connect() as conn:
        for table_name, new_cols in tables_and_columns.items():
            if table_name not in existing_tables:
                continue

            existing_cols = {col["name"] for col in inspector.get_columns(table_name)}

            for col_name, col_type in new_cols.items():
                if col_name not in existing_cols:
                    conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type}"))
        conn.commit()
