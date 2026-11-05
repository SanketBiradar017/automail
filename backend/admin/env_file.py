"""Safe, minimal .env editing for the admin Settings page."""
import os
import re
import tempfile
from pathlib import Path

ENV_PATH = Path(os.getenv("ENV_FILE_PATH") or Path(__file__).resolve().parents[2] / ".env")
GOOGLE_KEY_RE = re.compile(r"^[A-Za-z0-9_\-]{20,200}$")   # no whitespace/quotes/newlines: nothing can inject extra .env lines


def valid_google_key(key: str) -> bool:
    return bool(GOOGLE_KEY_RE.match(key or ""))


def mask(key: str) -> str:
    return f"{key[:4]}{'•' * 8}{key[-4:]}" if key and len(key) >= 12 else ""


def set_env_var(name: str, value: str, path: Path = None) -> None:
    """Replaces `NAME=...` in place (keeping every other line, comment and
    ordering) or appends it. Atomic write; file mode 0600."""
    path = path or ENV_PATH
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", name) or any(c in value for c in "\r\n\0"):
        raise ValueError("invalid .env name or value")
    lines = path.read_text().splitlines() if path.exists() else []
    pat = re.compile(rf"^\s*(export\s+)?{re.escape(name)}\s*=")
    out, done = [], False
    for ln in lines:
        if pat.match(ln):
            if not done:
                out.append(f"{name}={value}")
                done = True              # drop duplicate definitions of the same key
        else:
            out.append(ln)
    if not done:
        if out and out[-1].strip():
            out.append("")
        out.append(f"{name}={value}")
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".env.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(out) + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
