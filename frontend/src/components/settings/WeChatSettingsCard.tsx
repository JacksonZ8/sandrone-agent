import { useEffect, useRef, useState } from "react";
import { api } from "../../api";
import type { WeChatLoginStart, WeChatPairingStart, WeChatStatus } from "../../types";

interface Props {
  onConfigSaved: () => void;
}

export default function WeChatSettingsCard({ onConfigSaved }: Props) {
  const [wechat, setWechat] = useState<WeChatStatus | null>(null);
  const [wxLogin, setWxLogin] = useState<WeChatLoginStart | null>(null);
  const [wxLoginMessage, setWxLoginMessage] = useState("");
  const [wxTokenInput, setWxTokenInput] = useState("");
  const [wxAccountInput, setWxAccountInput] = useState("");
  const [wxOwnerInput, setWxOwnerInput] = useState("");
  const [wxSaving, setWxSaving] = useState(false);
  const [pairing, setPairing] = useState<WeChatPairingStart | null>(null);
  const [loading, setLoading] = useState(true);
  const wxPollTimer = useRef<number | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const status = await api.getWeChatStatus();
        setWechat(status);
        setWxAccountInput(status.account_id);
        setWxOwnerInput(status.owner_user_id);
      } catch (error) {
        setWxLoginMessage(error instanceof Error ? error.message : String(error));
      } finally {
        setLoading(false);
      }
    })();
    return () => {
      if (wxPollTimer.current) window.clearInterval(wxPollTimer.current);
    };
  }, []);

  async function startWeChatLogin() {
    setWxLoginMessage("正在获取二维码…");
    try {
      const login = await api.startWeChatLogin();
      setWxLogin(login);
      setWxLoginMessage(login.message);
      if (!login.ok || !login.qrcode) return;

      if (wxPollTimer.current) window.clearInterval(wxPollTimer.current);
      wxPollTimer.current = window.setInterval(async () => {
        try {
          const status = await api.pollWeChatLogin(login.qrcode);
          setWxLoginMessage(status.message);
          if (status.status === "confirmed" || status.status === "expired" || status.status === "error") {
            if (wxPollTimer.current) window.clearInterval(wxPollTimer.current);
            wxPollTimer.current = null;
            if (status.status === "confirmed") {
              setWxLogin(null);
              setWechat(await api.getWeChatStatus());
              onConfigSaved();
            }
          }
        } catch {
          // 继续轮询
        }
      }, 2000);
    } catch (error) {
      setWxLoginMessage(error instanceof Error ? error.message : String(error));
    }
  }

  async function saveWeChatConfig() {
    setWxSaving(true);
    try {
      const next = await api.updateWeChatConfig({
        account_id: wxAccountInput.trim(),
        token: wxTokenInput.trim() || undefined,
        owner_user_id: wxOwnerInput.trim(),
        enabled: true,
      });
      setWechat(next);
      setWxTokenInput("");
      setWxLoginMessage("微信配置已保存，正在连接。");
      onConfigSaved();
    } catch (error) {
      setWxLoginMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setWxSaving(false);
    }
  }

  async function startPairing() {
    try {
      const result = await api.startWeChatPairing();
      setPairing(result.ok ? result : null);
      setWxLoginMessage(result.message);
    } catch (error) {
      setWxLoginMessage(error instanceof Error ? error.message : String(error));
    }
  }

  async function refreshPairing() {
    try {
      const status = await api.getWeChatStatus();
      setWechat(status);
      if (status.owner_paired) {
        setPairing(null);
        setWxLoginMessage("已绑定你的微信。");
        onConfigSaved();
      } else {
        setWxLoginMessage("还没有收到正确的配对码。");
      }
    } catch (error) {
      setWxLoginMessage(error instanceof Error ? error.message : String(error));
    }
  }

  async function logoutWeChat() {
    if (!window.confirm("断开微信连接？")) return;
    try {
      const result = await api.logoutWeChat();
      setWechat(await api.getWeChatStatus());
      setWxLoginMessage(result.message);
      onConfigSaved();
    } catch (error) {
      setWxLoginMessage(error instanceof Error ? error.message : String(error));
    }
  }

  return (
    <div className="settings-card">
      <h3>微信私聊（iLink Bot）</h3>
      {loading ? (
        <div className="chat-hint">正在读取微信设置…</div>
      ) : (
        <>
          <div className="wechat-status">
            <span className={`status ${wechat?.status === "connected" ? "status-ready" : "status-warning"}`}>
              <span className="status-dot" />
            </span>
            <div>
              <div>
                {wechat?.enabled ? (wechat.status === "connected" ? "已连接" : wechat.status) : "未连接"}
                {wechat?.owner_paired ? " · 已绑定你的微信" : ""}
              </div>
              {wechat?.account_id_masked && <p>账号：{wechat.account_id_masked}</p>}
              {wechat?.last_error && <p className="wechat-error">最近错误：{wechat.last_error}</p>}
            </div>
          </div>

          {!wechat?.enabled && (
            <div className="wechat-login">
              <button className="small-button primary" onClick={() => void startWeChatLogin()}>
                扫码登录
              </button>
              <details>
                <summary>已有凭据，手动填写</summary>
                <div className="form-grid wechat-manual">
                  <label className="field">
                    <span>account_id</span>
                    <input value={wxAccountInput} onChange={(event) => setWxAccountInput(event.target.value)} placeholder="ilink_bot_id" />
                  </label>
                  <label className="field">
                    <span>token</span>
                    <input type="password" value={wxTokenInput} onChange={(event) => setWxTokenInput(event.target.value)} placeholder="bot_token" />
                  </label>
                  <label className="field">
                    <span>你的微信 ID（可选，留空则连接后用配对码绑定）</span>
                    <input value={wxOwnerInput} onChange={(event) => setWxOwnerInput(event.target.value)} placeholder="owner_user_id" />
                  </label>
                </div>
                <button className="small-button primary" onClick={() => void saveWeChatConfig()} disabled={wxSaving}>
                  {wxSaving ? "保存中…" : "保存并连接"}
                </button>
              </details>
            </div>
          )}

          {wxLogin?.ok && wxLogin.qr_data_url && (
            <div className="wechat-qr">
              <img src={wxLogin.qr_data_url} alt="微信登录二维码" />
              <p>{wxLoginMessage}</p>
              <small>二维码链接：{wxLogin.qr_url}</small>
            </div>
          )}
          {!wxLogin && wxLoginMessage && <p className="wechat-login-message">{wxLoginMessage}</p>}

          {wechat?.enabled && !wechat.owner_paired && (
            <div className="wechat-login">
              <p className="wechat-login-message">
                尚未绑定主人：绑定前任何人的消息都不会交给桑多涅。生成配对码后，用你自己的微信把它发给 Bot。
              </p>
              {pairing ? (
                <>
                  <p className="wechat-login-message">
                    配对码 <code>{pairing.code}</code>，{Math.round(pairing.expires_in_seconds / 60)} 分钟内有效，只能使用一次。
                  </p>
                  <button className="small-button" onClick={() => void refreshPairing()}>
                    我已发送，刷新状态
                  </button>
                </>
              ) : (
                <button className="small-button primary" onClick={() => void startPairing()}>
                  生成配对码
                </button>
              )}
            </div>
          )}

          {wechat?.enabled && (
            <div className="wechat-actions">
              <button className="small-button" onClick={() => void logoutWeChat()}>
                断开微信
              </button>
              <button className="small-button" onClick={() => void startWeChatLogin()}>
                重新扫码
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
