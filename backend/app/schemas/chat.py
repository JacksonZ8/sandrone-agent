"""对话请求、消息与痕迹的接口契约（前端 types.ts 手工同步）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ReplyStyleRequest(BaseModel):
    # 网页端的回复偏好；不传时沿用人设里的默认风格。
    reply_length: Literal["short", "long"] | None = None
    action_text: bool | None = None
    voice_mode: bool = False


class ChatRequest(ReplyStyleRequest):
    content: str = Field(min_length=1, max_length=20000)


class MemoryTracePublic(BaseModel):
    layer: str
    perspective: str = ""
    memory_type: str = ""
    content: str
    score: float = 0.0
    source_message_id: int | None = None


class MessagePublic(BaseModel):
    id: int
    thread_id: str
    channel: str = "web"
    sender_id: str = "owner"
    chat_id: str = "web"
    turn_index: int
    role: str
    content: str
    created_at: datetime
    provider: str
    model: str
    latency_ms: int
    audio_path: str = ""
    tool_calls: list[ToolCallTrace] = []
    memory_traces: list[MemoryTracePublic] = []
    initiated: bool = False


class ToolCallTrace(BaseModel):
    name: str
    arguments: dict[str, Any]
    result_preview: str = ""


class ChatResponse(BaseModel):
    user_message: MessagePublic
    assistant_message: MessagePublic | None = None
    tool_calls: list[ToolCallTrace] = []
    error: str = ""


MessagePublic.model_rebuild()
ToolCallTrace.model_rebuild()
ChatResponse.model_rebuild()
