"""游戏档案、知识与里程碑的接口契约（前端 types.ts 手工同步）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class GameSaveCard(BaseModel):
    """账号档案（只属于这个玩家，存在本机数据库）。"""

    progress: str = Field(default="", max_length=300)
    roster: str = Field(default="", max_length=300)
    build: str = Field(default="", max_length=300)
    resources: str = Field(default="", max_length=300)
    goals: str = Field(default="", max_length=300)
    struggles: str = Field(default="", max_length=300)
    preferences: str = Field(default="", max_length=300)
    notes: str = Field(default="", max_length=300)


class GameCreate(BaseModel):
    name: str = Field(max_length=60)
    app_names: list[str] = Field(min_length=1, max_length=10)
    aliases: list[str] = Field(default_factory=list, max_length=10)
    role: str
    genre: str = Field(default="", max_length=60)
    hud_hints: str = Field(default="", max_length=400)
    interval_minutes: int = Field(default=2, ge=1, le=30)
    recap_enabled: bool = True


class GameUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=60)
    app_names: list[str] | None = Field(default=None, min_length=1, max_length=10)
    aliases: list[str] | None = Field(default=None, max_length=10)
    role: str | None = None
    genre: str | None = Field(default=None, max_length=60)
    hud_hints: str | None = Field(default=None, max_length=400)
    interval_minutes: int | None = Field(default=None, ge=1, le=30)
    recap_enabled: bool | None = None
    save_card: GameSaveCard | None = None


class GamePublic(BaseModel):
    id: int
    name: str
    app_names: list[str]
    aliases: list[str]
    genre: str
    hud_hints: str
    role: str
    interval_minutes: int
    recap_enabled: bool
    save_card: GameSaveCard
    knowledge_file: str
    auto_detected: bool = False
    created_at: datetime
    updated_at: datetime


class GameRolePublic(BaseModel):
    id: str
    label: str
    summary: str


class GameSessionPublic(BaseModel):
    id: int
    game_id: int
    started_at: datetime
    last_seen_at: datetime
    ended_at: datetime | None
    remark_count: int
    recap: str
    message_id: int | None


class GameKnowledgeSource(BaseModel):
    number: int
    kind: str
    title: str
    url: str
    chars: int
    added_at: str
    pending: bool = False


class GameKnowledgePublic(BaseModel):
    file: str
    content: str
    updated_at: datetime | None
    sources: list[GameKnowledgeSource]
    pending: int


class GameKnowledgeUpdate(BaseModel):
    content: str = Field(max_length=20000)


class GameNoteImport(BaseModel):
    title: str = Field(default="", max_length=120)
    text: str = Field(min_length=1, max_length=12000)


class GameResearchRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=80)


class GameKnowledgeResult(GameKnowledgePublic):
    added: list[GameKnowledgeSource]
    failed: list[dict[str, str]]
    account_updated: bool


class GameMilestonePublic(BaseModel):
    id: int
    at: datetime
    kind: str
    text: str
    session_id: int | None


class GameMilestoneCreate(BaseModel):
    text: str = Field(min_length=1, max_length=120)
