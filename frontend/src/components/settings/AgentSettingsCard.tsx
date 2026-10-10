import { useEffect, useState } from "react";
import { api } from "../../api";
import type { AgentRunResponse, AgentToolConfig } from "../../types";

export default function AgentSettingsCard() {
  const [agentTool, setAgentTool] = useState<AgentToolConfig | null>(null);
  const [agentCommand, setAgentCommand] = useState("claude");
  const [agentPermission, setAgentPermission] = useState<"readonly" | "write">("readonly");
  const [agentAllowlist, setAgentAllowlist] = useState("");
  const [agentTimeout, setAgentTimeout] = useState(600);
  const [agentMaxOutput, setAgentMaxOutput] = useState(12000);
  const [agentBudget, setAgentBudget] = useState(5);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<AgentRunResponse | null>(null);
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    void (async () => {
      try {
        const config = await api.getAgentToolConfig();
        applyConfig(config);
      } catch (error) {
        setNotice(error instanceof Error ? error.message : String(error));
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  function applyConfig(config: AgentToolConfig) {
    setAgentTool(config);
    setAgentCommand(config.agent_command);
    setAgentPermission(config.agent_permission);
    setAgentAllowlist(config.agent_allowlist.join("\n"));
    setAgentTimeout(config.agent_timeout_seconds);
    setAgentMaxOutput(config.agent_max_output_chars);
    setAgentBudget(config.agent_max_budget_usd);
  }

  async function save() {
    setSaving(true);
    setNotice("");
    try {
      const directories = agentAllowlist
        .split("\n")
        .map((item) => item.trim())
        .filter(Boolean);
      if (agentTool?.agent_enabled && directories.length === 0) {
        setNotice("启用子代理前，请至少填写一个允许访问的目录。");
        return;
      }
      const config = await api.updateAgentToolConfig({
        agent_enabled: agentTool?.agent_enabled ?? false,
        agent_command: agentCommand,
        agent_permission: agentPermission,
        agent_allowlist: directories,
        agent_timeout_seconds: agentTimeout,
        agent_max_output_chars: agentMaxOutput,
        agent_max_budget_usd: agentBudget,
      });
      applyConfig(config);
      setTestResult(null);
      setNotice("子代理配置已保存。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
    } finally {
      setSaving(false);
    }
  }

  async function testAgent() {
    if (!agentTool?.agent_enabled) {
      setNotice("请先勾选启用子代理并保存配置。");
      return;
    }
    const cwd = agentTool.agent_allowlist[0] || "";
    if (!cwd) {
      setNotice("子代理还没有可用的目录。请先保存允许访问的目录。");
      return;
    }
    setTesting(true);
    setNotice("");
    setTestResult(null);
    try {
      const result = await api.runAgentTool({
        prompt:
          "你是桑多涅派来的子代理。本次测试只做只读分析，不要修改任何文件。请用不超过 150 字概括这个目录的项目类型和当前状态，并指出一个最值得注意的问题。",
        cwd,
      });
      setTestResult(result);
      setNotice(result.ok ? "子代理测试完成。" : result.message);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="settings-card">
      <h3>子代理（Agent 编排 v2）</h3>
      <p className="tool-hint">
        派 Claude Code 或 Codex 在授权目录里执行项目级任务。只读模式不写文件；写入模式允许修改授权目录内的文件，会消耗对应 CLI 的额度。
      </p>
      {loading ? (
        <div className="chat-hint">正在读取子代理设置…</div>
      ) : (
        <>
          <label className="field">
            <span className="inline-check">
              <input
                type="checkbox"
                checked={agentTool?.agent_enabled ?? false}
                onChange={(event) => setAgentTool(agentTool ? { ...agentTool, agent_enabled: event.target.checked } : null)}
              />
              允许桑多涅派子代理处理项目任务
            </span>
          </label>
          <label className="field">
            <span>子代理 CLI</span>
            <select value={agentCommand} onChange={(event) => setAgentCommand(event.target.value)}>
              <option value="claude">Claude Code</option>
              <option value="codex">OpenAI Codex CLI</option>
            </select>
            {agentTool && (
              <small>
                {agentCommand === agentTool.agent_command
                  ? agentTool.cli_available
                    ? `已检测到：${agentTool.cli_version}`
                    : `未在 PATH 中找到 ${agentCommand}，请先安装。`
                  : "切换 CLI 后请点保存，后端会重新检测。"}
              </small>
            )}
          </label>
          <label className="field">
            <span>权限模式</span>
            <select value={agentPermission} onChange={(event) => setAgentPermission(event.target.value as "readonly" | "write")}>
              <option value="readonly">只读（claude plan / codex read-only）</option>
              <option value="write">写入（claude acceptEdits / codex workspace-write）</option>
            </select>
            {agentPermission === "write" && (
              <small className="agent-warning">
                写入模式会修改授权目录内的文件。请只授权你信任的项目目录，并建议先提交或备份当前改动。
              </small>
            )}
          </label>
          <label className="field">
            <span>允许子代理访问的目录（每行一个绝对路径）</span>
            <textarea
              rows={3}
              value={agentAllowlist}
              onChange={(event) => setAgentAllowlist(event.target.value)}
              placeholder="/Users/you/projects/app&#10;/Users/you/projects/notes"
            />
          </label>
          <div className="form-grid">
            <label className="field">
              <span>超时时间（秒）</span>
              <input type="number" min={30} max={3600} step={30} value={agentTimeout} onChange={(event) => setAgentTimeout(Number(event.target.value))} />
            </label>
            <label className="field">
              <span>回传输出上限（字符）</span>
              <input type="number" min={500} max={100000} step={500} value={agentMaxOutput} onChange={(event) => setAgentMaxOutput(Number(event.target.value))} />
            </label>
            <label className="field">
              <span>单次预算上限（USD）</span>
              <input type="number" min={0.1} max={100} step={0.5} value={agentBudget} onChange={(event) => setAgentBudget(Number(event.target.value))} />
            </label>
          </div>

          <div className="settings-actions">
            <button className="small-button" onClick={() => void testAgent()} disabled={testing || saving}>
              {testing ? "子代理工作中…" : "测试子代理"}
            </button>
            <button className="small-button primary" onClick={() => void save()} disabled={saving}>
              {saving ? "保存中…" : "保存子代理配置"}
            </button>
          </div>
        </>
      )}
      {testResult && (
        <div className="agent-test-result">
          <div className="agent-test-meta">
            {testResult.ok ? "测试通过" : "测试失败"} · {testResult.command} · 只读（手动测试） · {testResult.cwd} ·{" "}
            {Math.round(testResult.duration_ms / 1000)}s
          </div>
          {testResult.output && <pre>{testResult.output}</pre>}
          {testResult.message && !testResult.ok && <p>{testResult.message}</p>}
        </div>
      )}
      {notice && <div className="success-banner">{notice}</div>}
      <p className="tool-hint">聊天中可以说：帮我整体分析这个项目。</p>
    </div>
  );
}
