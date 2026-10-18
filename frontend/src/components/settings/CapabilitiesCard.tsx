import { useEffect, useState, type ReactNode } from "react";
import { api } from "../../api";
import type {
  CompanionConfig,
  FileToolConfig,
  ProactiveConfig,
  ScreenToolConfig,
  WeChatStatus,
} from "../../types";
import ui from "../../ui/ui.module.css";

interface Props {
  onChanged: () => void;
}

interface State {
  files: FileToolConfig;
  screen: ScreenToolConfig;
  companion: CompanionConfig;
  proactive: ProactiveConfig;
  wechat: WeChatStatus | null;
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** 她能做什么：每项一个开关，打开即生效；细节（文件夹、不看的应用、频率、视觉模型）在“高级”里。 */
export default function CapabilitiesCard({ onChanged }: Props) {
  const [state, setState] = useState<State | null>(null);
  const [busy, setBusy] = useState("");
  const [askConsent, setAskConsent] = useState(false);
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let cancelled = false;
    void Promise.all([
      api.getFileToolConfig(),
      api.getScreenToolConfig(),
      api.getCompanionConfig(),
      api.getProactiveConfig(),
      api.getWeChatStatus().catch(() => null),
    ]).then(
      ([files, screen, companion, proactive, wechat]) => {
        if (!cancelled) setState({ files, screen, companion, proactive, wechat });
      },
      (error: unknown) => !cancelled && setNotice(errorText(error)),
    );
    return () => {
      cancelled = true;
    };
  }, []);

  async function change(key: string, action: () => Promise<Partial<State>>) {
    setBusy(key);
    setNotice("");
    try {
      const next = await action();
      setState((current) => (current ? { ...current, ...next } : current));
      onChanged();
    } catch (error) {
      setNotice(errorText(error));
    } finally {
      setBusy("");
    }
  }

  if (!state) {
    return <div className="chat-hint">{notice || "正在读取…"}</div>;
  }

  const { files, screen, companion, proactive, wechat } = state;
  const wechatReady = Boolean(wechat?.enabled && wechat.owner_paired);

  const toggleCompanion = (on: boolean) => {
    if (on && !companion.consented) {
      setAskConsent(true);
      return;
    }
    void change("companion", async () => ({
      companion: await api.updateCompanionConfig({ enabled: on }),
    }));
  };

  const row = (
    key: string,
    title: string,
    hint: ReactNode,
    checked: boolean | null,
    onToggle?: (on: boolean) => void,
  ) => (
    <label className={ui.row} key={key}>
      <span className={ui.rowText}>
        {title}
        <span className={ui.rowHint}>{hint}</span>
      </span>
      {checked === null ? (
        <span className={ui.tag}>一直开着</span>
      ) : (
        <input
          type="checkbox"
          role="switch"
          className={ui.switch}
          checked={checked}
          disabled={busy === key}
          aria-label={title}
          onChange={(event) => onToggle?.(event.target.checked)}
        />
      )}
    </label>
  );

  return (
    <>
      <div className={ui.list}>
        {row(
          "files",
          "读你的文件",
          "主目录里的文件都能读（只读）；资源库、隐藏文件夹、密钥和 .env 永远不读。",
          files.files_enabled,
          (on) =>
            void change("files", async () => ({
              files: await api.updateFileToolConfig({ files_enabled: on }),
            })),
        )}
        {row("search", "上网查资料", "先搜索找来源，需要时再读具体网页。", null)}
        {row(
          "screen",
          "看一眼屏幕",
          "你让她看时才截一张图；屏幕上有密码、聊天类应用时只截她能看的窗口。",
          screen.screen_enabled,
          (on) =>
            void change("screen", async () => ({
              screen: await api.updateScreenToolConfig({ screen_enabled: on }),
            })),
        )}
        {row(
          "companion",
          "陪着你看",
          "每隔几分钟看一眼你正在用的应用，多数时候安静，偶尔说一句；密码、系统设置、聊天和邮件不看。",
          companion.enabled,
          toggleCompanion,
        )}
        {row(
          "proactive",
          "主动找你",
          wechatReady
            ? "每天在随机时刻由她决定要不要给你发一条微信。"
            : "需要先在下面连上微信并绑定，她才发得出去。",
          proactive.enabled,
          (on) =>
            void change("proactive", async () => ({
              proactive: await api.updateProactiveConfig({ enabled: on }),
            })),
        )}
      </div>

      {askConsent && (
        <div className="settings-card">
          <p className="tool-hint">
            {`陪着看时，她会把你正在用的应用的一个窗口截图（不是整个屏幕），发给视觉模型（${companion.vision_model}）概括成一句话。截图不保存，概括保留 7 天。`}
          </p>
          <div className="settings-actions">
            <button type="button" className="small-button" onClick={() => setAskConsent(false)}>
              先不开
            </button>
            <button
              type="button"
              className="small-button primary"
              disabled={busy === "companion"}
              onClick={() => {
                setAskConsent(false);
                void change("companion", async () => ({
                  companion: await api.updateCompanionConfig({ consent: true, enabled: true }),
                }));
              }}
            >
              同意并打开
            </button>
          </div>
        </div>
      )}
      {(companion.enabled || screen.screen_enabled) && !companion.has_vision_key && (
        <p className="agent-warning">还没有视觉模型可用的 Key：填好上面的对话模型 Key 即可复用。</p>
      )}
      {notice && <div className="error-banner">{notice}</div>}
    </>
  );
}
