import { useLayoutEffect, useRef, type FormEvent } from "react";
import type { ReplyLength } from "../prefs/prefs";
import { PhoneIcon, SendIcon } from "../ui/icons";
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

/** 一行高的输入条：左边回复长短，右边打电话与发送；输入多了向上长高，最多六行左右。 */
export default function Composer(props: Props) {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  // 随内容与宽度重新量高：首次布局时宽度可能还是 0，只在内容变化时量会把空输入框撑到最高
  useLayoutEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    const fit = () => {
      textarea.style.height = "auto";
      if (!textarea.value) return;
      textarea.style.height = `${Math.min(textarea.scrollHeight, MAX_HEIGHT)}px`;
    };
    fit();
    let width = textarea.clientWidth;
    const observer = new ResizeObserver(() => {
      if (textarea.clientWidth === width) return;
      width = textarea.clientWidth;
      fit();
    });
    observer.observe(textarea);
    return () => observer.disconnect();
  }, [props.value]);

  function submit(event?: FormEvent) {
    event?.preventDefault();
    if (props.sending || !props.value.trim()) return;
    props.onSend();
    textareaRef.current?.focus();
  }

  const short = props.replyLength === "short";
  return (
    <form className={styles.wrap} onSubmit={submit}>
      {props.warning && <div className={styles.warning}>{props.warning}</div>}
      <div className={`${ui.glass} ${ui.lifted} ${styles.composer}`}>
        <button
          type="button"
          className={styles.length}
          aria-label={`回复长度：${short ? "短" : "长"}，点按切换`}
          title={
            short
              ? "短回复：一到三句（点按改为可以展开说）"
              : "长回复：可以展开说（点按改为一到三句）"
          }
          onClick={() => props.onReplyLength(short ? "long" : "short")}
        >
          {short ? "短" : "长"}
        </button>
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
        <button
          type="button"
          className={styles.call}
          aria-label="语音通话"
          title={props.callAvailable ? "和她语音通话" : "语音通话需要本地识别模型与语音合成"}
          disabled={!props.callAvailable}
          onClick={props.onCall}
        >
          <PhoneIcon size={17} />
          <span className={styles.callText}>通话</span>
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
