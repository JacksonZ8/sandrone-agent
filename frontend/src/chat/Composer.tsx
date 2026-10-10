import { useLayoutEffect, useRef, type FormEvent } from "react";
import type { ReplyLength } from "../prefs/prefs";
import { MicIcon, SendIcon } from "../ui/icons";
import ui from "../ui/ui.module.css";
import styles from "./Composer.module.css";

interface Props {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  sending: boolean;
  replyLength: ReplyLength;
  onReplyLength: (value: ReplyLength) => void;
  /** 本地识别模型与语音合成都就绪时才能通话 */
  callAvailable: boolean;
  onCall: () => void;
  warning?: string;
}

const MAX_HEIGHT = 168;

export default function Composer(props: Props) {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  // 输入框随内容长高，最多六行左右。
  useLayoutEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, MAX_HEIGHT)}px`;
  }, [props.value]);

  function submit(event?: FormEvent) {
    event?.preventDefault();
    if (props.sending || !props.value.trim()) return;
    props.onSend();
    textareaRef.current?.focus();
  }

  return (
    <form className={`${ui.glass} ${ui.lifted} ${styles.composer}`} onSubmit={submit}>
      {props.warning && <div className={styles.warning}>{props.warning}</div>}
      <label htmlFor="composer-input" className="visually-hidden">
        和她说话
      </label>
      <textarea
        id="composer-input"
        ref={textareaRef}
        rows={1}
        value={props.value}
        placeholder="和她说点什么…"
        onChange={(event) => props.onChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing)
            submit(event);
        }}
      />
      <div className={styles.toolbar}>
        <div role="group" aria-label="回复长度" className={ui.segmented}>
          {(["long", "short"] as const).map((length) => (
            <button
              key={length}
              type="button"
              className={ui.segment}
              aria-pressed={props.replyLength === length}
              title={length === "short" ? "一到三句" : "可以展开说"}
              onClick={() => props.onReplyLength(length)}
            >
              {length === "short" ? "短" : "长"}
            </button>
          ))}
        </div>
        <span className={styles.spacer} />
        <button
          type="button"
          className={`${ui.iconButton} ${styles.mic}`}
          aria-label="语音通话"
          title={props.callAvailable ? "和她语音通话" : "语音通话需要本地识别模型与语音合成"}
          disabled={!props.callAvailable}
          onClick={props.onCall}
        >
          <MicIcon />
        </button>
        <button
          type="submit"
          className={styles.send}
          aria-label="发送"
          disabled={props.sending || !props.value.trim()}
        >
          <SendIcon />
        </button>
      </div>
    </form>
  );
}
