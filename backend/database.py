from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import declarative_base, sessionmaker
import os

DATABASE_URL = "sqlite:///./mailgenie.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 15}
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
            "attachment_names": "TEXT"
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
