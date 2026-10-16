import type {
  AgentRunResponse,
  AgentTask,
  AgentTaskAction,
  CodexThread,
  AgentToolConfig,
  AppState,
  ChatResponse,
  CompanionConfig,
  CompanionObservation,
  CompanionStatus,
  DiaryEntry,
  LLMConfig,
  LLMTestResponse,
  Memory,
  MemoryPerspective,
  Message,
  FileToolConfig,
  Game,
  GameDraft,
  GameKnowledge,
  GameKnowledgeResult,
  GameMilestone,
  GameRole,
  GameSaveCard,
  GameSession,
  ProviderPresetsResponse,
  RelationshipState,
  ScreenToolConfig,
  SearchToolConfig,
  TTSResponse,
  TTSStatus,
  UsageSummary,
  WeChatLoginStart,
  WeChatPairingStart,
  ProactiveConfig,
  ProactiveEvent,
  WeChatLoginStatus,
  WeChatStatus,
} from "./types";

export interface ChatOptions {
  reply_length?: "short" | "long";
  action_text?: boolean;
}

export type ChatStreamEvent = Record<string, unknown> & { type?: string };

async function errorDetail(response: Response): Promise<string> {
  let detail = `HTTP ${response.status}`;
  try {
    const body = await response.json();
    if (typeof body.detail === "string") detail = body.detail;
    else if (body.detail) detail = JSON.stringify(body.detail);
  } catch {
    // 响应不是 JSON 时保留状态码
  }
  return detail;
}

async function streamEvents(
  path: string,
  body: unknown,
  onEvent: (event: ChatStreamEvent) => void,
): Promise<void> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Sandrone-Local": "1" },
    body: JSON.stringify(body),
  });
  if (!response.ok || !response.body) throw new Error(await errorDetail(response));

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? "";
    for (const part of parts) {
      const line = part.split("\n").find((item) => item.startsWith("data:"));
      if (!line) continue;
      try {
        onEvent(JSON.parse(line.slice(5).trim()) as ChatStreamEvent);
      } catch {
        // 忽略半包 JSON
      }
    }
  }
}

/** 流式对话：逐个回调 SSE 事件（thinking / tool_call / tool_result / final / error）。 */
export function streamChat(
  content: string,
  options: ChatOptions,
  onEvent: (event: ChatStreamEvent) => void,
): Promise<void> {
  return streamEvents("/api/chat/stream", { content, ...options }, onEvent);
}

/** 重新生成最新一条回复；成功的 final 事件带 replaced_message_id。 */
export function streamRegenerate(
  messageId: number,
  options: ChatOptions,
  onEvent: (event: ChatStreamEvent) => void,
): Promise<void> {
  return streamEvents(`/api/messages/${messageId}/regenerate`, options, onEvent);
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", "X-Sandrone-Local": "1" },
    ...options,
  });

  if (!response.ok) throw new Error(await errorDetail(response));
  return (await response.json()) as T;
}

export const api = {
  getState: () => request<AppState>("/api/state"),

  getMessages: (limit = 100, beforeId?: number) => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (beforeId) params.set("before_id", String(beforeId));
    return request<Message[]>(`/api/messages?${params}`);
  },

  getPushKey: () => request<{ public_key: string }>("/api/push/key"),

  subscribePush: (subscription: PushSubscriptionJSON) =>
    request<{ ok: boolean }>("/api/push/subscribe", {
      method: "POST",
      body: JSON.stringify(subscription),
    }),

  unsubscribePush: (endpoint: string) =>
    request<{ ok: boolean }>("/api/push/unsubscribe", {
      method: "POST",
      body: JSON.stringify({ endpoint }),
    }),

  testPush: () => request<{ ok: boolean; sent: number }>("/api/push/test", { method: "POST" }),

  getVoiceStatus: () =>
    request<{ models_present: boolean; asr_ready: boolean; error: string }>("/api/voice/status"),

  deleteMessage: (id: number) =>
    request<{ ok: boolean; deleted_id: number }>(`/api/messages/${id}`, { method: "DELETE" }),

  sendChat: (content: string) =>
    request<ChatResponse>("/api/chat", {
      method: "POST",
      body: JSON.stringify({ content }),
    }),

  getMemories: (perspective?: MemoryPerspective | "all", status = "active") => {
    const params = new URLSearchParams({ status });
    if (perspective && perspective !== "all") params.set("perspective", perspective);
    return request<Memory[]>(`/api/memories?${params}`);
  },

  getRelationship: () => request<RelationshipState>("/api/relationship"),

  updateMemory: (id: number, data: Partial<Memory>) =>
    request<Memory>(`/api/memories/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),

  deleteMemory: (id: number) => request<Memory>(`/api/memories/${id}`, { method: "DELETE" }),

  getDiary: (limit = 30) => request<DiaryEntry[]>(`/api/diary?limit=${limit}`),

  reflectDiary: () =>
    request<{ accepted: boolean; message: string }>("/api/diary/reflect", {
      method: "POST",
    }),

  consolidateMemories: () =>
    request<{ accepted: boolean; run_id: number | null; status: string; message: string }>(
      "/api/memories/consolidate",
      { method: "POST" },
    ),

  getLLMConfig: () => request<LLMConfig>("/api/llm-config"),

  getProviderPresets: () => request<ProviderPresetsResponse>("/api/provider-presets"),

  updateLLMConfig: (data: Record<string, unknown>) =>
    request<LLMConfig>("/api/llm-config", {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  testLLMConfig: (data: Record<string, unknown>) =>
    request<LLMTestResponse>("/api/llm-config/test", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  getWeChatStatus: () => request<WeChatStatus>("/api/wechat/status"),

  updateWeChatConfig: (data: Record<string, unknown>) =>
    request<WeChatStatus>("/api/wechat/config", {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  getProactiveConfig: () => request<ProactiveConfig>("/api/proactive/config"),

  updateProactiveConfig: (data: Partial<Omit<ProactiveConfig, "updated_at">>) =>
    request<ProactiveConfig>("/api/proactive/config", {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  getProactiveEvents: (limit = 10) =>
    request<ProactiveEvent[]>(`/api/proactive/events?limit=${limit}`),

  previewProactive: () =>
    request<{ send: boolean; text: string }>("/api/proactive/preview", {
      method: "POST",
    }),

  sendProactiveNow: () => request<ProactiveEvent>("/api/proactive/send-now", { method: "POST" }),

  getCompanionConfig: () => request<CompanionConfig>("/api/companion/config"),

  updateCompanionConfig: (
    data: Partial<
      Pick<
        CompanionConfig,
        "enabled" | "allowed_apps" | "interval_minutes" | "remark_gap_minutes" | "notify"
      >
    > & { consent?: boolean; stop_watch?: boolean },
  ) =>
    request<CompanionConfig>("/api/companion/config", {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  pauseCompanion: (minutes: number) =>
    request<CompanionConfig>("/api/companion/pause", {
      method: "POST",
      body: JSON.stringify({ minutes }),
    }),

  getCompanionStatus: () => request<CompanionStatus>("/api/companion/status"),

  getRunningApps: () => request<string[]>("/api/companion/apps"),

  checkCompanionNow: () =>
    request<{ result: string; status: CompanionStatus }>("/api/companion/check", {
      method: "POST",
    }),

  getCompanionObservations: (limit = 10) =>
    request<CompanionObservation[]>(`/api/companion/observations?limit=${limit}`),

  getGameRoles: () => request<GameRole[]>("/api/games/roles"),

  getGames: () => request<Game[]>("/api/games"),

  createGame: (data: GameDraft) =>
    request<Game>("/api/games", { method: "POST", body: JSON.stringify(data) }),

  updateGame: (id: number, data: Partial<GameDraft> & { save_card?: GameSaveCard }) =>
    request<Game>(`/api/games/${id}`, { method: "PUT", body: JSON.stringify(data) }),

  deleteGame: (id: number) => request<{ ok: boolean }>(`/api/games/${id}`, { method: "DELETE" }),

  getGameSessions: (id: number, limit = 10) =>
    request<GameSession[]>(`/api/games/${id}/sessions?limit=${limit}`),

  getGameMemories: (id: number) => request<Memory[]>(`/api/games/${id}/memories`),

  getGameKnowledge: (id: number) => request<GameKnowledge>(`/api/games/${id}/knowledge`),

  updateGameKnowledge: (id: number, content: string) =>
    request<GameKnowledge>(`/api/games/${id}/knowledge`, {
      method: "PUT",
      body: JSON.stringify({ content }),
    }),

  importGameNote: (id: number, title: string, text: string) =>
    request<GameKnowledgeResult>(`/api/games/${id}/notes`, {
      method: "POST",
      body: JSON.stringify({ title, text }),
    }),

  researchGame: (id: number, topic: string) =>
    request<GameKnowledgeResult>(`/api/games/${id}/research`, {
      method: "POST",
      body: JSON.stringify({ topic }),
    }),

  getGameMilestones: (id: number) => request<GameMilestone[]>(`/api/games/${id}/milestones`),

  addGameMilestone: (id: number, text: string) =>
    request<GameMilestone>(`/api/games/${id}/milestones`, {
      method: "POST",
      body: JSON.stringify({ text }),
    }),

  deleteGameMilestone: (id: number, milestoneId: number) =>
    request<{ ok: boolean }>(`/api/games/${id}/milestones/${milestoneId}`, { method: "DELETE" }),

  clearCompanionObservations: () =>
    request<{ deleted: number }>("/api/companion/observations", { method: "DELETE" }),

  startWeChatPairing: () =>
    request<WeChatPairingStart>("/api/wechat/pairing/start", { method: "POST" }),

  startWeChatLogin: () => request<WeChatLoginStart>("/api/wechat/login/start", { method: "POST" }),

  pollWeChatLogin: (qrcode: string) =>
    request<WeChatLoginStatus>(`/api/wechat/login/status?qrcode=${encodeURIComponent(qrcode)}`),

  logoutWeChat: () =>
    request<{ ok: boolean; message: string }>("/api/wechat/logout", {
      method: "POST",
    }),

  getFileToolConfig: () => request<FileToolConfig>("/api/tools/files/config"),

  updateFileToolConfig: (data: { files_enabled?: boolean; file_allowlist?: string[] }) =>
    request<FileToolConfig>("/api/tools/files/config", {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  getSearchToolConfig: () => request<SearchToolConfig>("/api/tools/search/config"),

  updateSearchToolConfig: (data: { search_provider?: string; api_key?: string | null }) =>
    request<SearchToolConfig>("/api/tools/search/config", {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  getScreenToolConfig: () => request<ScreenToolConfig>("/api/tools/screen/config"),

  updateScreenToolConfig: (data: {
    screen_enabled?: boolean;
    vision_base_url?: string;
    vision_model?: string;
    vision_api_key?: string | null;
  }) =>
    request<ScreenToolConfig>("/api/tools/screen/config", {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  getAgentToolConfig: () => request<AgentToolConfig>("/api/tools/agent/config"),

  updateAgentToolConfig: (data: {
    agent_enabled?: boolean;
    agent_command?: string;
    agent_permission?: "readonly" | "write";
    agent_allowlist?: string[];
    agent_timeout_seconds?: number;
    agent_max_output_chars?: number;
    agent_max_budget_usd?: number;
  }) =>
    request<AgentToolConfig>("/api/tools/agent/config", {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  runAgentTool: (data: { prompt: string; cwd?: string }) =>
    request<AgentRunResponse>("/api/tools/agent/run", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  listAgentTasks: (limit = 20) => request<AgentTask[]>(`/api/agent-tasks?limit=${limit}`),

  confirmAgentTask: (id: number) =>
    request<AgentTaskAction>(`/api/agent-tasks/${id}/confirm`, { method: "POST" }),

  cancelAgentTask: (id: number) =>
    request<AgentTaskAction>(`/api/agent-tasks/${id}/cancel`, { method: "POST" }),

  setAgentTaskThread: (id: number, threadId: string) =>
    request<AgentTaskAction>(`/api/agent-tasks/${id}/thread`, {
      method: "PUT",
      body: JSON.stringify({ thread_id: threadId }),
    }),

  listCodexThreads: (query = "") =>
    request<CodexThread[]>(`/api/agent-tasks/codex-threads?q=${encodeURIComponent(query)}`),

  getTTSStatus: () => request<TTSStatus>("/api/tts/status"),

  synthesizeTTS: (text: string, messageId?: number) =>
    request<TTSResponse>("/api/tts", {
      method: "POST",
      body: JSON.stringify({ text, message_id: messageId ?? null }),
    }),

  getUsageSummary: (params: { startAt?: string; provider?: string; purpose?: string }) => {
    const query = new URLSearchParams();
    if (params.startAt) query.set("start_at", params.startAt);
    if (params.provider) query.set("provider", params.provider);
    if (params.purpose) query.set("purpose", params.purpose);
    return request<UsageSummary>(`/api/usage/summary?${query}`);
  },
};
