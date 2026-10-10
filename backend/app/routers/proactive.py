from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from ..database import get_session
from ..schemas import (
    ProactiveConfigPublic,
    ProactiveConfigUpdate,
    ProactiveEventPublic,
    ProactivePreview,
)
from ..services.proactive_service import (
    compose_proactive_message,
    default_wechat_target,
    deliver_proactive_message,
    get_proactive_config,
    list_proactive_events,
    proactive_config_public,
    update_proactive_config,
)

router = APIRouter(prefix="/api/proactive", tags=["proactive"])


@router.get("/config", response_model=ProactiveConfigPublic)
def read_proactive_config(session: Session = Depends(get_session)) -> dict:
    return proactive_config_public(get_proactive_config(session))


@router.put("/config", response_model=ProactiveConfigPublic)
def write_proactive_config(
    payload: ProactiveConfigUpdate,
    session: Session = Depends(get_session),
) -> dict:
    try:
        config = update_proactive_config(session, payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return proactive_config_public(config)


@router.get("/events", response_model=list[ProactiveEventPublic])
def read_proactive_events(
    limit: int = Query(default=20, ge=1, le=100),
    session: Session = Depends(get_session),
) -> list[dict]:
    return list_proactive_events(session, limit=limit)


@router.post("/preview", response_model=ProactivePreview)
async def preview_proactive_message(session: Session = Depends(get_session)) -> dict:
    """只生成、不发送：看看她此刻会不会想主动说话、会说什么。"""
    draft = await compose_proactive_message(session)
    return {"send": draft.send, "text": draft.text}


@router.post("/send-now", response_model=ProactiveEventPublic)
async def send_proactive_now(session: Session = Depends(get_session)) -> dict:
    """立即让她决定并真实发送一条微信（不受时间表约束，用于用户确认后的实测）。"""
    event = await deliver_proactive_message(session, target=_require_target())
    return _event_public(event)


def _require_target():
    target = default_wechat_target()
    if target is None:
        raise HTTPException(status_code=409, detail="微信未连接或尚未绑定主人。")
    return target


def _event_public(event) -> dict:
    return {
        "id": event.id,
        "status": event.status,
        "reason": event.reason,
        "message_id": event.message_id,
        "created_at": event.created_at,
    }
