import os
from cryptography.fernet import Fernet

from backend.config import TOKEN_ENCRYPTION_KEY, TOKEN_DIRECTORY

_fernet = None


def _load_key() -> bytes:
    if TOKEN_ENCRYPTION_KEY:
        return TOKEN_ENCRYPTION_KEY.encode()

    key_path = os.path.join(TOKEN_DIRECTORY, ".encryption_key")

    if os.path.exists(key_path):
        with open(key_path, "rb") as f:
            return f.read().strip()

    os.makedirs(TOKEN_DIRECTORY, exist_ok=True)
    key = Fernet.generate_key()
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    return key


def _get():
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_key())
    return _fernet


def encrypt(value: str) -> str:
    return _get().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    return _get().decrypt(value.encode()).decode()
