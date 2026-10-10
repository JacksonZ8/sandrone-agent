import type { MemoryTrace, ToolCallTrace } from "../types";

function sameDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

export function dayLabel(iso: string, now = new Date()): string {
  const date = new Date(iso);
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (sameDay(date, now)) return "今天";
  if (sameDay(date, yesterday)) return "昨天";
  return `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日`;
}

const timeFormat = new Intl.DateTimeFormat("zh-CN", {
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

export function timeLabel(iso: string): string {
  return timeFormat.format(new Date(iso));
}

export function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms <= 0) return "";
  const totalSeconds = Math.max(1, Math.round(ms / 1000));
  if (totalSeconds < 60) return `${ms < 10000 ? (ms / 1000).toFixed(1) : totalSeconds}s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes < 60) return `${minutes}m ${seconds}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

const TOOL_LABELS: Record<string, string> = {
  read_file: "读文件",
  web_search: "网页搜索",
  web_fetch: "读网页",
  update_game_record: "更新账号档案",
  note_game_knowledge: "记下游戏知识",
  look_at_screen: "看屏幕",
  delegate_agent: "委托子代理",
};

export function toolLabel(name: string): string {
  return TOOL_LABELS[name] ?? name;
}

export function toolArgPreview(args: Record<string, unknown>): string {
  const value =
    args.path || args.query || args.url || args.question || args.prompt || args.text || args.game;
  return value ? String(value) : JSON.stringify(args);
}

export function toolTraceArg(trace: ToolCallTrace): string {
  return toolArgPreview(trace.arguments);
}

const LAYER_LABELS: Record<MemoryTrace["layer"], string> = {
  state: "状态",
  semantic: "长期记忆",
  diary: "日记",
  episodic: "旧对话",
  associative: "联想记忆",
};

export function memoryLayerLabel(layer: MemoryTrace["layer"]): string {
  return LAYER_LABELS[layer] ?? layer;
}

const MEMORY_TYPE_LABELS: Record<string, string> = {
  fact: "事实",
  preference: "偏好",
  relationship: "关系",
  project: "项目",
  event: "经历",
  diary: "日记",
};

const PERSPECTIVE_LABELS: Record<string, string> = {
  user: "关于你",
  self: "关于她",
  shared: "我们之间",
};

export function memoryTypeLabel(trace: MemoryTrace): string {
  return MEMORY_TYPE_LABELS[trace.memory_type] ?? PERSPECTIVE_LABELS[trace.perspective] ?? "";
}

/** 整段被全角或半角括号包住的段落视为动作描写 */
export function isActionLine(text: string): boolean {
  const trimmed = text.trim();
  return /^[（(][^（()）]*[）)]$/.test(trimmed) && trimmed.length > 2;
}

/** 念出来的文字：去掉括号里的动作描写与 Markdown 记号 */
export function spokenText(content: string): string {
  return content
    .replace(/[（(][^（()）]*[）)]/g, "")
    .replace(/[*_`#>~]+/g, "")
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\n{2,}/g, "\n")
    .trim();
}
