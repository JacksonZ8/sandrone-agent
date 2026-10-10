import AgentSettingsCard from "./settings/AgentSettingsCard";
import FileSearchSettingsCard from "./settings/FileSearchSettingsCard";
import LLMSettingsCard from "./settings/LLMSettingsCard";
import CompanionSettingsCard from "./settings/CompanionSettingsCard";
import ProactiveSettingsCard from "./settings/ProactiveSettingsCard";
import ScreenSettingsCard from "./settings/ScreenSettingsCard";
import TTSStatusCard from "./settings/TTSStatusCard";
import WeChatSettingsCard from "./settings/WeChatSettingsCard";

interface Props {
  onConfigSaved: () => void;
}

export default function SettingsPanel({ onConfigSaved }: Props) {
  return (
    <div className="settings-panel">
      <div className="panel-head">
        <div>
          <p>LLM 供应商、模型与 API Key 只保存在本机 SQLite 中。</p>
        </div>
      </div>

      <LLMSettingsCard onConfigSaved={onConfigSaved} />
      <WeChatSettingsCard onConfigSaved={onConfigSaved} />
      <ProactiveSettingsCard />
      <FileSearchSettingsCard />
      <CompanionSettingsCard />
      <ScreenSettingsCard />
      <AgentSettingsCard />
      <TTSStatusCard />
    </div>
  );
}
