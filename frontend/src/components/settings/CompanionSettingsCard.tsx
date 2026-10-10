import { useEffect, useState } from "react";
import { api } from "../../api";
import type { CompanionConfig, CompanionObservation, CompanionStatus } from "../../types";

const STATE_LABELS: Record<CompanionStatus["state"], string> = {
  off: "未开启",
  paused: "已暂停",
  not_allowed: "你现在用的应用不在名单里，她没有看",
  away: "你好像离开了，她没有看",
  no_window: "没找到名单内应用的窗口",
  watching: "正在陪你",
  playing: "正在陪你玩",
  error: "出错了",
};

const INTERVALS = [2, 3, 5, 10, 15, 30];
const REMARK_GAPS: [number, string][] = [
  [0, "只看不说"],
  [20, "最多 20 分钟一句"],
  [45, "最多 45 分钟一句"],
  [90, "最多 90 分钟一句"],
  [180, "最多 3 小时一句"],
];

function clock(iso: string | null): string {
  if (!iso) return "";
  const date = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`);
  return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

function statusText(status: CompanionStatus | null, config: CompanionConfig): string {
  if (!config.enabled) return STATE_LABELS.off;
  if (config.paused_until && new Date(config.paused_until) > new Date())
    return `已暂停到 ${clock(config.paused_until)}`;
  if (!status || !status.checked_at) return "等待第一次检查…";
  const label = STATE_LABELS[status.state] ?? status.state;
  const app = status.app ? `：${status.app}` : "";
  const seen = status.seen_at ? `（${clock(status.seen_at)} 看过）` : "";
  return `${label}${app}${seen}${status.message ? ` · ${status.message}` : ""}`;
}

/** 陪你用电脑（M14）：只读、低频地看名单内应用的窗口，多数时候安静。 */
export default function CompanionSettingsCard() {
  const [config, setConfig] = useState<CompanionConfig | null>(null);
  const [status, setStatus] = useState<CompanionStatus | null>(null);
  const [observations, setObservations] = useState<CompanionObservation[]>([]);
  const [running, setRunning] = useState<string[]>([]);
  const [pick, setPick] = useState("");
  const [agree, setAgree] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");

  async function refresh() {
    try {
      const [nextStatus, nextObservations] = await Promise.all([
        api.getCompanionStatus(),
        api.getCompanionObservations(8),
      ]);
      setStatus(nextStatus);
      setObservations(nextObservations);
    } catch {
      // 状态刷新失败不影响设置。
    }
  }

  useEffect(() => {
    void (async () => {
      try {
        setConfig(await api.getCompanionConfig());
        await refresh();
        setRunning(await api.getRunningApps());
      } catch (error) {
        setNotice(error instanceof Error ? error.message : String(error));
      }
    })();
    const timer = window.setInterval(() => void refresh(), 15000);
    return () => window.clearInterval(timer);
  }, []);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setNotice("");
    try {
      await action();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  if (!config) {
    return (
      <div className="settings-card">
        <h3>陪你用电脑</h3>
        <div className="chat-hint">{notice || "正在读取设置…"}</div>
      </div>
    );
  }

  const needsConsent = config.enabled && !config.consented;
  const choices = running.filter((name) => !config.allowed_apps.includes(name));
  const paused = Boolean(config.paused_until && new Date(config.paused_until) > new Date());

  const save = () =>
    run(async () => {
      if (needsConsent && !agree) throw new Error("开启前请先勾选同意。");
      setConfig(
        await api.updateCompanionConfig({
          enabled: config.enabled,
          consent: needsConsent ? true : undefined,
          allowed_apps: config.allowed_apps,
          interval_minutes: config.interval_minutes,
          remark_gap_minutes: config.remark_gap_minutes,
          notify: config.notify,
        }),
      );
      setNotice("已保存。");
      await refresh();
    });

  const checkNow = () =>
    run(async () => {
      const result = await api.checkCompanionNow();
      setStatus(result.status);
      await refresh();
      setNotice(
        result.result === "spoke"
          ? "她看了一眼，还说了句话（在对话里）。"
          : result.result === "observed"
            ? "她看了一眼。"
            : statusText(result.status, config),
      );
    });

  return (
    <div className="settings-card">
      <h3>陪你用电脑</h3>
      <label className="field">
        <span className="inline-check">
          <input
            type="checkbox"
            checked={config.enabled}
            onChange={(event) => setConfig({ ...config, enabled: event.target.checked })}
          />
          让她陪你用电脑（只读）
        </span>
      </label>
      <p className="tool-hint">
        {`每隔几分钟，如果你正在用名单里的应用、人在电脑前、画面有变化，她会截这个应用最前面的一个窗口（不是整个屏幕），缩放后发给视觉模型（${config.vision_model} · ${config.vision_base_url}）概括成一句话。`}
        {
          "截图只在内存里，不保存；概括保留 7 天，你们聊天时她会知道你在忙什么。多数时候她安静陪着，偶尔说一句。她不会操作键盘鼠标。"
        }
      </p>
      {!config.has_vision_key && (
        <p className="agent-warning">
          还没有可用的视觉模型 Key，请先在“屏幕观察”或模型设置里填写。
        </p>
      )}
      {needsConsent && (
        <label className="field">
          <span className="inline-check">
            <input
              type="checkbox"
              checked={agree}
              onChange={(event) => setAgree(event.target.checked)}
            />
            我同意把名单内应用的窗口截图发给上面的视觉模型服务
          </span>
        </label>
      )}

      <div className="field">
        <span>她可以看的应用</span>
        <div className="companion-apps">
          {config.allowed_apps.length === 0 && (
            <span className="tool-hint">还没有。名单为空时她什么都不看。</span>
          )}
          {config.allowed_apps.map((name) => (
            <button
              key={name}
              className="small-button"
              title="从名单移除"
              onClick={() =>
                setConfig({
                  ...config,
                  allowed_apps: config.allowed_apps.filter((item) => item !== name),
                })
              }
            >
              {name} ×
            </button>
          ))}
        </div>
        <div className="companion-apps">
          <select value={pick} onChange={(event) => setPick(event.target.value)}>
            <option value="">从正在运行的应用中选择…</option>
            {choices.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
          <button
            className="small-button"
            disabled={!pick}
            onClick={() => {
              setConfig({ ...config, allowed_apps: [...config.allowed_apps, pick].sort() });
              setPick("");
            }}
          >
            加入名单
          </button>
        </div>
        <span className="tool-hint">
          建议只放写代码、写文档、看资料这类应用；微信、邮箱、银行、密码管理器不要加。
        </span>
      </div>

      <div className="form-grid">
        <label className="field">
          <span>多久看一次</span>
          <select
            value={config.interval_minutes}
            onChange={(event) =>
              setConfig({ ...config, interval_minutes: Number(event.target.value) })
            }
          >
            {INTERVALS.map((value) => (
              <option key={value} value={value}>
                每 {value} 分钟
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>她开口的频率</span>
          <select
            value={config.remark_gap_minutes}
            onChange={(event) =>
              setConfig({ ...config, remark_gap_minutes: Number(event.target.value) })
            }
          >
            {REMARK_GAPS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <label className="field">
        <span className="inline-check">
          <input
            type="checkbox"
            checked={config.notify}
            onChange={(event) => setConfig({ ...config, notify: event.target.checked })}
          />
          她开口时弹系统通知
        </span>
      </label>

      <p className="tool-hint">状态：{statusText(status, config)}</p>
      <div className="settings-actions">
        <button
          className="small-button"
          onClick={() => void checkNow()}
          disabled={busy || !config.enabled}
        >
          现在看一眼
        </button>
        <button
          className="small-button"
          disabled={busy || !config.enabled}
          onClick={() => void run(async () => setConfig(await api.pauseCompanion(paused ? 0 : 60)))}
        >
          {paused ? "恢复" : "暂停 1 小时"}
        </button>
        <button className="small-button primary" onClick={() => void save()} disabled={busy}>
          {busy ? "处理中…" : "保存"}
        </button>
      </div>

      {observations.length > 0 && (
        <>
          <ul className="proactive-events companion-observations">
            {observations.map((row) => (
              <li key={row.id}>
                <span>{clock(row.created_at)}</span>
                <span>{row.app}</span>
                <span>
                  {row.summary}
                  {row.detail ? `；${row.detail}` : ""}
                  {row.message_id ? " · 她说了话" : ""}
                </span>
              </li>
            ))}
          </ul>
          <div className="settings-actions">
            <button
              className="small-button"
              disabled={busy}
              onClick={() => {
                if (!window.confirm("清除她看到的全部记录？")) return;
                void run(async () => {
                  await api.clearCompanionObservations();
                  setObservations([]);
                });
              }}
            >
              清除观察记录
            </button>
          </div>
        </>
      )}
      {notice && <div className="success-banner">{notice}</div>}
      <p className="tool-hint">
        需要后端有「屏幕录制」权限：系统设置 → 隐私与安全性 →
        屏幕录制，允许启动后端的终端。聊天里说“帮我看看”时，她也只看名单内的窗口。
      </p>
    </div>
  );
}
