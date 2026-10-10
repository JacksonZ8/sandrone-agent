"""游戏档案：陪玩角色、存档卡与增删改。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlmodel import Session, delete, select

from ...models import GameMilestone, GameProfile, GameSession, ScreenObservation, SemanticMemory, utcnow
from .scope import aliases, app_names, as_utc, game_scope


@dataclass(frozen=True)
class Role:
    label: str
    summary: str  # 给用户看的说明
    prompt: str  # 给她的角色说明
    max_remarks: int  # 每场最多开口次数
    gap_minutes: int  # 同一场里两次开口的最短间隔
    big_events_only: bool  # 只在大事件后开口


ROLES: dict[str, Role] = {
    "recorder": Role(
        "记录员",
        "只看不说，玩完写一段战报。",
        "你只是安静地看着、记着，游戏中不说话。",
        0,
        0,
        True,
    ),
    "spectator": Role(
        "安静观众",
        "只在通关、打倒 Boss、阵亡这类大事件后说一句。",
        "你像坐在旁边安静看他玩的人，只在大事件后简短说一句，不评价操作、不给建议。",
        3,
        15,
        True,
    ),
    "partner": Role(
        "搭子",
        "大事件和结算、菜单、读条这些间隙偶尔接话、吐槽。",
        "你像一起玩的搭子，偶尔接话、吐槽、为他高兴或替他不爽，语气随意，不长篇指导。",
        6,
        8,
        False,
    ),
    "coach": Role(
        "教练",
        "间隙给一两句具体的战术建议，阵亡后复盘一句。",
        "你是他的教练，在间隙给一两句具体可执行的建议（结合他的阵容/卡组和刚才的局面），阵亡后简短复盘原因。不确定的机制和数值不说。",
        8,
        5,
        False,
    ),
}

# 账号档案：只属于这个玩家，存在本机数据库，不进可分享的知识文件
SAVE_FIELDS: list[tuple[str, str]] = [
    ("progress", "进度"),
    ("roster", "拥有的角色/卡牌/装备"),
    ("build", "当前配队/卡组/流派"),
    ("resources", "资源与养成"),
    ("goals", "目标"),
    ("struggles", "卡关点"),
    ("preferences", "偏好"),
    ("notes", "备注"),
]
SAVE_FIELD_MAX = 300


def load_save_card(game: GameProfile) -> dict[str, str]:
    try:
        data = json.loads(game.save_card or "{}")
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {key: str(data.get(key) or "").strip()[:SAVE_FIELD_MAX] for key, _ in SAVE_FIELDS}


def merge_save_card(game: GameProfile, updates: dict[str, Any], *, replace: bool = False) -> dict[str, str]:
    """replace=True 时按用户填写原样保存（空串即清空）；否则只采用非空字段（模型更新）。"""
    card = load_save_card(game)
    for key, _ in SAVE_FIELDS:
        if key not in updates:
            continue
        value = str(updates.get(key) or "").strip()[:SAVE_FIELD_MAX]
        if replace or value:
            card[key] = value
    game.save_card = json.dumps(card, ensure_ascii=False)
    return card


def save_card_text(game: GameProfile) -> str:
    card = load_save_card(game)
    lines = [f"- {label}：{card[key]}" for key, label in SAVE_FIELDS if card[key]]
    return "\n".join(lines) or "（还没有记录）"


def roles_public() -> list[dict[str, Any]]:
    return [{"id": key, "label": role.label, "summary": role.summary} for key, role in ROLES.items()]


def game_public(game: GameProfile) -> dict[str, Any]:
    return {
        "id": game.id,
        "name": game.name,
        "app_names": app_names(game),
        "aliases": aliases(game),
        "genre": game.genre,
        "hud_hints": game.hud_hints,
        "role": game.role,
        "interval_minutes": game.interval_minutes,
        "recap_enabled": game.recap_enabled,
        "save_card": load_save_card(game),
        "knowledge_file": game.knowledge_file,
        "created_at": as_utc(game.created_at),
        "updated_at": as_utc(game.updated_at),
    }


def session_public(row: GameSession) -> dict[str, Any]:
    return {
        "id": row.id,
        "game_id": row.game_id,
        "started_at": as_utc(row.started_at),
        "last_seen_at": as_utc(row.last_seen_at),
        "ended_at": as_utc(row.ended_at),
        "remark_count": row.remark_count,
        "recap": row.recap,
        "message_id": row.message_id,
    }


def list_games(session: Session) -> list[GameProfile]:
    return list(session.exec(select(GameProfile).order_by(GameProfile.name)).all())


def game_for_app(session: Session, app: str) -> GameProfile | None:
    for game in list_games(session):
        if app in app_names(game):
            return game
    return None


def _clean_apps(values: Any) -> str:
    apps = sorted({str(value).strip() for value in values or [] if str(value).strip()})
    if not apps:
        raise ValueError("至少要选一个游戏窗口对应的应用。")
    return json.dumps(apps, ensure_ascii=False)


def _apply(game: GameProfile, data: dict[str, Any]) -> None:
    if data.get("name") is not None:
        name = str(data["name"]).strip()
        if not name:
            raise ValueError("游戏名不能为空。")
        game.name = name[:60]
    if data.get("app_names") is not None:
        game.app_names = _clean_apps(data["app_names"])
    if data.get("aliases") is not None:
        names = sorted({str(value).strip()[:30] for value in data["aliases"] if len(str(value).strip()) >= 2})
        game.aliases = json.dumps(names[:10], ensure_ascii=False)
    if data.get("role") is not None:
        if data["role"] not in ROLES:
            raise ValueError("未知的陪玩角色。")
        game.role = data["role"]
    for field in ("genre", "hud_hints"):
        if data.get(field) is not None:
            setattr(game, field, str(data[field]).strip()[:400])
    for field in ("interval_minutes", "recap_enabled"):
        if data.get(field) is not None:
            setattr(game, field, data[field])
    if data.get("save_card") is not None:
        merge_save_card(game, data["save_card"], replace=True)


def create_game(session: Session, data: dict[str, Any]) -> GameProfile:
    if data.get("role") not in ROLES:
        raise ValueError("请先为她选一个陪玩角色。")
    game = GameProfile(name="", app_names="[]")
    if not str(data.get("name") or "").strip():
        raise ValueError("游戏名不能为空。")
    if not data.get("app_names"):
        raise ValueError("至少要选一个游戏窗口对应的应用。")
    _apply(game, data)
    session.add(game)
    session.commit()
    session.refresh(game)
    return game


def update_game(session: Session, game: GameProfile, data: dict[str, Any]) -> GameProfile:
    _apply(game, data)
    game.updated_at = utcnow()
    session.add(game)
    session.commit()
    session.refresh(game)
    return game


def delete_game(session: Session, game: GameProfile) -> None:
    """删除档案、账号档案、场次、观察与本机资料；可分享的知识文件留在仓库，长期记忆转为通用记忆。"""
    assert game.id is not None
    session.exec(delete(ScreenObservation).where(ScreenObservation.game_id == game.id))
    session.exec(delete(GameSession).where(GameSession.game_id == game.id))
    for memory in session.exec(select(SemanticMemory).where(SemanticMemory.scope == game_scope(game.id))).all():
        memory.scope = ""
        session.add(memory)
    session.exec(delete(GameMilestone).where(GameMilestone.game_id == game.id))
    session.delete(game)
    session.commit()
    from .knowledge import remove_game_files  # 延迟导入：knowledge 依赖本模块

    remove_game_files(game)


def list_sessions(session: Session, game_id: int, limit: int = 20) -> list[GameSession]:
    return list(
        session.exec(
            select(GameSession)
            .where(GameSession.game_id == game_id)
            .order_by(GameSession.started_at.desc(), GameSession.id.desc())
            .limit(limit)
        ).all()
    )


def game_memories(session: Session, game_id: int, limit: int = 50) -> list[SemanticMemory]:
    return list(
        session.exec(
            select(SemanticMemory)
            .where(SemanticMemory.scope == game_scope(game_id), SemanticMemory.status == "active")
            .order_by(SemanticMemory.updated_at.desc())
            .limit(limit)
        ).all()
    )
