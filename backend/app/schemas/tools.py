"""工具配置与调用（文件、搜索、看屏、子代理）的接口契约（前端 types.ts 手工同步）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class FileToolConfig(BaseModel):
    files_enabled: bool
    file_allowlist: list[str]


class FileToolConfigUpdate(BaseModel):
    files_enabled: bool | None = None
    file_allowlist: list[str] | None = None


class FileReadRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1024)


class FileReadResult(BaseModel):
    ok: bool
    path: str
    content: str = ""
    size: int = 0
    message: str = ""


class SearchToolConfig(BaseModel):
    search_provider: str
    has_api_key: bool
    api_key_masked: str


class SearchToolConfigUpdate(BaseModel):
    search_provider: str | None = None
    api_key: str | None = Field(default=None, max_length=512)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    max_results: int = Field(default=5, ge=1, le=10)


class SearchResultItem(BaseModel):
    title: str
    url: str
    snippet: str


class SearchResponse(BaseModel):
    ok: bool
    query: str
    results: list[SearchResultItem]
    message: str = ""


class ScreenToolConfig(BaseModel):
    screen_enabled: bool
    vision_base_url: str
    vision_model: str
    has_vision_api_key: bool
    vision_api_key_masked: str


class ScreenToolConfigUpdate(BaseModel):
    screen_enabled: bool | None = None
    vision_base_url: str | None = Field(default=None, max_length=512)
    vision_model: str | None = Field(default=None, max_length=128)
    vision_api_key: str | None = Field(default=None, max_length=512)


class ScreenAnalyzeRequest(BaseModel):
    question: str = Field(default="看看我现在的屏幕，告诉我当前在做什么。", max_length=1000)


class ScreenAnalyzeResponse(BaseModel):
    ok: bool
    analysis: str
    message: str = ""


class AgentToolConfig(BaseModel):
    agent_enabled: bool
    agent_command: str
    agent_permission: str
    agent_allowlist: list[str]
    agent_timeout_seconds: int
    agent_max_output_chars: int
    agent_max_budget_usd: float
    cli_available: bool
    cli_version: str


class AgentToolConfigUpdate(BaseModel):
    agent_enabled: bool | None = None
    agent_command: str | None = Field(default=None, max_length=64)
    agent_permission: str | None = Field(default=None, pattern="^(readonly|write)$")
    agent_allowlist: list[str] | None = None
    agent_timeout_seconds: int | None = Field(default=None, ge=30, le=3600)
    agent_max_output_chars: int | None = Field(default=None, ge=500, le=100000)
    agent_max_budget_usd: float | None = Field(default=None, ge=0.1, le=100.0)


class AgentRunRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=20000)
    cwd: str = Field(default="", max_length=1024)


class AgentRunResponse(BaseModel):
    ok: bool
    command: str
    cwd: str
    output: str
    duration_ms: int
    message: str = ""


class AgentTaskPublic(BaseModel):
    id: int
    title: str
    prompt: str
    spoken_summary: str
    command: str
    permission: str
    cwd: str
    status: str
    status_label: str
    parent_id: int | None
    agent_session_id: str
    target: str
    target_title: str
    channel: str
    output: str
    error: str
    duration_ms: int
    created_at: datetime
    confirmed_at: datetime | None
    finished_at: datetime | None
    risks: list[str]
    codex_url: str


class AgentTaskActionResponse(BaseModel):
    ok: bool
    message: str
    task: AgentTaskPublic | None = None


class CodexThreadPublic(BaseModel):
    id: str
    title: str
    cwd: str
    updated_at_ms: int
    recently_active: bool


class AgentTaskThreadUpdate(BaseModel):
    thread_id: str = Field(default="", max_length=64)  # 空字符串表示不接着 App 线程
