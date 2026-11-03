from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from backend.database import get_db
from backend import analytics_service as svc
from backend import engagement_service as eng

router = APIRouter(prefix="/api/analytics", tags=["Analytics"])

KIND_PATTERN = "^(all|bulk|single|scheduled)$"


def _days(days: int) -> Optional[int]:
    return days if days > 0 else None


@router.get("/overview")
def overview(
    days: int = Query(30, ge=0, le=3650, description="0 = all time"),
    kind: str = Query("all", pattern=KIND_PATTERN),
    sync: bool = Query(True, description="Check Gmail for new replies/bounces first"),
    db: Session = Depends(get_db),
):
    sync_result = svc.sync_tracking() if sync else None
    db.expire_all()
    return {
        "success": True,
        "sync": sync_result,
        **svc.build_overview(db, _days(days), kind),
    }


@router.post("/sync")
def sync_now():
    return {"success": True, **svc.sync_tracking()}


@router.get("/export.csv")
def export_csv(
    report: str = Query("campaigns", pattern="^(campaigns|emails)$"),
    days: int = Query(30, ge=0, le=3650),
    kind: str = Query("all", pattern=KIND_PATTERN),
    db: Session = Depends(get_db),
):
    body = (
        svc.campaigns_csv(db, _days(days), kind)
        if report == "campaigns"
        else svc.emails_csv(db, _days(days), kind)
    )
    name = f"automail-{report}-{datetime.utcnow():%Y%m%d}.csv"
    return Response(
        content="﻿" + body,  # BOM so Excel reads UTF-8
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


RANGE_PATTERN = "^(today|7d|30d|custom)$"


@router.get("/engagement")
def engagement(
    range: str = Query("30d", pattern=RANGE_PATTERN),
    start: Optional[str] = Query(None, max_length=10),
    end: Optional[str] = Query(None, max_length=10),
    tz: str = Query("UTC", max_length=64),
    db: Session = Depends(get_db),
):
    try:
        return eng.build_engagement(db, range, start, end, tz)
    except eng.RangeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/engagement/emails")
def engagement_emails(
    metric: str = Query("all"),
    range: str = Query("30d", pattern=RANGE_PATTERN),
    start: Optional[str] = Query(None, max_length=10),
    end: Optional[str] = Query(None, max_length=10),
    tz: str = Query("UTC", max_length=64),
    page: int = Query(1, ge=1, le=1000),
    limit: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
):
    try:
        return eng.list_metric_emails(db, metric, range, start, end, tz, page, limit)
    except eng.RangeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/engagement/stamp")
def engagement_stamp(db: Session = Depends(get_db)):
    return eng.stamp(db)
