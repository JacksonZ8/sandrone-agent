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
      // 看屏的开关在“她能做什么”里，这里只存视觉模型
      const config = await api.updateScreenToolConfig({
        vision_base_url: visionBaseUrl.trim(),
        vision_model: visionModel.trim(),
        vision_api_key: visionKeyInput.trim() || null,
      });
      setScreenTool(config);
      setVisionKeyInput("");
      setNotice("已保存。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="settings-card">
      <h3>视觉模型（看屏用）</h3>
      {loading ? (
        <div className="chat-hint">正在读取屏幕设置…</div>
      ) : (
        <>
          <p className="chat-hint">
            三项都留空就用对话模型的地址和 Key（DeepSeek 默认是
            deepseek-v4-flash-vision-exp）。截图分析后即删除。
          </p>
          <label className="field">
            <span>视觉模型 Base URL（留空使用聊天 Base URL）</span>
            <input
              value={visionBaseUrl}
              onChange={(event) => setVisionBaseUrl(event.target.value)}
              placeholder="https://api.deepseek.com"
            />
          </label>
          <label className="field">
            <span>视觉模型名称（留空使用 deepseek-v4-flash-vision-exp）</span>
            <input
              value={visionModel}
              onChange={(event) => setVisionModel(event.target.value)}
              placeholder="deepseek-v4-flash-vision-exp"
            />
          </label>
          <label className="field">
            <span>视觉模型 API Key（留空复用聊天模型同一个 Key）</span>
            <input
              type="password"
              value={visionKeyInput}
              onChange={(event) => setVisionKeyInput(event.target.value)}
              placeholder={
                screenTool?.has_vision_api_key
                  ? `已保存 ${screenTool.vision_api_key_masked}`
                  : "留空复用 DeepSeek Key"
              }
            />
          </label>
          <div className="settings-actions">
            <button className="small-button primary" onClick={() => void save()} disabled={saving}>
              {saving ? "保存中…" : "保存"}
            </button>
          </div>
        </>
      )}
      {notice && <div className="success-banner">{notice}</div>}
      <p className="tool-hint">
        聊天或通话中可以说：看看我的屏幕 / 这张牌该不该拿 /
        讲讲屏幕上这道题。认出没建档的游戏会自动加进“游戏”。
      </p>
    </div>
  );
}
