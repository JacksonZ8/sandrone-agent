"""记忆、日记、她的状态与关系的接口契约（前端 types.ts 手工同步）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class MemoryCreate(BaseModel):
    perspective: str = Field(default="user", pattern="^(user|self|shared)$")
    type: str
    content: str
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    valence: float = Field(default=0.0, ge=-1.0, le=1.0)


class MemoryUpdate(BaseModel):
    type: str | None = None
    content: str | None = Field(default=None, min_length=1, max_length=4000)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    importance: float | None = Field(default=None, ge=0.0, le=1.0)
    valence: float | None = Field(default=None, ge=-1.0, le=1.0)
    status: str | None = None


class MemoryPublic(BaseModel):
    id: int
    perspective: str
    type: str
    content: str
    source_message_ids: list[int]
    confidence: float
    importance: float
    valence: float
    strength: float
    access_count: int
    last_accessed_at: datetime | None
    status: str
    notes: str
    scope: str = ""
    created_at: datetime
    updated_at: datetime


class DiaryPublic(BaseModel):
    id: int
    entry_type: str
    summary: str
    mood_before: str
    mood_after: str
    created_at: datetime


class AgentStatePublic(BaseModel):
    mood: str
    energy: float
    focus: str


class AffectionEventPublic(BaseModel):
    id: int
    delta: float
    level_before: float
    level_after: float
    reason: str
    created_at: datetime


class UserEmotionPublic(BaseModel):
    label: str
    confidence: float
    source: str
    observed_at: datetime


class RelationshipPublic(BaseModel):
    affection: float
    familiarity: float
    trust: float
    phase: str
    guidance: str
    updated_at: datetime
    events: list[AffectionEventPublic]
    user_emotion: UserEmotionPublic | None = None


class ReflectResponse(BaseModel):
    accepted: bool
    message: str


class ConsolidateResponse(BaseModel):
    accepted: bool
    run_id: int | None
    status: str
    message: str
