"""Profile photos for the connected user and for people in their mail.

Sources (all authorised Google APIs, no third-party lookups):
  * the connected account's own photo: OIDC userinfo `picture`
  * other people: People API (contacts + "other contacts"), which needs the
    optional contacts scopes. Without them senders simply get initials.

Photos are fetched server-side and served from /api/avatars/photo, so the
browser never talks to Google with the user's identity and no OAuth token
ever leaves the backend. Results (including "no photo") are cached.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from email.utils import parseaddr
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse

import requests

from backend.gmail_auth import (
    SenderNotAuthenticatedError,
    get_account_profile,
    get_valid_credentials,
    refresh_account_profile,
)

CONTACTS = "https://www.googleapis.com/auth/contacts.readonly"
OTHER_CONTACTS = "https://www.googleapis.com/auth/contacts.other.readonly"

POSITIVE_TTL = 24 * 3600    # photo URLs can expire; re-resolve daily
NEGATIVE_TTL = 6 * 3600     # "no photo" / API error: retry a few times a day
ERROR_TTL = 15 * 60
IMAGE_CACHE_MAX = 300
MAX_IMAGE_BYTES = 1_000_000
ALLOWED_HOSTS = ("googleusercontent.com", "ggpht.com")

_lock = threading.Lock()
_urls: Dict[str, Tuple[Optional[str], float]] = {}          # email -> (url|None, expires_at)
_images: Dict[str, Tuple[bytes, str, float]] = {}           # email -> (bytes, ctype, expires_at)
_warmed_for: Optional[str] = None


def normalize(value: str) -> str:
    return parseaddr(value or "")[1].strip().lower()


def _host_ok(url: str) -> bool:
    p = urlparse(url)
    return p.scheme == "https" and any(
        p.hostname == h or (p.hostname or "").endswith("." + h) for h in ALLOWED_HOSTS
    )


def _sized(url: str, size: int = 128) -> str:
    # Google photo URLs end in =sNN[-c]; ask for a crisp square instead.
    base = url.split("=s")[0] if "=s" in url else url
    return f"{base}=s{size}-c"


def _people_photo(token: str, endpoint: str, email: str) -> Optional[str]:
    resp = requests.get(
        endpoint,
        params={"query": email, "readMask": "photos,emailAddresses", "pageSize": 5},
        headers={"Authorization": f"Bearer {token}"},
        timeout=8,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"People API {resp.status_code}")
    for hit in resp.json().get("results", []):
        person = hit.get("person", {})
        addrs = {a.get("value", "").lower() for a in person.get("emailAddresses", [])}
        if email not in addrs:
            continue
        for photo in person.get("photos", []):
            # `default` is Google's generic silhouette: treat as "no photo".
            if photo.get("url") and not photo.get("default") and _host_ok(photo["url"]):
                return photo["url"]
    return None


def _warm(token: str, account: str):
    """The People search endpoints return empty on the very first call for a
    user until a warm-up request has been made."""
    global _warmed_for
    if _warmed_for == account:
        return
    for ep in ("https://people.googleapis.com/v1/people:searchContacts",
               "https://people.googleapis.com/v1/otherContacts:search"):
        try:
            requests.get(ep, params={"query": "", "readMask": "photos"},
                         headers={"Authorization": f"Bearer {token}"}, timeout=8)
        except requests.RequestException:
            pass
    _warmed_for = account


def _lookup(email: str, creds, account: str, scopes: set) -> Tuple[Optional[str], float]:
    now = time.time()

    if email == account:
        profile = get_account_profile() or {}
        return profile.get("picture"), now + POSITIVE_TTL

    if not (scopes & {CONTACTS, OTHER_CONTACTS}):
        return None, now + NEGATIVE_TTL

    try:
        _warm(creds.token, account)
        if CONTACTS in scopes:
            url = _people_photo(creds.token, "https://people.googleapis.com/v1/people:searchContacts", email)
            if url:
                return url, now + POSITIVE_TTL
        if OTHER_CONTACTS in scopes:
            url = _people_photo(creds.token, "https://people.googleapis.com/v1/otherContacts:search", email)
            if url:
                return url, now + POSITIVE_TTL
        return None, now + NEGATIVE_TTL
    except Exception:
        return None, now + ERROR_TTL


def photo_url_for(email: str) -> Optional[str]:
    with _lock:
        hit = _urls.get(email)
    if hit and hit[1] > time.time():
        return hit[0]
    return None


def resolve(emails) -> Dict[str, bool]:
    """email -> whether a photo is available. Looks up uncached addresses in
    parallel; never raises for API failures (those just mean "no photo")."""

    wanted = []
    for e in emails:
        n = normalize(e)
        if n and "@" in n and n not in wanted:
            wanted.append(n)
    wanted = wanted[:40]

    now = time.time()
    with _lock:
        missing = [e for e in wanted if e not in _urls or _urls[e][1] <= now]

    if missing:
        try:
            creds, account = get_valid_credentials()
        except SenderNotAuthenticatedError:
            return {e: False for e in wanted}
        profile = get_account_profile() or {}
        scopes = profile.get("scopes") or set()

        # Backfill our own photo once if it was never stored.
        if account in missing and not profile.get("picture"):
            try:
                refresh_account_profile()
            except Exception:
                pass

        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda e: (e, _lookup(e, creds, account, scopes)), missing))
        with _lock:
            for e, val in results:
                _urls[e] = val

    with _lock:
        return {e: bool(_urls.get(e, (None, 0))[0]) for e in wanted}


def photo_bytes(email: str) -> Optional[Tuple[bytes, str]]:
    n = normalize(email)
    if not n:
        return None
    now = time.time()
    with _lock:
        cached = _images.get(n)
    if cached and cached[2] > now:
        return cached[0], cached[1]

    url = photo_url_for(n)
    if url is None:
        resolve([n])
        url = photo_url_for(n)
    if not url or not _host_ok(url):
        return None

    try:
        r = requests.get(_sized(url), timeout=8)
        ctype = r.headers.get("content-type", "")
        if r.status_code != 200 or not ctype.startswith("image/") or len(r.content) > MAX_IMAGE_BYTES:
            raise RuntimeError("bad photo response")
    except Exception:
        # Expired/invalid URL: drop it so the next resolve() refetches.
        with _lock:
            _urls.pop(n, None)
        return None

    with _lock:
        if len(_images) >= IMAGE_CACHE_MAX:
            _images.pop(next(iter(_images)))
        _images[n] = (r.content, ctype, now + POSITIVE_TTL)
    return r.content, ctype


def clear_cache():
    global _warmed_for
    with _lock:
        _urls.clear()
        _images.clear()
    _warmed_for = None
