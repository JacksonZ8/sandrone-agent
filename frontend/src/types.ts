export type Role = "user" | "assistant";

export interface Message {
  id: number;
  thread_id: string;
  channel: string;
  sender_id: string;
  chat_id: string;
  turn_index: number;
  role: Role;
  content: string;
  created_at: string;
  provider: string;
  model: string;
  latency_ms: number;
  audio_path: string;
  tool_calls: ToolCallTrace[];
  memory_traces: MemoryTrace[];
  /** 她主动发起的消息（主动消息调度发出） */
  initiated: boolean;
}

export interface MemoryTrace {
  layer: "state" | "semantic" | "diary" | "episodic" | "associative";
  perspective: string;
  memory_type: string;
  content: string;
  score: number;
  source_message_id: number | null;
}

export interface ToolCallTrace {
  name: string;
  arguments: Record<string, unknown>;
  result_preview: string;
}

export interface ChatResponse {
  user_message: Message;
  assistant_message: Message | null;
  tool_calls: ToolCallTrace[];
  error: string;
}

export type MemoryPerspective = "user" | "self" | "shared";

export interface Memory {
  id: number;
  perspective: MemoryPerspective;
  type: "fact" | "preference" | "relationship" | "project" | "event";
  content: string;
  source_message_ids: number[];
  confidence: number;
  importance: number;
  valence: number;
  strength: number;
  access_count: number;
  last_accessed_at: string | null;
  status: string;
  notes: string;
  scope?: string;
  created_at: string;
  updated_at: string;
}

export interface DiaryEntry {
  id: number;
  entry_type: string;
  summary: string;
  mood_before: string;
  mood_after: string;
  created_at: string;
}

export interface AgentState {
  mood: string;
  energy: number;
  focus: string;
}

export interface AffectionEvent {
  id: number;
  delta: number;
  level_before: number;
  level_after: number;
  reason: string;
  created_at: string;
}

export interface RelationshipState {
  affection: number;
  familiarity: number;
  trust: number;
  phase: string;
  guidance: string;
  updated_at: string;
  events: AffectionEvent[];
  /** 她推测的你最近的情绪（12 小时内、随时间变淡），和她自己的心情分开 */
  user_emotion: UserEmotion | null;
}

export interface UserEmotion {
  label: string;
  confidence: number;
  source: "llm" | "rule" | string;
  observed_at: string;
}

export interface LLMConfig {
  provider: string;
  provider_label: string;
  base_url: string;
  model: string;
  has_api_key: boolean;
  api_key_masked: string;
  api_key_required: boolean;
  temperature: number;
  max_tokens: number;
  timeout_seconds: number;
  reply_thinking: "auto" | "off" | "low" | "high";
  updated_at: string;
}

export interface ProviderPreset {
  label: string;
  base_url: string;
  model: string;
  api_key_required: boolean;
}

export interface ProviderPresetsResponse {
  presets: Record<string, ProviderPreset>;
}

export interface LLMTestResponse {
  ok: boolean;
  message: string;
  provider: string;
  model: string;
  latency_ms: number;
  reply_preview: string;
}

export interface AppState {
  app_name: string;
  app_version: string;
  message_count: number;
  active_memory_count: number;
  has_api_key: boolean;
  provider: string;
  model: string;
  voice_ready: boolean;
  voice_message: string;
  agent_state: AgentState;
}

export interface WeChatStatus {
  enabled: boolean;
  name: string;
  account_id: string;
  account_id_masked: string;
  has_token: boolean;
  token_masked: string;
  base_url: string;
  owner_user_id: string;
  owner_paired: boolean;
  status: string;
  last_error: string;
  updated_at: string;
}

export interface WeChatLoginStart {
  ok: boolean;
  qrcode: string;
  qr_url: string;
  qr_data_url: string;
  message: string;
}

export interface WeChatPairingStart {
  ok: boolean;
  code: string;
  expires_in_seconds: number;
  message: string;
}

export interface ProactiveConfig {
  enabled: boolean;
  daily_max: number;
  quiet_start: string;
  quiet_end: string;
  min_idle_minutes: number;
  updated_at: string;
}

export interface ProactiveEvent {
  id: number;
  status: "sent" | "declined" | "skipped" | "failed";
  reason: string;
  message_id: number | null;
  created_at: string;
}

export interface WeChatLoginStatus {
  status: string;
  message: string;
  account_id: string;
  base_url: string;
}

export interface FileToolConfig {
  files_enabled: boolean;
  file_allowlist: string[];
}

export interface ScreenToolConfig {
  screen_enabled: boolean;
  vision_base_url: string;
  vision_model: string;
  has_vision_api_key: boolean;
  vision_api_key_masked: string;
}

export interface AgentToolConfig {
  agent_enabled: boolean;
  agent_command: string;
  agent_permission: "readonly" | "write";
  agent_allowlist: string[];
  agent_timeout_seconds: number;
  agent_max_output_chars: number;
  agent_max_budget_usd: number;
  cli_available: boolean;
  cli_version: string;
}

export interface AgentRunResponse {
  ok: boolean;
  command: string;
  cwd: string;
  output: string;
  duration_ms: number;
  message: string;
}

export type AgentTaskStatus =
  "proposed" | "running" | "completed" | "failed" | "cancelled" | "expired" | "interrupted";

/** 委托给子代理的任务卡：确认后在后台执行，做完会通知。 */
export interface AgentTask {
  id: number;
  title: string;
  prompt: string;
  spoken_summary: string;
  command: "claude" | "codex";
  permission: "readonly" | "write";
  cwd: string;
  status: AgentTaskStatus;
  status_label: string;
  parent_id: number | null;
  agent_session_id: string;
  target: "" | "codex_app";
  target_title: string;
  channel: string;
  output: string;
  error: string;
  duration_ms: number;
  created_at: string;
  confirmed_at: string | null;
  finished_at: string | null;
  risks: string[];
  codex_url: string;
}

export interface AgentTaskAction {
  ok: boolean;
  message: string;
  task: AgentTask | null;
}

export interface CodexThread {
  id: string;
  title: string;
  cwd: string;
  updated_at_ms: number;
  recently_active: boolean;
}

export interface SearchToolConfig {
  search_provider: string;
  has_api_key: boolean;
  api_key_masked: string;
}

export interface SearchResultItem {
  title: string;
  url: string;
  snippet: string;
}

export interface TTSStatus {
  ready: boolean;
  voice_id: string;
  engine: string;
  message: string;
}

export interface TTSResponse {
  audio_path: string;
  audio_url: string;
  voice_id: string;
  duration_sec: number;
  cached: boolean;
  text: string;
}

export type UsageRange = "today" | "7d" | "30d" | "all";

export interface UsageMetrics {
  calls: number;
  success_calls: number;
  failed_calls: number;
  other_calls: number;
  success_rate: number | null;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  average_latency_ms: number | null;
  max_latency_ms: number | null;
}

export interface UsagePurposeBreakdown extends UsageMetrics {
  purpose: string;
}

export interface UsageModelBreakdown extends UsageMetrics {
  provider: string;
  model: string;
}

export interface UsageError {
  created_at: string;
  purpose: string;
  provider: string;
  model: string;
  status: string;
  latency_ms: number | null;
  error: string;
}

export interface UsageSummary {
  range: { start_at: string | null; end_at: string | null };
  filters: { provider: string; purpose: string };
  totals: UsageMetrics;
  by_purpose: UsagePurposeBreakdown[];
  by_model: UsageModelBreakdown[];
  recent_errors: UsageError[];
  available_providers: string[];
  available_purposes: string[];
}

export interface CompanionConfig {
  enabled: boolean;
  allowed_apps: string[];
  interval_minutes: number;
  remark_gap_minutes: number;
  notify: boolean;
  consented: boolean;
  paused_until: string | null;
  /** 语音临时开的“陪着看”到几点（没有在临时看时为 null） */
  watch_until: string | null;
  watch_apps: string[];
  vision_base_url: string;
  vision_model: string;
  has_vision_key: boolean;
  updated_at: string;
}

export interface CompanionStatus {
  state: "off" | "paused" | "not_allowed" | "away" | "no_window" | "watching" | "playing" | "error";
  app: string;
  checked_at: string | null;
  seen_at: string | null;
  message: string;
}

export interface CompanionObservation {
  id: number;
  created_at: string;
  app: string;
  summary: string;
  detail: string;
  notable: boolean;
  message_id: number | null;
}

export type GameRoleId = "recorder" | "spectator" | "partner" | "coach";

export interface GameRole {
  id: GameRoleId;
  label: string;
  summary: string;
}

export interface GameSaveCard {
  progress: string;
  roster: string;
  build: string;
  resources: string;
  goals: string;
  struggles: string;
  preferences: string;
  notes: string;
}

export interface Game {
  id: number;
  name: string;
  app_names: string[];
  aliases: string[];
  genre: string;
  hud_hints: string;
  role: GameRoleId;
  interval_minutes: number;
  recap_enabled: boolean;
  save_card: GameSaveCard;
  knowledge_file: string;
  /** 看屏时自动认出、自动加的 */
  auto_detected: boolean;
  created_at: string;
  updated_at: string;
}

export type GameDraft = Pick<
  Game,
  | "name"
  | "app_names"
  | "aliases"
  | "genre"
  | "hud_hints"
  | "role"
  | "interval_minutes"
  | "recap_enabled"
>;

export interface GameSession {
  id: number;
  game_id: number;
  started_at: string;
  last_seen_at: string;
  ended_at: string | null;
  remark_count: number;
  recap: string;
  message_id: number | null;
}

export interface GameKnowledgeSource {
  number: number;
  kind: "note" | "web" | "search" | "play" | "chat";
  title: string;
  url: string;
  chars: number;
  added_at: string;
  pending: boolean;
}

export interface GameKnowledge {
  file: string;
  content: string;
  updated_at: string | null;
  sources: GameKnowledgeSource[];
  pending: number;
}

export interface GameKnowledgeResult extends GameKnowledge {
  added: GameKnowledgeSource[];
  failed: { url: string; error: string }[];
  account_updated: boolean;
}

export interface GameMilestone {
  id: number;
  at: string;
  kind: string;
  text: string;
  session_id: number | null;
}
