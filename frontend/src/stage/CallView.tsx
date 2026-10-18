import { useEffect, useRef, useState, type PointerEvent } from "react";
import { postNative } from "../app/native";
import { toolLabel } from "../chat/format";
import { ChatIcon, DownIcon, HangUpIcon, MicIcon, MicOffIcon } from "../ui/icons";
import type { CallState, CallTimings } from "../voice/useVoiceCall";
import styles from "./CallView.module.css";
import CharacterStage from "./CharacterStage";

const STATE_TEXT: Record<CallState, string> = {
  idle: "准备中…",
  connecting: "正在接通…",
  listening: "在听你说",
  thinking: "她在想…",
  speaking: "她在说",
  ended: "通话已结束",
};

interface Props {
  /** page：网页里的沉浸式全屏；desktop：macOS 桌面上的透明通话窗口 */
  variant?: "page" | "desktop";
  state: CallState;
  partial: string;
  userText: string;
  herText: string;
  userSpeaking: boolean;
  muted: boolean;
  tool: string;
  emotion: string | null;
  herLevel: () => number;
  timings: CallTimings | null;
  error: string;
  onToggleMute: () => void;
  onInterrupt: () => void;
  onHangUp: () => void;
  /** 桌面通话窗口：打开对话窗口（通话继续） */
  onOpenChat?: () => void;
}

function formatSeconds(ms: number): string {
  return `${(ms / 1000).toFixed(1)}s`;
}

function formatClock(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  return `${String(minutes).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

/** 接通后开始计时，挂断后停在最后的时长 */
function useCallClock(state: CallState): number {
  const startedAt = useRef<number | null>(null);
  const [seconds, setSeconds] = useState(0);
  const connected = state === "listening" || state === "thinking" || state === "speaking";
  useEffect(() => {
    if (!connected) return;
    startedAt.current ??= Date.now();
    const timer = window.setInterval(() => {
      setSeconds(Math.floor((Date.now() - (startedAt.current ?? Date.now())) / 1000));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [connected]);
  return seconds;
}

const DRAG_SLOP = 4;

/**
 * 实时通话：她的形象占据画面，字幕浮在半身像下沿，底部只留静音与挂断。她说话时轻点形象即可打断。
 * 桌面通话窗口里背景透明，控制在鼠标移上来时出现，按住形象可以拖动窗口。
 */
export default function CallView({ variant = "page", ...props }: Props) {
  const desktop = variant === "desktop";
  const { onHangUp } = props;
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onHangUp();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onHangUp]);

  // 长回复逐句追加时，字幕区跟到最新一句
  const captionsRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const box = captionsRef.current;
    if (box) box.scrollTop = box.scrollHeight;
  }, [props.herText, props.partial]);

  const seconds = useCallClock(props.state);
  const speaking = props.state === "speaking";
  const status = props.muted
    ? "已静音"
    : props.tool && props.state === "thinking"
      ? `她在${toolLabel(props.tool)}…`
      : STATE_TEXT[props.state];

  // 桌面窗口：按住形象拖动窗口；没拖动就当作轻点（她说话时打断）
  const drag = useRef<{ x: number; y: number; moved: boolean } | null>(null);
  const onPointerDown = (event: PointerEvent<HTMLButtonElement>) => {
    if (!desktop || event.button !== 0) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = { x: event.screenX, y: event.screenY, moved: false };
  };
  const onPointerMove = (event: PointerEvent<HTMLButtonElement>) => {
    const current = drag.current;
    if (!current) return;
    const dx = event.screenX - current.x;
    const dy = event.screenY - current.y;
    if (!current.moved && Math.hypot(dx, dy) < DRAG_SLOP) return;
    current.moved = true;
    current.x = event.screenX;
    current.y = event.screenY;
    postNative({ type: "move", dx, dy });
  };
  const onPointerUp = () => {
    const current = drag.current;
    drag.current = null;
    if (current?.moved) postNative({ type: "move-end" });
    else if (speaking) props.onInterrupt();
  };

  const listening = props.state === "listening" && props.userSpeaking && !props.muted;
  const userLine = props.partial || props.userText;

  return (
    <div
      className={styles.view}
      role="dialog"
      aria-modal="true"
      aria-label="语音通话"
      data-variant={variant}
      data-state={props.state}
      data-muted={props.muted}
    >
      <header className={styles.top}>
        {!desktop && (
          <button
            type="button"
            className={styles.ghost}
            aria-label="挂断并回到对话"
            onClick={props.onHangUp}
          >
            <DownIcon size={20} />
          </button>
        )}
        <div className={styles.status} aria-live="polite">
          <span className={styles.dot} data-active={listening} aria-hidden="true" />
          <span>{status}</span>
          {seconds > 0 && <span className={styles.clock}>{formatClock(seconds)}</span>}
        </div>
        {!desktop && <span className={styles.spacer} />}
      </header>

      <button
        type="button"
        className={styles.stage}
        onClick={desktop ? undefined : speaking ? props.onInterrupt : undefined}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={() => (drag.current = null)}
        aria-label={speaking ? "打断她" : "她的形象"}
        tabIndex={speaking ? 0 : -1}
      >
        <span className={styles.halo} aria-hidden="true" />
        <CharacterStage
          size="full"
          speaking={speaking}
          listening={listening}
          emotion={props.emotion}
          level={props.herLevel}
          label=""
        />
        {speaking && <span className={styles.tapHint}>轻点打断</span>}
      </button>

      <div ref={captionsRef} className={styles.captions} aria-live="polite">
        {userLine && (
          <p className={styles.user} data-live={Boolean(props.partial)}>
            {userLine}
          </p>
        )}
        {props.herText ? (
          <p className={styles.her}>{props.herText}</p>
        ) : (
          props.state === "listening" &&
          !userLine && (
            <p className={styles.placeholder}>{props.userSpeaking ? "……" : "说吧，我在听。"}</p>
          )
        )}
      </div>

      {props.error && (
        <div className={styles.error} role="alert">
          {props.error}
        </div>
      )}

      <div className={styles.controls}>
        <button
          type="button"
          className={styles.round}
          aria-pressed={props.muted}
          aria-label={props.muted ? "取消静音" : "静音"}
          title={props.muted ? "取消静音" : "静音"}
          onClick={props.onToggleMute}
        >
          {props.muted ? <MicOffIcon /> : <MicIcon />}
        </button>
        <button
          type="button"
          className={styles.hangUp}
          aria-label="挂断"
          title="挂断（Esc）"
          onClick={props.onHangUp}
        >
          <HangUpIcon size={desktop ? 20 : 24} />
        </button>
        {desktop && props.onOpenChat ? (
          <button
            type="button"
            className={styles.round}
            aria-label="打开对话窗口"
            title="打开对话窗口"
            onClick={props.onOpenChat}
          >
            <ChatIcon />
          </button>
        ) : (
          <div
            className={styles.meter}
            data-active={props.userSpeaking && !props.muted}
            aria-hidden="true"
          >
            {Array.from({ length: 5 }, (_, index) => (
              <span key={index} style={{ animationDelay: `${-((index * 37) % 80) / 100}s` }} />
            ))}
          </div>
        )}
      </div>

      {!desktop && (
        // 始终占一行，耗时出现时不把人物往上推
        <div className={styles.timings} aria-hidden={!props.timings}>
          {props.timings ? (
            <>
              上一句：说完后 {formatSeconds(props.timings.end_silence_ms)} 判定结束 · 她{" "}
              {formatSeconds(props.timings.first_audio_ms)} 后开口
            </>
          ) : (
            " "
          )}
        </div>
      )}
    </div>
  );
}
