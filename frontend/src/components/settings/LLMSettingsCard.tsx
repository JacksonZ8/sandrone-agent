import { useEffect, useState } from "react";
import { api } from "../../api";
import type { LLMConfig, ProviderPreset } from "../../types";

interface Props {
  onConfigSaved: () => void;
}

export default function LLMSettingsCard({ onConfigSaved }: Props) {
  const [config, setConfig] = useState<LLMConfig | null>(null);
  const [presets, setPresets] = useState<Record<string, ProviderPreset>>({});
  const [provider, setProvider] = useState("deepseek");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [apiKeyInput, setApiKeyInput] = useState("");
  const [temperature, setTemperature] = useState(0.75);
  const [maxTokens, setMaxTokens] = useState(1024);
  const [timeout, setTimeoutSeconds] = useState(60);
  const [replyThinking, setReplyThinking] = useState<LLMConfig["reply_thinking"]>("auto");
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [feedback, setFeedback] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    void (async () => {
      try {
        const [cfg, presetData] = await Promise.all([api.getLLMConfig(), api.getProviderPresets()]);
        setConfig(cfg);
        setPresets(presetData.presets);
        setProvider(cfg.provider);
        setBaseUrl(cfg.base_url);
        setModel(cfg.model);
        setTemperature(cfg.temperature);
        setMaxTokens(cfg.max_tokens);
        setTimeoutSeconds(cfg.timeout_seconds);
        setReplyThinking(cfg.reply_thinking);
      } catch (error) {
        setFeedback({
          kind: "error",
          text: error instanceof Error ? error.message : String(error),
        });
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  function selectProvider(next: string) {
    setProvider(next);
    const preset = presets[next];
    if (preset) {
      if (preset.base_url) setBaseUrl(preset.base_url);
      if (preset.model) setModel(preset.model);
    }
    setFeedback(null);
  }

  function payload(withKey: boolean) {
    const data: Record<string, unknown> = {
      provider,
      base_url: baseUrl.trim(),
      model: model.trim(),
      temperature,
      max_tokens: maxTokens,
      timeout_seconds: timeout,
      reply_thinking: replyThinking,
    };
    if (withKey) data.api_key = apiKeyInput.trim() || null;
    return data;
  }

  async function save() {
    const preset = presets[provider];
    if (preset?.api_key_required && !config?.has_api_key && !apiKeyInput.trim()) {
      setFeedback({ kind: "error", text: "该 Provider 需要 API Key。" });
      return;
    }
    setSaving(true);
    setFeedback(null);
    try {
      const next = await api.updateLLMConfig(payload(true));
      setConfig(next);
      setApiKeyInput("");
      setFeedback({ kind: "ok", text: "配置已保存。" });
      onConfigSaved();
    } catch (error) {
      setFeedback({ kind: "error", text: error instanceof Error ? error.message : String(error) });
    } finally {
      setSaving(false);
    }
  }

  async function test() {
    setTesting(true);
    setFeedback(null);
    try {
      const result = await api.testLLMConfig(payload(true));
      if (result.ok) {
        setFeedback({
          kind: "ok",
          text: `${result.message} 模型：${result.model}，耗时 ${result.latency_ms}ms，回复预览：${result.reply_preview || "（空）"}`,
        });
      } else {
        setFeedback({ kind: "error", text: result.message });
      }
    } catch (error) {
      setFeedback({ kind: "error", text: error instanceof Error ? error.message : String(error) });
    } finally {
      setTesting(false);
    }
  }

  async function clearKey() {
    if (!window.confirm("确定清除已保存的 API Key？")) return;
    try {
      const next = await api.updateLLMConfig({ api_key: "" });
      setConfig(next);
      setApiKeyInput("");
      setFeedback({ kind: "ok", text: "API Key 已清除。" });
      onConfigSaved();
    } catch (error) {
      setFeedback({ kind: "error", text: error instanceof Error ? error.message : String(error) });
    }
  }

  return (
    <div className="settings-card">
      <h3>对话模型</h3>
      {loading ? (
        <div className="chat-hint">正在读取模型设置…</div>
      ) : (
        <>
          <div className="form-grid">
            <label className="field">
              <span>Provider</span>
              <select value={provider} onChange={(event) => selectProvider(event.target.value)}>
                {Object.entries(presets).map(([key, preset]) => (
                  <option key={key} value={key}>
                    {preset.label}
                  </option>
                ))}
              </select>
            </label>

            <label className="field">
              <span>Base URL</span>
              <input
                value={baseUrl}
                onChange={(event) => setBaseUrl(event.target.value)}
                placeholder="https://api.deepseek.com"
                disabled={provider === "mock"}
              />
            </label>

            <label className="field">
              <span>Model</span>
              <input
                value={model}
                onChange={(event) => setModel(event.target.value)}
                placeholder="deepseek-v4-flash"
                disabled={provider === "mock"}
              />
            </label>

            <label className="field">
              <span>API Key</span>
              <input
                type="password"
                value={apiKeyInput}
                onChange={(event) => setApiKeyInput(event.target.value)}
                placeholder={
                  config?.has_api_key ? `已保存 ${config.api_key_masked}，留空表示保持不变` : "sk-…"
                }
                autoComplete="off"
              />
              {config?.has_api_key && (
                <small>
                  已保存：{config.api_key_masked}
                  <button className="link-button" onClick={() => void clearKey()}>
                    清除 Key
                  </button>
                </small>
              )}
            </label>

            <label className="field">
              <span>Temperature（0–2）</span>
              <input
                type="number"
                min={0}
                max={2}
                step={0.05}
                value={temperature}
                onChange={(event) => setTemperature(Number(event.target.value))}
              />
            </label>

            <label className="field">
              <span>Max tokens</span>
              <input
                type="number"
                min={64}
                max={32768}
                step={64}
                value={maxTokens}
                onChange={(event) => setMaxTokens(Number(event.target.value))}
              />
            </label>

            <label className="field">
              <span>超时时间（秒）</span>
              <input
                type="number"
                min={5}
                max={300}
                step={5}
                value={timeout}
                onChange={(event) => setTimeoutSeconds(Number(event.target.value))}
              />
            </label>

            <label className="field">
              <span>回复时的思考（DeepSeek）</span>
              <select
                value={replyThinking}
                onChange={(event) =>
                  setReplyThinking(event.target.value as LLMConfig["reply_thinking"])
                }
              >
                <option value="auto">自动：平时关闭，要查资料/读文件/看屏幕时开低（默认）</option>
                <option value="off">关闭：最快，偶尔该查资料时不查</option>
                <option value="low">低：稍慢，总会按需查资料</option>
                <option value="high">高：最慢，回答更周全</option>
              </select>
            </label>
          </div>
          <p className="chat-hint">
            只影响对话、语音通话和看屏点评；记忆整理、战报、游戏知识等后台任务不受影响。思考开着时
            Temperature 不生效。
          </p>

          <div className="settings-actions">
            <button className="small-button" onClick={() => void test()} disabled={testing}>
              {testing ? "测试中…" : "测试连接"}
            </button>
            <button className="small-button primary" onClick={() => void save()} disabled={saving}>
              {saving ? "保存中…" : "保存配置"}
            </button>
          </div>
        </>
      )}
      {feedback && (
        <div className={feedback.kind === "ok" ? "success-banner" : "error-banner"}>
          {feedback.text}
        </div>
      )}
    </div>
  );
}
