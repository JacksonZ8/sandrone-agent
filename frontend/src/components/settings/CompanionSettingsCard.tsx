import { useEffect, useState } from "react";
import { api } from "../../api";
import type { CompanionConfig, CompanionObservation, CompanionStatus } from "../../types";

const STATE_LABELS: Record<CompanionStatus["state"], string> = {
  off: "未开启",
  paused: "已暂停",
  not_allowed: "你现在用的应用在不看的名单里，她没有看",
  away: "你好像离开了，她没有看",
  no_window: "没找到她可以看的窗口",
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
  if (!config.enabled && !config.watch_until) return STATE_LABELS.off;
  if (config.paused_until && new Date(config.paused_until) > new Date())
    return `已暂停到 ${clock(config.paused_until)}`;
  if (!status || !status.checked_at) return "等待第一次检查…";
  const label = STATE_LABELS[status.state] ?? status.state;
  const app = status.app ? `：${status.app}` : "";
  const seen = status.seen_at ? `（${clock(status.seen_at)} 看过）` : "";
  return `${label}${app}${seen}${status.message ? ` · ${status.message}` : ""}`;
}

/** 看屏 · 陪着看：只读、低频地看窗口，多数时候安静；可以常开，也可以语音临时开一段时间。 */
export default function CompanionSettingsCard() {
  const [config, setConfig] = useState<CompanionConfig | null>(null);
  const [status, setStatus] = useState<CompanionStatus | null>(null);
  const [observations, setObservations] = useState<CompanionObservation[]>([]);
  const [running, setRunning] = useState<string[]>([]);
  const [pick, setPick] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");

  async function refresh() {
    try {
      const [nextStatus, nextObservations, fresh] = await Promise.all([
        api.getCompanionStatus(),
        api.getCompanionObservations(8),
        api.getCompanionConfig(),
      ]);
      setStatus(nextStatus);
      setObservations(nextObservations);
      // 开关在“她能做什么”里改：只同步开关与同意状态，不冲掉这里没保存的编辑
      setConfig((current) =>
        current
          ? {
              ...current,
              enabled: fresh.enabled,
              consented: fresh.consented,
              watch_until: fresh.watch_until,
              watch_apps: fresh.watch_apps,
            }
          : current,
      );
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
        <h3>陪着看</h3>
        <div className="chat-hint">{notice || "正在读取设置…"}</div>
      </div>
    );
  }

  const watching = Boolean(config.watch_until && new Date(config.watch_until) > new Date());
  const choices = running.filter((name) => !config.hidden_apps.includes(name));
  const paused = Boolean(config.paused_until && new Date(config.paused_until) > new Date());

  const save = () =>
    run(async () => {
      setConfig(
        await api.updateCompanionConfig({
          hidden_apps: config.hidden_apps,
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
      <h3>陪着看</h3>
      <p className="tool-hint">
        {`在“她能做什么”里打开“陪着你看”后：每隔几分钟，如果你正在用的应用不在下面的名单里、人在电脑前、画面有变化，她会截这个应用最前面的一个窗口（不是整个屏幕），缩放后发给视觉模型（${config.vision_model}）概括成一句话。`}
        {
          "截图只在内存里，不保存；概括保留 7 天，你们聊天时她会知道你在忙什么。多数时候她安静陪着，偶尔说一句。她不会操作键盘鼠标。"
        }
        {
          "不想常开的话，通话或聊天里说“这局陪我看着”，她会临时看你当前用的应用（默认 1 小时，到点自动停）。"
        }
      </p>
      {!config.has_vision_key && (
        <p className="agent-warning">
          还没有可用的视觉模型 Key：填好对话模型的 Key 即可复用，或在“视觉模型”里单独填写。
        </p>
      )}
      {watching && (
        <div className="settings-actions">
          <span className="tool-hint">
            正在临时陪着看 {config.watch_apps.join("、")}，到 {clock(config.watch_until)} 自动结束。
          </span>
          <button
            className="small-button"
            disabled={busy}
            onClick={() =>
              void run(async () => setConfig(await api.updateCompanionConfig({ stop_watch: true })))
            }
          >
            现在停止
          </button>
        </div>
      )}
      <div className="field">
        <span>她不看的应用</span>
        <div className="companion-apps">
          {config.always_hidden_apps.length > 0 && (
            <span className="tool-hint">永远不看：{config.always_hidden_apps.join("、")}</span>
          )}
          {config.hidden_apps.map((name) => (
            <button
              key={name}
              className="small-button"
              title="从名单移除"
              onClick={() =>
                setConfig({
                  ...config,
                  hidden_apps: config.hidden_apps.filter((item) => item !== name),
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
              setConfig({ ...config, hidden_apps: [...config.hidden_apps, pick].sort() });
              setPick("");
            }}
          >
            不让她看
          </button>
        </div>
        <span className="tool-hint">
          除了这些，你正在用的应用她都可以看。银行等其他不想让她看的应用可以加进来。
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
          disabled={busy || !(config.enabled || watching)}
        >
          现在看一眼
        </button>
        <button
          className="small-button"
          disabled={busy || !(config.enabled || watching)}
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
        屏幕录制，允许启动后端的终端。屏幕上有她不看的应用时，“看一眼”也只截她能看的窗口。
      </p>
    </div>
  );
}
