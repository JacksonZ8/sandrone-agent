from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from ..database import get_session
from ..models import SemanticMemory
from ..schemas import (
    ConsolidateResponse,
    DiaryPublic,
    MemoryCreate,
    MemoryPublic,
    MemoryUpdate,
    ReflectResponse,
    RelationshipPublic,
)
from ..services.memory_service import (
    _diary_to_public,
    _memory_to_dict,
    generate_diary_now,
    list_diary_public,
    list_memories,
    run_consolidation_now,
    soft_delete_memory,
    update_memory,
)
from ..services.relationship_service import relationship_public

router = APIRouter(prefix="/api", tags=["memory"])

VALID_PERSPECTIVES = ("user", "self", "shared")
VALID_TYPES = ("fact", "preference", "relationship", "project", "event")


@router.get("/memories", response_model=list[MemoryPublic])
def read_memories(
    session: Session = Depends(get_session),
    status: str = Query(default="active"),
    type: str | None = Query(default=None, alias="type"),
    perspective: str | None = Query(default=None),
) -> list[dict]:
    if perspective and perspective not in VALID_PERSPECTIVES:
        raise HTTPException(status_code=422, detail="未知的记忆视角。")
    memories = list_memories(session, status=status, mtype=type, perspective=perspective)
    return [_memory_to_dict(m) for m in memories]


@router.post("/memories", response_model=MemoryPublic)
def create_memory(payload: MemoryCreate, session: Session = Depends(get_session)) -> dict:
    if payload.type not in VALID_TYPES:
        raise HTTPException(status_code=422, detail="未知的记忆类型。")
    if payload.perspective != "user":
        raise HTTPException(
            status_code=403,
            detail="只有「关于用户」的记忆可以由用户手动添加；她自己的记忆由她自己巩固。",
        )
    memory = SemanticMemory(
        perspective="user",
        type=payload.type,
        content=payload.content,
        source_message_ids="[]",
        confidence=payload.confidence,
        importance=payload.importance,
        valence=payload.valence,
        status="active",
        notes="手动添加",
    )
    session.add(memory)
    session.commit()
    session.refresh(memory)
    return _memory_to_dict(memory)


@router.patch("/memories/{memory_id}", response_model=MemoryPublic)
def patch_memory(
    memory_id: int,
    payload: MemoryUpdate,
    session: Session = Depends(get_session),
) -> dict:
    memory = session.get(SemanticMemory, memory_id)
    if memory is None:
        raise HTTPException(status_code=404, detail="记忆不存在。")
    if memory.perspective != "user":
        raise HTTPException(
            status_code=403,
            detail="这是她关于自己或你们之间的记忆，默认只读。你只能编辑「关于用户」的记忆。",
        )
    data = payload.model_dump(exclude_unset=True)
    if data.get("type") and data["type"] not in VALID_TYPES:
        raise HTTPException(status_code=422, detail="未知的记忆类型。")
    memory = update_memory(session, memory, data)
    return _memory_to_dict(memory)


@router.delete("/memories/{memory_id}", response_model=MemoryPublic)
def delete_memory(memory_id: int, session: Session = Depends(get_session)) -> dict:
    memory = session.get(SemanticMemory, memory_id)
    if memory is None:
        raise HTTPException(status_code=404, detail="记忆不存在。")
    if memory.perspective != "user":
        raise HTTPException(
            status_code=403,
            detail="这是她关于自己或你们之间的记忆，默认只读，不能由用户删除。",
        )
    memory = soft_delete_memory(session, memory)
    return _memory_to_dict(memory)


@router.get("/relationship", response_model=RelationshipPublic)
def read_relationship(session: Session = Depends(get_session)) -> dict:
    return relationship_public(session)


@router.post("/memories/consolidate", response_model=ConsolidateResponse)
async def consolidate_memories() -> dict:
    run = await asyncio.to_thread(run_consolidation_now)
    messages = {
        "success": "记忆整理完成。",
        "skipped": run.error or "当前没有需要整理的内容。",
        "failed": f"记忆整理失败：{run.error}",
    }
    return {
        "accepted": run.status != "failed",
        "run_id": run.id,
        "status": run.status,
        "message": messages.get(run.status, run.status),
    }


@router.get("/diary", response_model=list[DiaryPublic])
def read_diary(
    session: Session = Depends(get_session),
    limit: int = Query(default=30, ge=1, le=100),
) -> list[dict]:
    entries = list_diary_public(session, limit=limit)
    return [_diary_to_public(d) for d in entries]


@router.post("/diary/reflect", response_model=ReflectResponse)
async def reflect_now() -> dict:
    entry = await asyncio.to_thread(generate_diary_now)
    if entry is None:
        return {"accepted": False, "message": "还没有足够的对话可以整理。"}
    return {"accepted": True, "message": "她写完了一篇新的日记。"}
