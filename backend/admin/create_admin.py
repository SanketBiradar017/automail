"""python -m backend.admin.create_admin  -> create/reset an admin from the terminal."""
import getpass
import sys

from backend.database import SessionLocal, init_db
from backend.admin import models  # noqa: F401  (register tables)
from backend.admin.models import AdminUser
from backend.admin.security import ROLES, hash_password, validate_password_strength
from fastapi import HTTPException


def main():
    init_db()
    email = input("Admin email: ").strip().lower()
    role = (input(f"Role {ROLES} [super_admin]: ").strip().lower().replace(" ", "_").replace("-", "_") or "super_admin")
    if role not in ROLES:
        sys.exit("Invalid role")
    pw = getpass.getpass("Password (min 8 chars): ")
    try:
        validate_password_strength(pw)
    except HTTPException as exc:
        sys.exit(exc.detail)
    if pw != getpass.getpass("Repeat password: "):
        sys.exit("Passwords differ")
    db = SessionLocal()
    try:
        a = db.query(AdminUser).filter(AdminUser.email == email).first()
        if a:
            a.password_hash, a.role, a.is_active = hash_password(pw), role, True
        else:
            db.add(AdminUser(email=email, role=role, password_hash=hash_password(pw)))
        db.commit()
        print("saved", email, role)
    finally:
        db.close()


if __name__ == "__main__":
    main()
