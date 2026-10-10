import { memo, useState, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";
import type { Message } from "../types";
import { CheckIcon, CopyIcon, PauseIcon, PlayIcon, RegenerateIcon, TrashIcon } from "../ui/icons";
import ui from "../ui/ui.module.css";
import {
  formatDuration,
  isActionLine,
  memoryLayerLabel,
  memoryTypeLabel,
  timeLabel,
  toolLabel,
  toolTraceArg,
} from "./format";
import styles from "./MessageItem.module.css";

function plainText(children: ReactNode): string {
  if (typeof children === "string" || typeof children === "number") return String(children);
  if (Array.isArray(children)) return children.map(plainText).join("");
  return "";
}

const markdownComponents: Components = {
  a: (props) => <a {...props} target="_blank" rel="noreferrer" />,
  p: ({ children }) => (
    <p className={isActionLine(plainText(children)) ? styles.action : undefined}>{children}</p>
  ),
};

/** 关闭动作描写时，隐藏整段只有动作的段落（全部是动作时保留原文）。 */
function visibleContent(content: string, showActions: boolean): string {
  if (showActions) return content;
  const kept = content
    .split(/\n+/)
    .filter((line) => !isActionLine(line))
    .join("\n\n")
    .trim();
  return kept || content;
}

interface Props {
  message: Message;
  /** 她主动发来的消息（没有对应的用户发言） */
  initiated: boolean;
  showActions: boolean;
  voiceReady: boolean;
  synthesizing: boolean;
  playing: boolean;
  onListen: (message: Message) => void;
  /** 只有时间线上最新、且回应了你的那条回复可以重新生成 */
  canRegenerate: boolean;
  regenerating: boolean;
  onRegenerate: (message: Message) => void;
  onDelete: (message: Message) => void;
}

function MessageItem({
  message,
  initiated,
  showActions,
  voiceReady,
  synthesizing,
  playing,
  onListen,
  canRegenerate,
  regenerating,
  onRegenerate,
  onDelete,
}: Props) {
  const [openMemory, setOpenMemory] = useState(false);
  const [openTools, setOpenTools] = useState(false);
  const [copied, setCopied] = useState(false);
  const time = timeLabel(message.created_at);
  const fromWeChat = message.channel === "wechat_dm";
  const fromCall = message.channel === "voice";

  if (message.role === "user") {
    return (
      <div className={styles.userRow}>
        <div className={styles.mine}>{message.content}</div>
        <div className={styles.userMeta}>
          {fromWeChat && <span className={styles.badge}>微信</span>}
          {fromCall && <span className={styles.callBadge}>通话</span>}
          <span>{time}</span>
          <button
            type="button"
            className={styles.metaButton}
            aria-label="删除这条消息"
            title="删除"
            onClick={() => onDelete(message)}
          >
            <TrashIcon size={14} />
          </button>
        </div>
      </div>
    );
  }

  const memories = message.memory_traces ?? [];
  const tools = message.tool_calls ?? [];

  async function copy() {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      // 剪贴板不可用时不提示
    }
  }

  return (
    <div className={styles.assistantRow}>
      {initiated && (
        <div className={`${ui.glass} ${styles.chip}`}>
          {time} · 她主动{fromWeChat ? "发到微信" : "发来"}
        </div>
      )}
      <article
        className={`${ui.card} ${ui.lifted} ${styles.reply}`}
        data-regenerating={regenerating}
        aria-busy={regenerating}
      >
        <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]} components={markdownComponents}>
          {visibleContent(message.content, showActions)}
        </ReactMarkdown>
      </article>

      <div className={styles.actions}>
        <button
          type="button"
          className={`${ui.iconButton} ${ui.small}`}
          aria-label={copied ? "已复制" : "复制"}
          title={copied ? "已复制" : "复制"}
          onClick={() => void copy()}
        >
          {copied ? <CheckIcon /> : <CopyIcon />}
        </button>
        {canRegenerate && (
          <button
            type="button"
            className={`${ui.iconButton} ${ui.small}`}
            aria-label="重新回答"
            title="重新回答"
            onClick={() => onRegenerate(message)}
          >
            <RegenerateIcon />
          </button>
        )}
        <button
          type="button"
          className={`${ui.iconButton} ${ui.small}`}
          aria-label="删除这条消息"
          title="删除"
          disabled={regenerating}
          onClick={() => onDelete(message)}
        >
          <TrashIcon />
        </button>
        {(voiceReady || message.audio_path) && (
          <button
            type="button"
            className={`${ui.glass} ${styles.listen}`}
            aria-label={playing ? "暂停" : "听她说"}
            disabled={synthesizing}
            onClick={() => onListen(message)}
          >
            {playing ? <PauseIcon size={12} /> : <PlayIcon size={12} />}
            {synthesizing ? "合成中…" : playing ? "暂停" : "听她说"}
          </button>
        )}
        {memories.length > 0 && (
          <button
            type="button"
            className={styles.toggle}
            aria-expanded={openMemory}
            onClick={() => setOpenMemory((value) => !value)}
          >
            回忆 · {memories.length}
          </button>
        )}
        {tools.length > 0 && (
          <button
            type="button"
            className={styles.toggle}
            aria-expanded={openTools}
            onClick={() => setOpenTools((value) => !value)}
          >
            过程 · {tools.length}
          </button>
        )}
        <span className={styles.meta}>
          {fromWeChat && !initiated && <span className={styles.badge}>微信</span>}
          {fromCall && <span className={styles.callBadge}>通话</span>}
          {message.latency_ms > 0 && <span>用时 {formatDuration(message.latency_ms)}</span>}
          <span>{time}</span>
        </span>
      </div>

      {openMemory && (
        <ul className={`${ui.glass} ${styles.traces}`} aria-label="这次想起的事">
          {memories.map((trace, index) => {
            const type = memoryTypeLabel(trace);
            return (
              <li key={index}>
                <span className={ui.tagAccent}>
                  {memoryLayerLabel(trace.layer)}
                  {type ? ` · ${type}` : ""}
                </span>
                <span className={styles.traceText}>{trace.content}</span>
              </li>
            );
          })}
        </ul>
      )}

      {openTools && (
        <div className={`${ui.glass} ${styles.traces}`}>
          {tools.map((trace, index) => (
            <details key={index} className={styles.step}>
              <summary>
                <span className={ui.tag}>{toolLabel(trace.name)}</span>
                <span className={styles.traceText}>{toolTraceArg(trace)}</span>
              </summary>
              <div className={styles.stepBody}>
                <div>参数</div>
                <pre>{JSON.stringify(trace.arguments, null, 2)}</pre>
                <div>结果</div>
                <pre>{trace.result_preview || "（空）"}</pre>
              </div>
            </details>
          ))}
        </div>
      )}
    </div>
  );
}

export default memo(MessageItem);
