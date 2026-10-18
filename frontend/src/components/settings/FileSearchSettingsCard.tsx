import { useEffect, useState } from "react";
import { api } from "../../api";
import type { SearchToolConfig } from "../../types";
import FolderList from "./FolderList";

export default function FileSearchSettingsCard() {
  const [searchTool, setSearchTool] = useState<SearchToolConfig | null>(null);
  const [extraFolders, setExtraFolders] = useState<string[]>([]);
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
        setExtraFolders(fileConfig.file_allowlist);
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
      // 读文件的开关在“她能做什么”里，这里只存额外的文件夹，不带开关以免盖掉它
      const [fileConfig, searchConfig] = await Promise.all([
        api.updateFileToolConfig({ file_allowlist: extraFolders }),
        api.updateSearchToolConfig({
          search_provider: searchProvider,
          api_key: searchKeyInput.trim() || null,
        }),
      ]);
      setExtraFolders(fileConfig.file_allowlist);
      setSearchTool(searchConfig);
      setSearchKeyInput("");
      setNotice("已保存。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="settings-card">
      <h3>文件夹与搜索</h3>
      {loading ? (
        <div className="chat-hint">正在读取文件与搜索设置…</div>
      ) : (
        <>
          <div className="field">
            <span>主目录以外还想让她读的文件夹（例如外接硬盘）</span>
          </div>
          <FolderList
            folders={extraFolders}
            onChange={setExtraFolders}
            empty="没有。主目录里的文件不用加，打开“读你的文件”就能读。"
          />
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
              {saving ? "保存中…" : "保存"}
            </button>
          </div>
        </>
      )}
      {notice && <div className="success-banner">{notice}</div>}
      <p className="tool-hint">
        聊天中可以说：读一下桌面上的 plan.md；帮我查查某个问题。搜索默认用 DuckDuckGo，不需要 Key。
      </p>
    </div>
  );
}
