from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

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


class UsageMetrics(BaseModel):
    calls: int
    success_calls: int
    failed_calls: int
    other_calls: int
    success_rate: float | None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    average_latency_ms: int | None
    max_latency_ms: int | None


class UsagePurposeBreakdown(UsageMetrics):
    purpose: str


class UsageModelBreakdown(UsageMetrics):
    provider: str
    model: str


class UsageErrorPublic(BaseModel):
    created_at: datetime
    purpose: str
    provider: str
    model: str
    status: str
    latency_ms: int | None
    error: str


class UsageSummaryResponse(BaseModel):
    range: dict[str, datetime | None]
    filters: dict[str, str]
    totals: UsageMetrics
    by_purpose: list[UsagePurposeBreakdown]
    by_model: list[UsageModelBreakdown]
    recent_errors: list[UsageErrorPublic]
    available_providers: list[str]
    available_purposes: list[str]


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


class RelationshipPublic(BaseModel):
    affection: float
    familiarity: float
    trust: float
    phase: str
    guidance: str
    updated_at: datetime
    events: list[AffectionEventPublic]


class ReflectResponse(BaseModel):
    accepted: bool
    message: str


class WeChatConfigUpdate(BaseModel):
    enabled: bool | None = None
    account_id: str | None = Field(default=None, max_length=128)
    token: str | None = Field(default=None, max_length=1024)
    base_url: str | None = Field(default=None, max_length=512)
    cdn_base_url: str | None = Field(default=None, max_length=512)
    owner_user_id: str | None = Field(default=None, max_length=128)


class WeChatStatus(BaseModel):
    enabled: bool
    name: str
    account_id: str
    account_id_masked: str
    has_token: bool
    token_masked: str
    base_url: str
    owner_user_id: str
    owner_paired: bool
    status: str
    last_error: str
    updated_at: datetime


class WeChatLoginStart(BaseModel):
    ok: bool
    qrcode: str
    qr_url: str
    qr_data_url: str
    message: str


class WeChatLoginStatus(BaseModel):
    status: str
    message: str
    account_id: str = ""
    base_url: str = ""


class WeChatPairingStart(BaseModel):
    ok: bool
    code: str
    expires_in_seconds: int
    message: str


class ProactiveConfigUpdate(BaseModel):
    enabled: bool | None = None
    daily_max: int | None = Field(default=None, ge=1, le=4)
    quiet_start: str | None = Field(default=None, max_length=5)
    quiet_end: str | None = Field(default=None, max_length=5)
    min_idle_minutes: int | None = Field(default=None, ge=0, le=1440)


class ProactiveConfigPublic(BaseModel):
    enabled: bool
    daily_max: int
    quiet_start: str
    quiet_end: str
    min_idle_minutes: int
    updated_at: datetime


class ProactiveEventPublic(BaseModel):
    id: int
    status: str
    reason: str
    message_id: int | None
    created_at: datetime


class ProactivePreview(BaseModel):
    send: bool
    text: str


class WeChatSendRequest(BaseModel):
    chat_id: str
    text: str = Field(min_length=1, max_length=2000)


class WeChatSendResponse(BaseModel):
    ok: bool
    message: str


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


class AgentApprovalRequest(BaseModel):
    request_id: str = Field(min_length=8, max_length=64)
    approved: bool


class AgentApprovalResponse(BaseModel):
    ok: bool
    message: str = ""


class ConsolidateResponse(BaseModel):
    accepted: bool
    run_id: int | None
    status: str
    message: str


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


MessagePublic.model_rebuild()
ToolCallTrace.model_rebuild()
ChatResponse.model_rebuild()


class CompanionConfigUpdate(BaseModel):
    enabled: bool | None = None
    consent: bool | None = None
    allowed_apps: list[str] | None = Field(default=None, max_length=50)
    interval_minutes: int | None = Field(default=None, ge=2, le=60)
    remark_gap_minutes: int | None = Field(default=None, ge=0, le=600)
    notify: bool | None = None


class CompanionConfigPublic(BaseModel):
    enabled: bool
    allowed_apps: list[str]
    interval_minutes: int
    remark_gap_minutes: int
    notify: bool
    consented: bool
    paused_until: datetime | None
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
