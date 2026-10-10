import { useEffect, useRef } from "react";
import { toolLabel } from "../chat/format";
import { DownIcon, HangUpIcon, MicIcon, MicOffIcon } from "../ui/icons";
import ui from "../ui/ui.module.css";
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
}

function formatSeconds(ms: number): string {
  return `${(ms / 1000).toFixed(1)}s`;
}

/** 实时通话：全屏角色舞台 + 双方字幕 + 静音 / 挂断。她说话时轻点舞台即可打断。 */
export default function CallView(props: Props) {
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

  const speaking = props.state === "speaking";
  const status = props.muted
    ? "已静音"
    : props.tool && props.state === "thinking"
      ? `她在${toolLabel(props.tool)}…`
      : STATE_TEXT[props.state];

  return (
    <div
      className={styles.view}
      role="dialog"
      aria-modal="true"
      aria-label="语音通话"
      data-state={props.state}
    >
      <header className={styles.header}>
        <button
          type="button"
          className={ui.iconButton}
          aria-label="挂断并回到对话"
          onClick={props.onHangUp}
        >
          <DownIcon size={20} />
        </button>
        <div className={styles.title}>
          <div className={styles.brand}>SANDRONE</div>
          <div className={ui.hint} aria-live="polite">
            {status}
          </div>
        </div>
      </header>

      <button
        type="button"
        className={styles.stage}
        onClick={speaking ? props.onInterrupt : undefined}
        aria-label={speaking ? "打断她" : "她的形象"}
        tabIndex={speaking ? 0 : -1}
      >
        <CharacterStage
          size="full"
          speaking={speaking}
          emotion={props.emotion}
          level={props.herLevel}
          label=""
        />
        {speaking && <span className={styles.tapHint}>轻点打断</span>}
      </button>

      <section className={`${ui.glass} ${ui.lifted} ${styles.panel}`}>
        <div ref={captionsRef} className={styles.captions} aria-live="polite">
          {(props.partial || props.userText) && (
            <p className={styles.user} data-live={Boolean(props.partial)}>
              {props.partial || props.userText}
            </p>
          )}
          {props.herText ? (
            <p className={styles.her}>{props.herText}</p>
          ) : (
            <p className={styles.placeholder}>
              {props.state === "listening" ? (props.userSpeaking ? "……" : "说吧，我在听。") : " "}
            </p>
          )}
        </div>
        {props.error && <div className={ui.error}>{props.error}</div>}
        <div className={styles.controls}>
          <button
            type="button"
            className={styles.round}
            aria-pressed={props.muted}
            aria-label={props.muted ? "取消静音" : "静音"}
            onClick={props.onToggleMute}
          >
            {props.muted ? <MicOffIcon /> : <MicIcon />}
          </button>
          <div
            className={styles.meter}
            data-active={props.userSpeaking && !props.muted}
            aria-hidden="true"
          >
            {Array.from({ length: 9 }, (_, index) => (
              <span key={index} style={{ animationDelay: `${-((index * 37) % 80) / 100}s` }} />
            ))}
          </div>
          <button
            type="button"
            className={styles.hangUp}
            aria-label="挂断"
            onClick={props.onHangUp}
          >
            <HangUpIcon size={22} />
          </button>
        </div>
        {/* 始终占一行，耗时出现时面板不长高，人物不跟着移动 */}
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
      </section>
    </div>
  );
}
