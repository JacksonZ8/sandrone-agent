import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { postNative } from "../app/native";
import { usePrefs } from "../prefs/prefs";
import { useVoiceCall } from "../voice/useVoiceCall";
import CallView from "./CallView";
import styles from "./CallOverlay.module.css";

const UNAVAILABLE_CLOSE_MS = 4000;

/**
 * macOS 桌面通话窗口里的页面（/?overlay=call）：打开就接通，挂断后交给原生壳收起窗口、
 * 恢复桌宠与对话窗口。语音模型没准备好时说明原因，几秒后自己收起。
 */
export default function CallOverlay() {
  const { prefs } = usePrefs();
  const call = useVoiceCall({ replyLength: prefs.replyLength, onTurn: () => undefined });
  const { start, stop } = call;
  const [unavailable, setUnavailable] = useState("");
  const [ended, setEnded] = useState(false);

  useEffect(() => {
    document.documentElement.dataset.overlay = "call";
    return () => {
      delete document.documentElement.dataset.overlay;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    // 延到下一拍再拨：开发模式的 StrictMode 会先卸载一次，避免拨两通
    const timer = window.setTimeout(() => {
      api.getVoiceStatus().then(
        (status) => {
          if (cancelled) return;
          if (status.models_present) void start();
          else setUnavailable("语音模型还没准备好，暂时不能通话。");
        },
        () => {
          if (!cancelled) setUnavailable("连不上她的后端，暂时不能通话。");
        },
      );
    }, 0);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [start]);

  const hangUp = useCallback(() => {
    stop();
    // 没有原生壳（在浏览器里直接打开这个地址）：留在结束页
    if (!postNative({ type: "hangup" })) setEnded(true);
  }, [stop]);

  useEffect(() => {
    if (!unavailable) return;
    const timer = window.setTimeout(hangUp, UNAVAILABLE_CLOSE_MS);
    return () => window.clearTimeout(timer);
  }, [hangUp, unavailable]);

  if (ended) {
    return (
      <div className={styles.ended}>
        <p>通话已结束</p>
        <a href="/#/chat">回到对话</a>
      </div>
    );
  }

  return (
    <CallView
      variant="desktop"
      state={call.state}
      partial={call.partial}
      userText={call.userText}
      herText={call.herText}
      userSpeaking={call.userSpeaking}
      muted={call.muted}
      tool={call.tool}
      emotion={call.emotion}
      herLevel={call.herLevel}
      timings={call.timings}
      error={unavailable || call.error}
      onToggleMute={call.toggleMute}
      onInterrupt={call.interrupt}
      onHangUp={hangUp}
      onOpenChat={() => postNative({ type: "open-chat" })}
    />
  );
}
