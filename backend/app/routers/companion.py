from __future__ import annotations

import asyncio
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from ..database import get_session
from ..models import utcnow
from ..schemas import (
    CompanionCheckResult,
    CompanionConfigPublic,
    CompanionConfigUpdate,
    CompanionObservationPublic,
    CompanionPauseRequest,
    CompanionStatusPublic,
)
from ..services.screen_companion import (
    COMPANION,
    clear_observations,
    companion_config_public,
    companion_status_public,
    get_companion_config,
    list_observations,
    observation_public,
    update_companion_config,
)

router = APIRouter(prefix="/api/companion", tags=["companion"])


@router.get("/config", response_model=CompanionConfigPublic)
def read_companion_config(session: Session = Depends(get_session)) -> dict:
    return companion_config_public(session, get_companion_config(session))


@router.put("/config", response_model=CompanionConfigPublic)
def write_companion_config(payload: CompanionConfigUpdate, session: Session = Depends(get_session)) -> dict:
    try:
        config = update_companion_config(session, payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return companion_config_public(session, config)


@router.post("/pause", response_model=CompanionConfigPublic)
def pause_companion(payload: CompanionPauseRequest, session: Session = Depends(get_session)) -> dict:
    until = utcnow() + timedelta(minutes=payload.minutes) if payload.minutes else None
    config = update_companion_config(session, {"paused_until": until})
    return companion_config_public(session, config)


@router.get("/status", response_model=CompanionStatusPublic)
def read_companion_status() -> dict:
    return companion_status_public()


@router.get("/apps", response_model=list[str])
async def read_running_apps() -> list[str]:
    """正在运行的普通应用名，供设置页挑选名单。"""
    try:
        return await asyncio.to_thread(COMPANION.desktop.running_apps)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"读取应用列表失败：{exc}") from exc


@router.post("/check", response_model=CompanionCheckResult)
async def check_now() -> dict:
    """立刻看一眼名单内最前面的窗口（忽略间隔与画面未变），用于试用和确认权限。"""
    result = await COMPANION.tick(force=True)
    return {"result": result, "status": companion_status_public()}


@router.get("/observations", response_model=list[CompanionObservationPublic])
def read_observations(
    limit: int = Query(default=20, ge=1, le=100),
    session: Session = Depends(get_session),
) -> list[dict]:
    return [observation_public(row) for row in list_observations(session, limit=limit)]


@router.delete("/observations")
def delete_observations(session: Session = Depends(get_session)) -> dict:
    return {"deleted": clear_observations(session)}
