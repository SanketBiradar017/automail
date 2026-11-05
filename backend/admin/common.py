from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, Query

from backend import engagement_service as eng

RANGE = "^(today|7d|30d|90d|custom)$"


class Win:
    """Resolved date filter shared by every admin analytics endpoint."""
    def __init__(self, range_key: str, start: Optional[str], end: Optional[str], tz: str):
        try:
            if range_key == "90d":
                today = datetime.now(eng._tz(tz)).date()
                r = eng.resolve_window("custom", (today - timedelta(days=89)).isoformat(), today.isoformat(), tz)
            else:
                r = eng.resolve_window(range_key, start, end, tz)
        except eng.RangeError as exc:
            raise HTTPException(422, str(exc))
        self.start_utc, self.end_utc, self.first, self.last, self.tz = r
        self.key = range_key

    def meta(self):
        return {"key": self.key, "start": self.first.isoformat(), "end": self.last.isoformat(), "tz": str(self.tz)}

    def days(self):
        d = self.first
        while d <= self.last:
            yield d
            d += timedelta(days=1)

    def bucket(self, datetimes, cap: int = 200000) -> Counter:
        """Counts naive-UTC datetimes per local calendar day."""
        c = Counter()
        for i, dt in enumerate(datetimes):
            if i >= cap or dt is None:
                break
            c[dt.replace(tzinfo=timezone.utc).astimezone(self.tz).date()] += 1
        return c

    def series(self, **counters):
        return [{"date": d.isoformat(), **{k: v.get(d, 0) for k, v in counters.items()}} for d in self.days()]


def page_params(page: int = Query(1, ge=1, le=100000), page_size: int = Query(25, ge=1, le=100)):
    return page, page_size


def paginate(q, page: int, size: int, total: Optional[int] = None):
    total = q.count() if total is None else total
    items = q.limit(size).offset((page - 1) * size).all()
    return items, {"total": total, "page": page, "page_size": size, "pages": max(1, -(-total // size))}


def iso(d: Optional[datetime]):
    return d.isoformat() + "Z" if d else None


def like(s: str) -> str:
    return "%" + s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
