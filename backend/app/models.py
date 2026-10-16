from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Field, SQLModel

from .secret_store import EncryptedStr


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LLMConfig(SQLModel, table=True):
    __tablename__ = "llm_config"

    id: int = Field(default=1, primary_key=True)
    provider: str = Field(default="deepseek", index=True)
    base_url: str = Field(default="https://api.deepseek.com")
    model: str = Field(default="deepseek-v4-flash")
    api_key: str = Field(default="", sa_type=EncryptedStr)
    temperature: float = Field(default=0.75)
    max_tokens: int = Field(default=1024)
    timeout_seconds: float = Field(default=60.0)
    # 对话、通话、看屏点评时模型的推理：auto（默认，平时关、要用工具时 low）/ off / low / high；后台整理不受影响
    reply_thinking: str = Field(default="auto")
    updated_at: datetime = Field(default_factory=utcnow)


class Message(SQLModel, table=True):
    """单一时间线上的原始对话，同时作为情景记忆的不可变来源。"""

    __tablename__ = "messages"

    id: int | None = Field(default=None, primary_key=True)
    thread_id: str = Field(default="main", index=True)
    channel: str = Field(default="web", index=True)  # web / wechat_dm / wechat_group
    sender_id: str = Field(default="owner", index=True)
    chat_id: str = Field(default="web")
    turn_index: int = Field(default=0, index=True)
    role: str = Field(index=True)  # user / assistant
    content: str
    created_at: datetime = Field(default_factory=utcnow, index=True)
    provider: str = Field(default="")
    model: str = Field(default="")
    prompt_tokens: int = Field(default=0)
    completion_tokens: int = Field(default=0)
    latency_ms: int = Field(default=0)
    importance: float = Field(default=0.0)
    valence: float = Field(default=0.0)
    inner_state: str = Field(default="{}")  # JSON：她回复时的情绪/精力/专注快照
    tool_calls_json: str = Field(default="[]")  # JSON：本次回复的 Agent 工具轨迹
    memory_traces_json: str = Field(default="[]")  # JSON：本次回复唤起的记忆痕迹
    emotion_tags: str = Field(default="[]")  # JSON list，预留给 M3
    audio_path: str = Field(default="")      # 预留给 M4


class SemanticMemory(SQLModel, table=True):
    """从情景记忆中巩固出来的语义/自传记忆。"""

    __tablename__ = "semantic_memories"

    id: int | None = Field(default=None, primary_key=True)
    perspective: str = Field(default="user", index=True)  # user/self/shared
    type: str = Field(default="fact", index=True)
    content: str
    source_message_ids: str = Field(default="[]")  # JSON list[int]
    confidence: float = Field(default=0.5)
    importance: float = Field(default=0.5)
    valence: float = Field(default=0.0)             # -1 消极 … +1 积极
    strength: float = Field(default=1.0)            # 上次巩固时的强度锚点，当前强度见 effective_strength
    access_count: int = Field(default=0)            # 被用户主动重提、确认的次数（只是被检索到不算）
    last_accessed_at: datetime | None = Field(default=None)  # 上次被用户重提的时间，即衰减锚点
    status: str = Field(default="active", index=True)
    notes: str = Field(default="")
    scope: str = Field(default="", index=True)  # "" 通用；"game:<id>" 只属于某个游戏
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class MemoryEmbedding(SQLModel, table=True):
    """记忆内容的向量缓存：本地向量模型算出，内容或模型变了就重算。"""

    __tablename__ = "memory_embeddings"

    memory_id: int = Field(primary_key=True)
    model: str
    content_hash: str
    vector: bytes


class MemoryLink(SQLModel, table=True):
    """记忆之间的联想边，为 M3 的图检索预留。"""

    __tablename__ = "memory_links"

    id: int | None = Field(default=None, primary_key=True)
    source_id: int = Field(index=True)
    target_id: int = Field(index=True)
    relation: str = Field(default="related")
    weight: float = Field(default=1.0)
    created_at: datetime = Field(default_factory=utcnow)


class MemoryRedaction(SQLModel, table=True):
    """用户要求忘掉或纠正后，不再被当作旧对话片段想起的消息。时间线里原文照旧，只是不再参与回忆。"""

    __tablename__ = "memory_redactions"

    id: int | None = Field(default=None, primary_key=True)
    message_id: int = Field(index=True, unique=True)
    reason: str = Field(default="")
    created_at: datetime = Field(default_factory=utcnow)


class MemoryExtractionRun(SQLModel, table=True):
    __tablename__ = "memory_extraction_runs"

    id: int | None = Field(default=None, primary_key=True)
    source_message_ids: str = Field(default="[]")
    provider: str = Field(default="")
    model: str = Field(default="")
    status: str = Field(default="pending")
    candidates_json: str = Field(default="[]")
    error: str = Field(default="")
    created_at: datetime = Field(default_factory=utcnow)


class ApiCall(SQLModel, table=True):
    __tablename__ = "api_calls"

    id: int | None = Field(default=None, primary_key=True)
    purpose: str = Field(default="chat", index=True)
    provider: str = Field(default="")
    model: str = Field(default="")
    prompt_tokens: int = Field(default=0)
    completion_tokens: int = Field(default=0)
    total_tokens: int = Field(default=0)
    latency_ms: int = Field(default=0)
    status: str = Field(default="ok", index=True)
    error: str = Field(default="")
    created_at: datetime = Field(default_factory=utcnow, index=True)


class AgentDiary(SQLModel, table=True):
    """她的第一人称日记。完整内容只用于她自己的长期记忆，不通过 API 返回给用户。"""

    __tablename__ = "agent_diary"

    id: int | None = Field(default=None, primary_key=True)
    entry_type: str = Field(default="daily", index=True)  # turn/daily/weekly/manual
    content: str
    summary: str = Field(default="")  # 她愿意给用户看的一句话摘要
    mood_before: str = Field(default="")
    mood_after: str = Field(default="")
    source_message_ids: str = Field(default="[]")
    created_at: datetime = Field(default_factory=utcnow, index=True)


class AgentState(SQLModel, table=True):
    """她自己的连续状态，属于她本人，不是用户档案。"""

    __tablename__ = "agent_state"

    id: int = Field(default=1, primary_key=True)
    mood: str = Field(default="专注")
    energy: float = Field(default=0.70)
    focus: str = Field(default="等对方说明来意")
    need: str = Field(default="")
    inner_note: str = Field(default="")
    last_forgetting_at: datetime | None = Field(default=None)
    last_consolidation_at: datetime | None = Field(default=None)
    updated_at: datetime = Field(default_factory=utcnow)

class RelationshipState(SQLModel, table=True):
    """关系/情绪连续状态，M2 只建档，M5 起真正驱动回复。"""

    __tablename__ = "relationship_state"

    id: int = Field(default=1, primary_key=True)
    affection: float = Field(default=0.50)   # 好感度 0..1，初始 50% 左右
    familiarity: float = Field(default=0.30) # 熟悉度 0..1
    trust: float = Field(default=0.35)       # 信任度 0..1
    mood: str = Field(default="专注")
    energy: float = Field(default=0.70)
    notes: str = Field(default="")
    updated_at: datetime = Field(default_factory=utcnow)


class AffectionEvent(SQLModel, table=True):
    """好感度变化流水。为什么涨、为什么跌，都要有理由。"""

    __tablename__ = "affection_events"

    id: int | None = Field(default=None, primary_key=True)
    source_message_id: int | None = Field(default=None, index=True)
    delta: float
    level_before: float
    level_after: float
    reason: str
    created_at: datetime = Field(default_factory=utcnow, index=True)


class MemoryConsolidationRun(SQLModel, table=True):
    """记忆整理流水：合并、休眠、纠错、建立联想。"""

    __tablename__ = "memory_consolidation_runs"

    id: int | None = Field(default=None, primary_key=True)
    trigger: str = Field(default="manual", index=True)  # manual/diary/scheduled
    provider: str = Field(default="")
    model: str = Field(default="")
    status: str = Field(default="pending")  # pending/success/fallback/failed/skipped
    actions_json: str = Field(default="[]")
    error: str = Field(default="")
    created_at: datetime = Field(default_factory=utcnow, index=True)



class WeChatAccount(SQLModel, table=True):
    """iLink Bot 微信账号。只做私聊，群聊后续再评估。"""

    __tablename__ = "wechat_account"

    id: int = Field(default=1, primary_key=True)
    enabled: bool = Field(default=False)
    name: str = Field(default="微信")
    account_id: str = Field(default="")
    token: str = Field(default="", sa_type=EncryptedStr)
    base_url: str = Field(default="https://ilinkai.weixin.qq.com")
    cdn_base_url: str = Field(default="https://novac2c.cdn.weixin.qq.com/c2c")
    user_id: str = Field(default="")
    owner_user_id: str = Field(default="")  # 绑定为“你”的微信 ID；空表示等待首次私聊配对
    status: str = Field(default="stopped")  # stopped/connecting/connected/error
    last_sync_buf: str = Field(default="")
    context_tokens: str = Field(default="{}")  # JSON: {peer_user_id: context_token}
    last_error: str = Field(default="")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)



class ToolConfig(SQLModel, table=True):
    """只读文件、网页搜索等本地工具的配置。"""

    __tablename__ = "tool_config"

    id: int = Field(default=1, primary_key=True)
    files_enabled: bool = Field(default=False)
    file_allowlist: str = Field(default="[]")  # JSON list[str]，允许读取的目录
    search_provider: str = Field(default="duckduckgo")  # duckduckgo / brave / tavily / serper
    search_api_key: str = Field(default="", sa_type=EncryptedStr)
    screen_enabled: bool = Field(default=False)
    vision_base_url: str = Field(default="")
    vision_model: str = Field(default="")
    vision_api_key: str = Field(default="", sa_type=EncryptedStr)
    agent_enabled: bool = Field(default=False)
    agent_command: str = Field(default="claude")
    agent_permission: str = Field(default="readonly")  # readonly / write
    agent_allowlist: str = Field(default="[]")
    agent_timeout_seconds: int = Field(default=600)
    agent_max_output_chars: int = Field(default=12000)
    agent_max_budget_usd: float = Field(default=5.0)
    updated_at: datetime = Field(default_factory=utcnow)


class AgentTask(SQLModel, table=True):
    """委托给本地 CLI 子代理的一项任务：任务卡 -> 用户确认 -> 后台执行 -> 回执与通知。"""

    __tablename__ = "agent_tasks"

    id: int | None = Field(default=None, primary_key=True)
    title: str
    prompt: str                                      # 交给子代理的完整任务
    spoken_summary: str = Field(default="")          # 一句口语摘要：语音确认与完成通知用
    command: str                                     # claude / codex
    permission: str = Field(default="readonly")      # readonly / write
    cwd: str
    # proposed 待确认 / running 执行中 / completed / failed / cancelled / expired / interrupted
    status: str = Field(default="proposed", index=True)
    parent_id: int | None = Field(default=None)      # 续接的上一个任务
    agent_session_id: str = Field(default="")        # codex thread_id / claude session_id，续接用
    target: str = Field(default="")                  # "" 她自己开的会话；codex_app 接着用户在 Codex App 里的线程
    target_title: str = Field(default="")            # codex_app 线程的标题
    channel: str = Field(default="web")              # 发起渠道，决定完成后怎样通知
    output: str = Field(default="")
    error: str = Field(default="")
    duration_ms: int = Field(default=0)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    confirmed_at: datetime | None = Field(default=None)
    finished_at: datetime | None = Field(default=None)


class ProactiveConfig(SQLModel, table=True):
    """她主动发微信的节奏设置。默认关闭，由用户在设置页开启。"""

    __tablename__ = "proactive_config"

    id: int = Field(default=1, primary_key=True)
    enabled: bool = Field(default=False)
    daily_max: int = Field(default=2)
    quiet_start: str = Field(default="23:00")  # 本地时间 HH:MM
    quiet_end: str = Field(default="09:00")
    min_idle_minutes: int = Field(default=120)  # 对方最近说过话的这段时间内不主动打扰
    updated_at: datetime = Field(default_factory=utcnow)


class ProactiveEvent(SQLModel, table=True):
    """每次主动联系的决定：发送、她选择不发、条件不满足或失败，都留痕。"""

    __tablename__ = "proactive_events"

    id: int | None = Field(default=None, primary_key=True)
    status: str = Field(index=True)  # sent / declined / skipped / failed
    reason: str = Field(default="")
    message_id: int | None = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow, index=True)


class PushSubscription(SQLModel, table=True):
    """手机 App（PWA）的 Web Push 订阅；推送服务返回 404/410 时自动删除。"""

    __tablename__ = "push_subscriptions"

    id: int | None = Field(default=None, primary_key=True)
    endpoint: str = Field(index=True, unique=True)
    p256dh: str
    auth: str
    user_agent: str = Field(default="")
    created_at: datetime = Field(default_factory=utcnow)
    last_sent_at: datetime | None = Field(default=None)


class ScreenCompanionConfig(SQLModel, table=True):
    """“陪你用电脑”：只读、低频地看名单内应用的窗口。默认关闭，首次开启需用户同意截图外发。"""

    __tablename__ = "screen_companion_config"

    id: int = Field(default=1, primary_key=True)
    enabled: bool = Field(default=False)
    allowed_apps: str = Field(default="[]")  # JSON 数组，应用名与 macOS 窗口所属应用名一致
    interval_minutes: int = Field(default=5)
    remark_gap_minutes: int = Field(default=45)  # 两次开口的最短间隔；0 表示只看不说
    notify: bool = Field(default=True)  # 开口时同时弹系统通知
    consent_at: datetime | None = Field(default=None)
    paused_until: datetime | None = Field(default=None)
    updated_at: datetime = Field(default_factory=utcnow)


class ScreenObservation(SQLModel, table=True):
    """她看到的概括（不存截图），保留 7 天，用户可随时清除。"""

    __tablename__ = "screen_observations"

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    app: str = Field(default="")
    summary: str = Field(default="")
    detail: str = Field(default="")
    notable: bool = Field(default=False)
    message_id: int | None = Field(default=None)  # 她因此开口时对应的时间线消息
    game_id: int | None = Field(default=None, index=True)
    session_id: int | None = Field(default=None, index=True)
    scene: str = Field(default="")  # 游戏画面：combat / result / death / menu / loading / map / dialog / other
    event: str = Field(default="")  # victory / boss_defeated / death / level_up / unlock / none
    progress: str = Field(default="")


class GameProfile(SQLModel, table=True):
    """陪玩的游戏档案：由用户添加，带陪玩角色与“存档卡”（她记得的当前进度）。"""

    __tablename__ = "game_profiles"

    id: int | None = Field(default=None, primary_key=True)
    name: str
    app_names: str = Field(default="[]")  # JSON 数组：窗口所属应用名
    aliases: str = Field(default="[]")  # JSON 数组：聊天里对这个游戏的叫法，如“尖塔”“STS”
    genre: str = Field(default="")
    hud_hints: str = Field(default="")
    role: str = Field(default="spectator")  # recorder / spectator / partner / coach
    interval_minutes: int = Field(default=2)
    recap_enabled: bool = Field(default=True)
    save_card: str = Field(default="{}")  # 账号档案 JSON：progress / roster / build / resources / goals / struggles / preferences / notes
    knowledge_file: str = Field(default="")  # knowledge/games/ 下的文件名，可分享
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class GameSession(SQLModel, table=True):
    """一场游戏：游戏出现在前台时开始，离开 15 分钟后结束并写战报。"""

    __tablename__ = "game_sessions"

    id: int | None = Field(default=None, primary_key=True)
    game_id: int = Field(index=True)
    started_at: datetime = Field(default_factory=utcnow, index=True)
    last_seen_at: datetime = Field(default_factory=utcnow)
    ended_at: datetime | None = Field(default=None)
    remark_count: int = Field(default=0)
    last_remark_at: datetime | None = Field(default=None)
    recap: str = Field(default="")
    message_id: int | None = Field(default=None)


class GameMilestone(SQLModel, table=True):
    """账号档案里的里程碑：打倒 Boss、通关、解锁、升级等，主要由她在陪玩时记下。"""

    __tablename__ = "game_milestones"

    id: int | None = Field(default=None, primary_key=True)
    game_id: int = Field(index=True)
    at: datetime = Field(default_factory=utcnow, index=True)
    kind: str = Field(default="")  # boss_defeated / victory / unlock / level_up / recap / chat
    text: str
    session_id: int | None = Field(default=None)
