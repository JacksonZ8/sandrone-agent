from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, func, select

from ..database import get_session
from ..models import Message

router = APIRouter(prefix="/api/pet", tags=["pet"])

BUBBLE_MAX_CHARS = 90
SCAN_LIMIT = 50


def _initiated(message: Message) -> bool:
    try:
        return bool(json.loads(message.inner_state or "{}").get("initiated"))
    except (json.JSONDecodeError, AttributeError):
        return False


def _bubble_text(text: str) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= BUBBLE_MAX_CHARS else flat[: BUBBLE_MAX_CHARS - 1] + "…"


@router.get("/messages")
def initiated_messages(after: int | None = Query(default=None, ge=0), session: Session = Depends(get_session)) -> dict:
    """桌宠气泡：她主动发出的消息（主动消息、看屏/陪玩时开口、战报）。

    不带 after 时只返回当前最新的消息 id，桌宠从这里开始等新消息，启动时不会把旧消息冒出来。
    """
    latest = session.exec(select(func.max(Message.id))).one() or 0
    if after is None:
        return {"latest_id": latest, "messages": []}
    rows = session.exec(
        select(Message)
        .where(Message.id > after, Message.role == "assistant")
        .order_by(Message.id)
        .limit(SCAN_LIMIT)
    ).all()
    messages = [
        {"id": row.id, "text": _bubble_text(row.content), "created_at": row.created_at}
        for row in rows
        if _initiated(row) and row.content.strip()
    ]
    return {"latest_id": max([latest, *(row.id for row in rows)]), "messages": messages}
