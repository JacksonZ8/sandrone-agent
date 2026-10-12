"""应用状态与 TTS的接口契约（前端 types.ts 手工同步）。"""

from __future__ import annotations

from pydantic import BaseModel

from .memory import AgentStatePublic


class AppState(BaseModel):
    app_name: str
    app_version: str
    message_count: int
    active_memory_count: int
    has_api_key: bool
    provider: str
    model: str
    voice_ready: bool
    voice_message: str
    agent_state: AgentStatePublic


class TTSStatus(BaseModel):
    ready: bool
    voice_id: str
    engine: str
    message: str
