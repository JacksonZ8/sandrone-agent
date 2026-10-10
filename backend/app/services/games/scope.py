"""游戏语境判断：只依赖数据表，供记忆检索与提取使用（避免与陪玩逻辑循环导入）。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from ...models import GameProfile, GameSession

SCOPE_PREFIX = "game:"
SESSION_IDLE_MINUTES = 15  # 游戏离开前台这么久算一场结束


def game_scope(game_id: int) -> str:
    return f"{SCOPE_PREFIX}{game_id}"


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _json_names(raw: str) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    return [str(item) for item in value if isinstance(item, str) and item.strip()] if isinstance(value, list) else []


def app_names(game: GameProfile) -> list[str]:
    return _json_names(game.app_names)


def aliases(game: GameProfile) -> list[str]:
    return _json_names(game.aliases)


def active_session(session: Session, now: datetime | None = None) -> GameSession | None:
    """最近 15 分钟内还在玩的那一场。"""
    cutoff = (now or datetime.now(timezone.utc)).astimezone(timezone.utc) - timedelta(minutes=SESSION_IDLE_MINUTES)
    row = session.exec(
        select(GameSession)
        .where(GameSession.ended_at.is_(None))  # type: ignore[union-attr]
        .order_by(GameSession.last_seen_at.desc(), GameSession.id.desc())
    ).first()
    if row is None or (as_utc(row.last_seen_at) or cutoff) < cutoff:
        return None
    return row


def mentioned_games(session: Session, text: str) -> list[GameProfile]:
    lowered = (text or "").lower()
    if not lowered:
        return []
    found = []
    for game in session.exec(select(GameProfile)).all():
        names = [game.name, *aliases(game), *app_names(game)]
        if any(len(name) >= 2 and name.lower() in lowered for name in names):
            found.append(game)
    return found


def active_game_scopes(session: Session, query: str, now: datetime | None = None) -> set[str]:
    """当前语境下“算数”的游戏：正在玩的那个，加上这句话里提到的。"""
    scopes = {game_scope(game.id) for game in mentioned_games(session, query) if game.id is not None}
    playing = active_session(session, now)
    if playing is not None:
        scopes.add(game_scope(playing.game_id))
    return scopes


def message_scope(session: Session, text: str, at: datetime | None) -> str:
    """一轮对话的记忆归属：发生在某场游戏中、或只提到一个游戏时归该游戏，否则为通用。"""
    playing = active_session(session, at)
    if playing is not None:
        return game_scope(playing.game_id)
    games = mentioned_games(session, text)
    return game_scope(games[0].id) if len(games) == 1 and games[0].id is not None else ""
