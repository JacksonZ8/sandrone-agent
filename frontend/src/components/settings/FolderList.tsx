import { useState } from "react";
import { canPickFolder, pickFolder } from "../../app/native";

interface Props {
  folders: string[];
  onChange: (folders: string[]) => void;
  empty: string;
}

/** 文件夹清单：Mac 应用里用系统面板选，浏览器里手填路径。 */
export default function FolderList({ folders, onChange, empty }: Props) {
  const [typed, setTyped] = useState("");
  const add = (path: string) => {
    const value = path.trim();
    if (value && !folders.includes(value)) onChange([...folders, value]);
  };

  return (
    <div className="field">
      <div className="companion-apps">
        {folders.length === 0 && <span className="tool-hint">{empty}</span>}
        {folders.map((path) => (
          <button
            key={path}
            type="button"
            className="small-button"
            title="移除"
            onClick={() => onChange(folders.filter((item) => item !== path))}
          >
            {path} ×
          </button>
        ))}
      </div>
      {canPickFolder() ? (
        <div className="settings-actions">
          <button
            type="button"
            className="small-button"
            onClick={() => void pickFolder().then((path) => path && add(path))}
          >
            添加文件夹…
          </button>
        </div>
      ) : (
        <div className="companion-apps">
          <input
            value={typed}
            onChange={(event) => setTyped(event.target.value)}
            placeholder="/Volumes/外接硬盘/资料"
            aria-label="文件夹路径"
          />
          <button
            type="button"
            className="small-button"
            disabled={!typed.trim()}
            onClick={() => {
              add(typed);
              setTyped("");
            }}
          >
            添加
          </button>
        </div>
      )}
    </div>
  );
}
