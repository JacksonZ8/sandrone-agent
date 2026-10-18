import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "../api";
import ui from "../ui/ui.module.css";
import AgentSettingsCard from "./settings/AgentSettingsCard";
import CapabilitiesCard from "./settings/CapabilitiesCard";
import FileSearchSettingsCard from "./settings/FileSearchSettingsCard";
import LLMSettingsCard from "./settings/LLMSettingsCard";
import CompanionSettingsCard from "./settings/CompanionSettingsCard";
import ProactiveSettingsCard from "./settings/ProactiveSettingsCard";
import ScreenSettingsCard from "./settings/ScreenSettingsCard";
import { sectionStatus, type SectionId, type SettingsSnapshot } from "./settings/sectionStatus";
import styles from "./SettingsPanel.module.css";
import TTSStatusCard from "./settings/TTSStatusCard";
import WeChatSettingsCard from "./settings/WeChatSettingsCard";

interface Props {
  onConfigSaved: () => void;
}

const SECTIONS: { id: SectionId; label: string; title: string }[] = [
  { id: "model", label: "模型", title: "对话模型" },
  { id: "abilities", label: "她能做什么", title: "她能做什么" },
  { id: "wechat", label: "微信", title: "微信" },
  { id: "voice", label: "语音", title: "语音" },
  { id: "advanced", label: "高级", title: "高级" },
];

/** 每张卡各自保存，保存后它们不通知这里：页内点过按钮后稍等再刷新一次分区状态 */
const REFRESH_AFTER_CLICK_MS = 1500;

async function loadSnapshot(): Promise<SettingsSnapshot> {
  const pick = <T,>(result: PromiseSettledResult<T>) =>
    result.status === "fulfilled" ? result.value : null;
  const [llm, wechat, proactive, files, screen, companion, agent, tts] = await Promise.allSettled([
    api.getLLMConfig(),
    api.getWeChatStatus(),
    api.getProactiveConfig(),
    api.getFileToolConfig(),
    api.getScreenToolConfig(),
    api.getCompanionConfig(),
    api.getAgentToolConfig(),
    api.getTTSStatus(),
  ]);
  return {
    llm: pick(llm),
    wechat: pick(wechat),
    proactive: pick(proactive),
    files: pick(files),
    screen: pick(screen),
    companion: pick(companion),
    agent: pick(agent),
    tts: pick(tts),
  };
}

/** 模型、微信与工具：一页按类别分区，顶部吸顶导航跳到分区，分区标题旁显示当前状态。 */
export default function SettingsPanel({ onConfigSaved }: Props) {
  const [snapshot, setSnapshot] = useState<SettingsSnapshot>({});
  const [active, setActive] = useState<SectionId>("model");
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const timer = useRef(0);

  const refresh = useCallback(() => {
    void loadSnapshot().then(setSnapshot);
  }, []);

  useEffect(() => {
    let cancelled = false;
    void loadSnapshot().then((value) => {
      if (!cancelled) setSnapshot(value);
    });
    return () => {
      cancelled = true;
      window.clearTimeout(timer.current);
    };
  }, []);

  const saved = useCallback(() => {
    onConfigSaved();
    refresh();
  }, [onConfigSaved, refresh]);

  // 滚动时高亮当前分区
  const sectionsRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const root = sectionsRef.current;
    if (!root) return;
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
        if (visible) setActive(visible.target.id.replace("settings-", "") as SectionId);
      },
      { rootMargin: "-80px 0px -60% 0px" },
    );
    root.querySelectorAll("section[id]").forEach((section) => observer.observe(section));
    return () => observer.disconnect();
  }, []);

  const jump = (id: SectionId) => {
    setActive(id);
    if (id === "advanced") setAdvancedOpen(true);
    document
      .getElementById(`settings-${id}`)
      ?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const section = (id: SectionId, children: ReactNode) => {
    const meta = SECTIONS.find((item) => item.id === id)!;
    const status = sectionStatus(id, snapshot);
    return (
      <section
        id={`settings-${id}`}
        className={ui.section}
        aria-labelledby={`settings-${id}-title`}
      >
        <div className={ui.sectionHead}>
          <h2 id={`settings-${id}-title`} className={ui.sectionTitle}>
            {meta.title}
          </h2>
          {status && (
            <span className={ui.badge} data-tone={status.tone}>
              {status.text}
            </span>
          )}
        </div>
        {children}
      </section>
    );
  };

  return (
    <div
      className={`${ui.page} settings-panel`}
      onClick={(event) => {
        if (!(event.target as HTMLElement).closest("button")) return;
        window.clearTimeout(timer.current);
        timer.current = window.setTimeout(refresh, REFRESH_AFTER_CLICK_MS);
      }}
    >
      <nav className={styles.nav} aria-label="设置分区">
        {SECTIONS.map((item) => (
          <button
            key={item.id}
            type="button"
            className={styles.navItem}
            aria-current={active === item.id ? "true" : undefined}
            onClick={() => jump(item.id)}
          >
            {item.label}
          </button>
        ))}
      </nav>
      <p className={ui.pageLead}>
        一般只需要填对话模型的 Key，再在“她能做什么”里打开想要的能力。设置只保存在这台电脑上。
      </p>

      <div ref={sectionsRef} className={styles.sections}>
        {section("model", <LLMSettingsCard onConfigSaved={saved} />)}
        {section("abilities", <CapabilitiesCard onChanged={saved} />)}
        {section("wechat", <WeChatSettingsCard onConfigSaved={saved} />)}
        {section("voice", <TTSStatusCard />)}
        {section(
          "advanced",
          // 默认收起：额外文件夹、搜索、视觉模型、陪着看的细节、主动消息节奏与子代理
          <details
            className={styles.advanced}
            open={advancedOpen}
            onToggle={(event) => setAdvancedOpen(event.currentTarget.open)}
          >
            <summary>文件夹与搜索、视觉模型、陪着看、主动找你的节奏、子代理</summary>
            <div className={styles.advancedCards}>
              <FileSearchSettingsCard />
              <ScreenSettingsCard />
              <CompanionSettingsCard />
              <ProactiveSettingsCard />
              <AgentSettingsCard />
            </div>
          </details>,
        )}
      </div>
    </div>
  );
}
