"""LLM 配置与 Provider 预设的接口契约（前端 types.ts 手工同步）。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


PROVIDER_PRESETS = {
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-flash",
        "api_key_required": True,
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "api_key_required": True,
    },
    "openai_compatible": {
        "label": "OpenAI 兼容接口",
        "base_url": "",
        "model": "",
        "api_key_required": False,
    },
    "mock": {
        "label": "本地 Mock（无 Key 联调）",
        "base_url": "local://mock",
        "model": "mock-sandrone",
        "api_key_required": False,
    },
}


class LLMConfigUpdate(BaseModel):
    provider: str | None = Field(default=None, max_length=64)
    base_url: str | None = Field(default=None, max_length=512)
    model: str | None = Field(default=None, max_length=128)
    api_key: str | None = Field(default=None, max_length=512)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=64, le=32768)
    timeout_seconds: float | None = Field(default=None, ge=5.0, le=300.0)
    reply_thinking: Literal["auto", "off", "low", "high"] | None = None


class LLMConfigPublic(BaseModel):
    provider: str
    provider_label: str
    base_url: str
    model: str
    has_api_key: bool
    api_key_masked: str
    api_key_required: bool
    temperature: float
    max_tokens: int
    timeout_seconds: float
    reply_thinking: str
    updated_at: datetime


class LLMTestRequest(LLMConfigUpdate):
    pass


class LLMTestResponse(BaseModel):
    ok: bool
    message: str
    provider: str
    model: str
    latency_ms: int
    reply_preview: str = ""
