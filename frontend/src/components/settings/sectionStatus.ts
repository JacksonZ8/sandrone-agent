// 设置页分区标题旁的状态小标签：从各项配置推出一句话与色调，纯函数便于测试。
import type {
  AgentToolConfig,
  CompanionConfig,
  FileToolConfig,
  LLMConfig,
  ProactiveConfig,
  ScreenToolConfig,
  TTSStatus,
  WeChatStatus,
} from "../../types";

export type Tone = "ok" | "warn" | "off";

export interface SectionStatus {
  text: string;
  tone: Tone;
}

export interface SettingsSnapshot {
  llm?: LLMConfig | null;
  wechat?: WeChatStatus | null;
  proactive?: ProactiveConfig | null;
  files?: FileToolConfig | null;
  screen?: ScreenToolConfig | null;
  companion?: CompanionConfig | null;
  agent?: AgentToolConfig | null;
  tts?: TTSStatus | null;
}

export type SectionId = "model" | "abilities" | "wechat" | "voice" | "advanced";

/** 某项配置还没读到（或读取失败）时不显示标签 */
export function sectionStatus(id: SectionId, s: SettingsSnapshot): SectionStatus | null {
  switch (id) {
    case "model":
      if (!s.llm) return null;
      if (s.llm.provider === "mock") return { text: "Mock 联调", tone: "warn" };
      return s.llm.has_api_key
        ? { text: s.llm.provider_label || s.llm.provider, tone: "ok" }
        : { text: "缺少 API Key", tone: "warn" };
    case "wechat": {
      if (!s.wechat) return null;
      if (!s.wechat.enabled) return { text: "未连接", tone: "off" };
      if (s.wechat.status !== "connected") return { text: "连接异常", tone: "warn" };
      if (!s.wechat.owner_paired) return { text: "已连接 · 未绑定", tone: "warn" };
      return { text: s.proactive?.enabled ? "已连接 · 会主动找你" : "已连接", tone: "ok" };
    }
    case "abilities": {
      const switches = [
        s.files?.files_enabled,
        s.screen?.screen_enabled,
        s.companion?.enabled,
        s.proactive?.enabled,
      ].filter((value) => value !== undefined);
      if (switches.length === 0) return null;
      const on = switches.filter(Boolean).length;
      return on ? { text: `已打开 ${on} 项`, tone: "ok" } : { text: "都没打开", tone: "off" };
    }
    case "voice":
      if (!s.tts) return null;
      return s.tts.ready ? { text: "就绪", tone: "ok" } : { text: "未就绪", tone: "warn" };
    case "advanced":
      return null;
  }
}
