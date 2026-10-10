import { useEffect, useState } from "react";
import { api } from "../../api";
import type { ScreenToolConfig } from "../../types";

export default function ScreenSettingsCard() {
  const [screenTool, setScreenTool] = useState<ScreenToolConfig | null>(null);
  const [visionBaseUrl, setVisionBaseUrl] = useState("");
  const [visionModel, setVisionModel] = useState("");
  const [visionKeyInput, setVisionKeyInput] = useState("");
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    void (async () => {
      try {
        const config = await api.getScreenToolConfig();
        setScreenTool(config);
        setVisionBaseUrl(config.vision_base_url);
        setVisionModel(config.vision_model);
      } catch (error) {
        setNotice(error instanceof Error ? error.message : String(error));
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  async function save() {
    setSaving(true);
    setNotice("");
    try {
      const config = await api.updateScreenToolConfig({
        screen_enabled: screenTool?.screen_enabled ?? false,
        vision_base_url: visionBaseUrl.trim(),
        vision_model: visionModel.trim(),
        vision_api_key: visionKeyInput.trim() || null,
      });
      setScreenTool(config);
      setVisionKeyInput("");
      setNotice("屏幕观察配置已保存。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="settings-card">
      <h3>屏幕观察</h3>
      {loading ? (
        <div className="chat-hint">正在读取屏幕设置…</div>
      ) : (
        <>
          <label className="field">
            <span className="inline-check">
              <input
                type="checkbox"
                checked={screenTool?.screen_enabled ?? false}
                onChange={(event) => setScreenTool(screenTool ? { ...screenTool, screen_enabled: event.target.checked } : null)}
              />
              允许桑多涅观察屏幕（只读）
            </span>
          </label>
          <p className="chat-hint">
            开启后，她判断需要时会截取整个屏幕（在“陪你用电脑”里设了应用名单时，只截名单内最前面的窗口），缩放后发送给下方视觉模型的服务地址；留空时使用对话模型的地址与 Key。截图分析后即删除，但发送前请留意屏幕上的私密内容。
          </p>
          <label className="field">
            <span>视觉模型 Base URL（留空使用聊天 Base URL）</span>
            <input value={visionBaseUrl} onChange={(event) => setVisionBaseUrl(event.target.value)} placeholder="https://api.deepseek.com" />
          </label>
          <label className="field">
            <span>视觉模型名称（留空使用 deepseek-v4-flash-vision-exp）</span>
            <input value={visionModel} onChange={(event) => setVisionModel(event.target.value)} placeholder="deepseek-v4-flash-vision-exp" />
          </label>
          <label className="field">
            <span>视觉模型 API Key（留空复用聊天模型同一个 Key）</span>
            <input
              type="password"
              value={visionKeyInput}
              onChange={(event) => setVisionKeyInput(event.target.value)}
              placeholder={screenTool?.has_vision_api_key ? `已保存 ${screenTool.vision_api_key_masked}` : "留空复用 DeepSeek Key"}
            />
          </label>
          <div className="settings-actions">
            <button className="small-button primary" onClick={() => void save()} disabled={saving}>
              {saving ? "保存中…" : "保存屏幕配置"}
            </button>
          </div>
        </>
      )}
      {notice && <div className="success-banner">{notice}</div>}
      <p className="tool-hint">聊天中可以说：看看我现在的屏幕。</p>
    </div>
  );
}
