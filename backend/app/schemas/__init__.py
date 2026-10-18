"""后端公共 Pydantic 契约，按领域分文件；这里统一导出，`from ..schemas import X` 照旧可用。"""

from .providers import (
    PROVIDER_PRESETS,
    LLMConfigUpdate,
    LLMConfigPublic,
    LLMTestRequest,
    LLMTestResponse,
)
from .usage import (
    UsageMetrics,
    UsagePurposeBreakdown,
    UsageModelBreakdown,
    UsageErrorPublic,
    UsageSummaryResponse,
)
from .chat import (
    ReplyStyleRequest,
    ChatRequest,
    MemoryTracePublic,
    MessagePublic,
    ToolCallTrace,
    ChatResponse,
)
from .memory import (
    MemoryCreate,
    MemoryUpdate,
    MemoryPublic,
    DiaryPublic,
    AgentStatePublic,
    AffectionEventPublic,
    RelationshipPublic,
    ReflectResponse,
    ConsolidateResponse,
)
from .wechat import (
    WeChatConfigUpdate,
    WeChatStatus,
    WeChatLoginStart,
    WeChatLoginStatus,
    WeChatPairingStart,
    ProactiveConfigUpdate,
    ProactiveConfigPublic,
    ProactiveEventPublic,
    ProactivePreview,
    WeChatSendRequest,
    WeChatSendResponse,
)
from .tools import (
    AgentAvailability,
    AgentPermissionAnswer,
    AgentWorkspacePublic,
    FileToolConfig,
    FileToolConfigUpdate,
    FileReadRequest,
    FileReadResult,
    SearchToolConfig,
    SearchToolConfigUpdate,
    SearchRequest,
    SearchResultItem,
    SearchResponse,
    ScreenToolConfig,
    ScreenToolConfigUpdate,
    ScreenAnalyzeRequest,
    ScreenAnalyzeResponse,
    AgentToolConfig,
    AgentToolConfigUpdate,
    AgentRunRequest,
    AgentRunResponse,
    AgentTaskPublic,
    AgentTaskActionResponse,
    CodexThreadPublic,
    AgentTaskThreadUpdate,
)
from .state import (
    AppState,
    TTSStatus,
)
from .companion import (
    CompanionConfigUpdate,
    CompanionConfigPublic,
    CompanionPauseRequest,
    CompanionStatusPublic,
    CompanionObservationPublic,
    CompanionCheckResult,
)
from .games import (
    GameSaveCard,
    GameCreate,
    GameUpdate,
    GamePublic,
    GameRolePublic,
    GameSessionPublic,
    GameKnowledgeSource,
    GameKnowledgePublic,
    GameKnowledgeUpdate,
    GameNoteImport,
    GameResearchRequest,
    GameKnowledgeResult,
    GameMilestonePublic,
    GameMilestoneCreate,
)
