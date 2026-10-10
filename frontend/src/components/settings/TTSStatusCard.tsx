import { useEffect, useState } from "react";
import { api } from "../../api";
import type { TTSStatus } from "../../types";

export default function TTSStatusCard() {
  const [tts, setTts] = useState<TTSStatus | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    void api
      .getTTSStatus()
      .then(setTts)
      .catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)));
  }, []);

  return (
    <div className="settings-card">
      <h3>语音模块</h3>
      {tts ? (
        <div className="voice-status">
          <span className={`status ${tts.ready ? "status-ready" : "status-warning"}`}>
            <span className="status-dot" />
          </span>
          <div>
            <div>
              {tts.engine} · {tts.voice_id || "未识别"}
            </div>
            <p>{tts.message}</p>
          </div>
        </div>
      ) : (
        <div className="chat-hint">{error || "正在读取语音状态…"}</div>
      )}
    </div>
  );
}
