"""看屏 · 陪着看的接口契约（前端 types.ts 手工同步）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class CompanionConfigUpdate(BaseModel):
    enabled: bool | None = None
    consent: bool | None = None
    allowed_apps: list[str] | None = Field(default=None, max_length=50)
    interval_minutes: int | None = Field(default=None, ge=2, le=60)
    remark_gap_minutes: int | None = Field(default=None, ge=0, le=600)
    notify: bool | None = None
    stop_watch: bool | None = None  # 结束语音临时开的陪着看


class CompanionConfigPublic(BaseModel):
    enabled: bool
    allowed_apps: list[str]
    interval_minutes: int
    remark_gap_minutes: int
    notify: bool
    consented: bool
    paused_until: datetime | None
    watch_until: datetime | None = None  # 语音临时开的陪着看到几点
    watch_apps: list[str] = []
    vision_base_url: str
    vision_model: str
    has_vision_key: bool
    updated_at: datetime


class CompanionPauseRequest(BaseModel):
    minutes: int = Field(default=60, ge=0, le=24 * 60)  # 0 表示恢复


class CompanionStatusPublic(BaseModel):
    state: str
    app: str
    checked_at: datetime | None
    seen_at: datetime | None
    message: str


class CompanionObservationPublic(BaseModel):
    id: int
    created_at: datetime
    app: str
    summary: str
    detail: str
    notable: bool
    message_id: int | None


class CompanionCheckResult(BaseModel):
    result: str
    status: CompanionStatusPublic
