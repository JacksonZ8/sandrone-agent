from __future__ import annotations

from typing import Any

from sqlmodel import Session, select

from ...models import AgentDiary, SemanticMemory, utcnow


def list_memories(
    session: Session,
    *,
    status: str = "active",
    mtype: str | None = None,
    perspective: str | None = None,
) -> list[SemanticMemory]:
    statement = select(SemanticMemory).where(SemanticMemory.status == status)
    if mtype:
        statement = statement.where(SemanticMemory.type == mtype)
    if perspective:
        statement = statement.where(SemanticMemory.perspective == perspective)
    return list(
        session.exec(
            statement.order_by(SemanticMemory.importance.desc(), SemanticMemory.updated_at.desc())
        ).all()
    )


def update_memory(session: Session, memory: SemanticMemory, data: dict[str, Any]) -> SemanticMemory:
    allowed = ("type", "content", "confidence", "importance", "valence", "status", "notes")
    for key, value in data.items():
        if key in allowed and value is not None:
            setattr(memory, key, value)
    memory.updated_at = utcnow()
    session.add(memory)
    session.commit()
    session.refresh(memory)
    return memory


def soft_delete_memory(session: Session, memory: SemanticMemory) -> SemanticMemory:
    memory.status = "deleted"
    memory.updated_at = utcnow()
    session.add(memory)
    session.commit()
    session.refresh(memory)
    return memory


def list_diary_public(session: Session, *, limit: int = 30) -> list[AgentDiary]:
    return list(
        session.exec(
            select(AgentDiary).order_by(AgentDiary.created_at.desc(), AgentDiary.id.desc()).limit(limit)
        ).all()
    )
