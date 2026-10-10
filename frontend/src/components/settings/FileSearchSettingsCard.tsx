import { useEffect, useState } from "react";
import { api } from "../../api";
import type { FileToolConfig, SearchToolConfig } from "../../types";

export default function FileSearchSettingsCard() {
  const [fileTool, setFileTool] = useState<FileToolConfig | null>(null);
  const [searchTool, setSearchTool] = useState<SearchToolConfig | null>(null);
  const [fileAllowlist, setFileAllowlist] = useState("");
  const [searchProvider, setSearchProvider] = useState("duckduckgo");
  const [searchKeyInput, setSearchKeyInput] = useState("");
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    void (async () => {
      try {
        const [fileConfig, searchConfig] = await Promise.all([
          api.getFileToolConfig(),
          api.getSearchToolConfig(),
        ]);
        setFileTool(fileConfig);
        setFileAllowlist(fileConfig.file_allowlist.join("\n"));
        setSearchTool(searchConfig);
        setSearchProvider(searchConfig.search_provider);
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
      const allowlist = fileAllowlist
        .split("\n")
        .map((item) => item.trim())
        .filter(Boolean);
      const [fileConfig, searchConfig] = await Promise.all([
        api.updateFileToolConfig({
          files_enabled: fileTool?.files_enabled ?? false,
          file_allowlist: allowlist,
        }),
        api.updateSearchToolConfig({
          search_provider: searchProvider,
          api_key: searchKeyInput.trim() || null,
        }),
      ]);
      setFileTool(fileConfig);
      setSearchTool(searchConfig);
      setSearchKeyInput("");
      setNotice("文件与搜索配置已保存。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="settings-card">
      <h3>文件与搜索</h3>
      {loading ? (
        <div className="chat-hint">正在读取文件与搜索设置…</div>
      ) : (
        <>
          <label className="field">
            <span className="inline-check">
              <input
                type="checkbox"
                checked={fileTool?.files_enabled ?? false}
                onChange={(event) =>
                  setFileTool(
                    fileTool ? { ...fileTool, files_enabled: event.target.checked } : null,
                  )
                }
              />
              允许桑多涅只读访问文件
            </span>
          </label>
          <label className="field">
            <span>允许访问的目录（每行一个绝对路径）</span>
            <textarea
              rows={3}
              value={fileAllowlist}
              onChange={(event) => setFileAllowlist(event.target.value)}
              placeholder="/Users/you/Documents&#10;/Users/you/projects"
            />
          </label>
          <label className="field">
            <span>搜索 Provider</span>
            <select
              value={searchProvider}
              onChange={(event) => setSearchProvider(event.target.value)}
            >
              <option value="duckduckgo">DuckDuckGo（免费，无需 Key，默认）</option>
              <option value="brave">Brave Search（需要 Key）</option>
              <option value="tavily">Tavily（需要 Key）</option>
              <option value="serper">Serper（需要 Key）</option>
            </select>
          </label>
          <label className="field">
            <span>搜索 API Key（DuckDuckGo 可留空）</span>
            <input
              type="password"
              value={searchKeyInput}
              onChange={(event) => setSearchKeyInput(event.target.value)}
              placeholder={
                searchTool?.has_api_key ? `已保存 ${searchTool.api_key_masked}` : "API Key"
              }
            />
          </label>
          <div className="settings-actions">
            <button className="small-button primary" onClick={() => void save()} disabled={saving}>
              {saving ? "保存中…" : "保存文件与搜索配置"}
            </button>
          </div>
        </>
      )}
      {notice && <div className="success-banner">{notice}</div>}
      <p className="tool-hint">
        聊天中可以说：读文件 /path/to/file；搜索某个问题。她会先搜索找来源，需要时再读具体网页。
      </p>
    </div>
  );
}
