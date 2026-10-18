import { useEffect, useState } from "react";
import { api } from "../../api";
import type { ProactiveConfig, ProactiveEvent } from "../../types";

const STATUS_LABELS: Record<ProactiveEvent["status"], string> = {
  sent: "已发送",
  declined: "她选择不发",
  skipped: "未发送",
  failed: "失败",
};

const REASON_LABELS: Record<string, string> = {
  quiet_hours: "安静时段",
  daily_limit: "今天已发够",
  recently_chatted: "你们刚聊过",
  awaiting_reply: "还在等你回复上一条",
  wechat_unavailable: "微信未连接或未绑定",
};

function eventTime(iso: string): string {
  const date = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`);
  return date.toLocaleString("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function ProactiveSettingsCard() {
  const [config, setConfig] = useState<ProactiveConfig | null>(null);
  const [events, setEvents] = useState<ProactiveEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [preview, setPreview] = useState<{ send: boolean; text: string } | null>(null);

  async function refreshEvents() {
    try {
      setEvents(await api.getProactiveEvents(10));
    } catch {
      // 日志刷新失败不影响设置。
    }
  }

  useEffect(() => {
    void (async () => {
      try {
        setConfig(await api.getProactiveConfig());
        await refreshEvents();
      } catch (error) {
        setNotice(error instanceof Error ? error.message : String(error));
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  async function save() {
    if (!config) return;
    setBusy(true);
    setNotice("");
    try {
      setConfig(
        // 开关在“她能做什么”里，这里只存节奏
        await api.updateProactiveConfig({
          daily_max: config.daily_max,
          quiet_start: config.quiet_start,
          quiet_end: config.quiet_end,
          min_idle_minutes: config.min_idle_minutes,
        }),
      );
      setNotice("主动消息设置已保存。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function runPreview() {
    setBusy(true);
    setNotice("");
    setPreview(null);
    try {
      setPreview(await api.previewProactive());
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function sendNow() {
    if (!window.confirm("让她现在决定并真实发送一条微信给你？")) return;
    setBusy(true);
    setNotice("");
    try {
      const event = await api.sendProactiveNow();
      setNotice(`结果：${STATUS_LABELS[event.status]}${event.reason ? `（${event.reason}）` : ""}`);
      await refreshEvents();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="settings-card">
      <h3>主动找你的节奏</h3>
      {loading || !config ? (
        <div className="chat-hint">{notice || "正在读取主动消息设置…"}</div>
      ) : (
        <>
          <p className="chat-hint">
            在“她能做什么”里打开“主动找你”后生效：每天随机挑时间，由她自己决定说什么，或者那次干脆不说。安静时段、你们刚聊过、或上一条你还没回时不会打扰。需要后端保持运行，停机期间错过的时间不会补发。
          </p>
          <div className="form-grid">
            <label className="field">
              <span>每天最多</span>
              <select
                value={config.daily_max}
                onChange={(event) =>
                  setConfig({ ...config, daily_max: Number(event.target.value) })
                }
              >
                {[1, 2, 3, 4].map((value) => (
                  <option key={value} value={value}>
                    {value} 条
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>安静开始</span>
              <input
                type="time"
                value={config.quiet_start}
                onChange={(event) => setConfig({ ...config, quiet_start: event.target.value })}
              />
            </label>
            <label className="field">
              <span>安静结束</span>
              <input
                type="time"
                value={config.quiet_end}
                onChange={(event) => setConfig({ ...config, quiet_end: event.target.value })}
              />
            </label>
            <label className="field">
              <span>刚聊过多久内不打扰（分钟）</span>
              <input
                type="number"
                min={0}
                max={1440}
                value={config.min_idle_minutes}
                onChange={(event) =>
                  setConfig({ ...config, min_idle_minutes: Number(event.target.value) })
                }
              />
            </label>
          </div>
          <div className="settings-actions">
            <button className="small-button" onClick={() => void runPreview()} disabled={busy}>
              预览她现在会说什么
            </button>
            <button className="small-button" onClick={() => void sendNow()} disabled={busy}>
              现在发一条（真实发送）
            </button>
            <button className="small-button primary" onClick={() => void save()} disabled={busy}>
              {busy ? "处理中…" : "保存"}
            </button>
          </div>
          {preview && (
            <p className="chat-hint">
              预览（未发送）：
              {preview.send ? `「${preview.text}」` : "她这次选择不发。"}
            </p>
          )}
          {events.length > 0 && (
            <ul className="proactive-events">
              {events.map((event) => (
                <li key={event.id}>
                  <span>{eventTime(event.created_at)}</span>
                  <span>{STATUS_LABELS[event.status]}</span>
                  <span>{REASON_LABELS[event.reason] || event.reason}</span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
      {notice && !loading && <div className="success-banner">{notice}</div>}
    </div>
  );
}
