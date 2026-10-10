"""账号档案的里程碑：打倒 Boss、通关、解锁、升级，以及战报和聊天里她记下的大事。只存在本机数据库。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlmodel import Session, select

from ...models import GameMilestone
from .scope import as_utc

MILESTONE_EVENTS = {"boss_defeated", "victory", "unlock", "level_up"}
MAX_TEXT = 120


def add_milestone(
    session: Session,
    game_id: int,
    text: str,
    *,
    kind: str,
    session_id: int | None = None,
    at: datetime | None = None,
) -> GameMilestone | None:
    """同一游戏一天内同样的里程碑只记一次。"""
    text = text.strip()[:MAX_TEXT]
    if not text:
        return None
    at = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    day_start = at - timedelta(hours=24)
    for row in session.exec(select(GameMilestone).where(GameMilestone.game_id == game_id, GameMilestone.text == text)):
        if (as_utc(row.at) or at) >= day_start:
            return None
    row = GameMilestone(game_id=game_id, text=text, kind=kind, session_id=session_id, at=at)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def list_milestones(session: Session, game_id: int, limit: int = 20) -> list[GameMilestone]:
    return list(
        session.exec(
            select(GameMilestone)
            .where(GameMilestone.game_id == game_id)
            .order_by(GameMilestone.at.desc(), GameMilestone.id.desc())
            .limit(limit)
        ).all()
    )


def milestone_public(row: GameMilestone) -> dict[str, Any]:
    return {"id": row.id, "at": as_utc(row.at), "kind": row.kind, "text": row.text, "session_id": row.session_id}


def delete_milestone(session: Session, game_id: int, milestone_id: int) -> bool:
    row = session.get(GameMilestone, milestone_id)
    if row is None or row.game_id != game_id:
        return False
    session.delete(row)
    session.commit()
    return True
