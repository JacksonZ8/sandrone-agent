from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from ..database import get_session
from ..models import GameProfile
from ..schemas import (
    GameCreate,
    GameKnowledgePublic,
    GameKnowledgeResult,
    GameKnowledgeUpdate,
    GameMilestoneCreate,
    GameMilestonePublic,
    GameNoteImport,
    GamePublic,
    GameResearchRequest,
    GameRolePublic,
    GameSessionPublic,
    GameUpdate,
    MemoryPublic,
)
from ..services.games import knowledge
from ..services.games.account import add_milestone, delete_milestone, list_milestones, milestone_public
from ..services.games.profiles import (
    create_game,
    delete_game,
    game_memories,
    game_public,
    list_games,
    list_sessions,
    roles_public,
    session_public,
    update_game,
)
from ..services.memory.common import _memory_to_dict

router = APIRouter(prefix="/api/games", tags=["games"])


def _game(session: Session, game_id: int) -> GameProfile:
    game = session.get(GameProfile, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="没有这个游戏。")
    return game


@router.get("/roles", response_model=list[GameRolePublic])
def read_roles() -> list[dict]:
    return roles_public()


@router.get("", response_model=list[GamePublic])
def read_games(session: Session = Depends(get_session)) -> list[dict]:
    return [game_public(game) for game in list_games(session)]


@router.post("", response_model=GamePublic)
def add_game(payload: GameCreate, session: Session = Depends(get_session)) -> dict:
    try:
        return game_public(create_game(session, payload.model_dump()))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{game_id}", response_model=GamePublic)
def read_game(game_id: int, session: Session = Depends(get_session)) -> dict:
    return game_public(_game(session, game_id))


@router.put("/{game_id}", response_model=GamePublic)
def write_game(game_id: int, payload: GameUpdate, session: Session = Depends(get_session)) -> dict:
    try:
        return game_public(update_game(session, _game(session, game_id), payload.model_dump(exclude_unset=True)))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/{game_id}")
def remove_game(game_id: int, session: Session = Depends(get_session)) -> dict:
    delete_game(session, _game(session, game_id))
    return {"ok": True}


@router.get("/{game_id}/sessions", response_model=list[GameSessionPublic])
def read_sessions(
    game_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    session: Session = Depends(get_session),
) -> list[dict]:
    _game(session, game_id)
    return [session_public(row) for row in list_sessions(session, game_id, limit=limit)]


@router.get("/{game_id}/memories", response_model=list[MemoryPublic])
def read_game_memories(game_id: int, session: Session = Depends(get_session)) -> list[dict]:
    """她关于这个游戏的长期记忆；关于你的那部分可在“她记得的事”里修改或删除。"""
    _game(session, game_id)
    return [_memory_to_dict(memory) for memory in game_memories(session, game_id)]


@router.get("/{game_id}/milestones", response_model=list[GameMilestonePublic])
def read_milestones(game_id: int, session: Session = Depends(get_session)) -> list[dict]:
    _game(session, game_id)
    return [milestone_public(row) for row in list_milestones(session, game_id, limit=50)]


@router.post("/{game_id}/milestones", response_model=GameMilestonePublic)
def write_milestone(game_id: int, payload: GameMilestoneCreate, session: Session = Depends(get_session)) -> dict:
    _game(session, game_id)
    row = add_milestone(session, game_id, payload.text, kind="manual")
    if row is None:
        raise HTTPException(status_code=409, detail="今天已经记过这条里程碑。")
    return milestone_public(row)


@router.delete("/{game_id}/milestones/{milestone_id}")
def remove_milestone(game_id: int, milestone_id: int, session: Session = Depends(get_session)) -> dict:
    if not delete_milestone(session, game_id, milestone_id):
        raise HTTPException(status_code=404, detail="没有这条里程碑。")
    return {"ok": True}


@router.get("/{game_id}/knowledge", response_model=GameKnowledgePublic)
def read_knowledge(game_id: int, session: Session = Depends(get_session)) -> dict:
    game = _game(session, game_id)
    knowledge.ensure_knowledge_file(session, game)
    return knowledge.knowledge_public(game)


@router.put("/{game_id}/knowledge", response_model=GameKnowledgePublic)
def write_knowledge(game_id: int, payload: GameKnowledgeUpdate, session: Session = Depends(get_session)) -> dict:
    """直接修改可分享的知识文件；下次整理会以修改后的内容为基础（含个人信息的行会被去掉）。"""
    game = _game(session, game_id)
    knowledge.ensure_knowledge_file(session, game)
    knowledge.write_knowledge(game, payload.content)
    return knowledge.knowledge_public(game)


@router.post("/{game_id}/notes", response_model=GameKnowledgeResult)
async def import_note(game_id: int, payload: GameNoteImport, session: Session = Depends(get_session)) -> dict:
    """导入笔记：她把通用部分整理进知识文件，你个人的部分合并进账号档案。"""
    try:
        return await knowledge.import_note(session, _game(session, game_id), payload.title, payload.text)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"整理失败：{exc}") from exc


@router.post("/{game_id}/research", response_model=GameKnowledgeResult)
async def research(game_id: int, payload: GameResearchRequest, session: Session = Depends(get_session)) -> dict:
    """让她就一个主题联网查资料并整理进知识文件（约一分钟）。"""
    try:
        return await knowledge.research(session, _game(session, game_id), payload.topic)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"查资料失败：{exc}") from exc
