from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from ..database import get_session
from ..schemas import UsageSummaryResponse
from ..services.usage_service import get_usage_summary

router = APIRouter(prefix="/api/usage", tags=["usage"])


@router.get("/summary", response_model=UsageSummaryResponse)
def read_usage_summary(
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    provider: str = Query(default="", max_length=128),
    purpose: str = Query(default="", max_length=64),
    session: Session = Depends(get_session),
) -> dict:
    if start_at is not None and end_at is not None and end_at <= start_at:
        raise HTTPException(status_code=422, detail="结束时间必须晚于开始时间。")
    return get_usage_summary(
        session,
        start_at=start_at,
        end_at=end_at,
        provider=provider.strip(),
        purpose=purpose.strip(),
    )
