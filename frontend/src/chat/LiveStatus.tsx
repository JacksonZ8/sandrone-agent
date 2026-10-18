import { useEffect, useMemo, useState } from "react";
import HerAvatar from "../character/HerAvatar";
import ui from "../ui/ui.module.css";
import { formatDuration, toolArgPreview, toolLabel } from "./format";
import styles from "./LiveStatus.module.css";
import type { LiveEvent } from "./useChatStream";

interface Props {
  events: LiveEvent[];
  startedAt: number;
  /** 已经生成出来的回复正文（流式）；有内容时代替“正在输入”的三个点 */
  partial?: string;
}

export default function LiveStatus({ events, startedAt, partial = "" }: Props) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const timer = window.setInterval(() => setElapsed(Date.now() - startedAt), 250);
    return () => window.clearInterval(timer);
  }, [startedAt]);

  const status = useMemo(() => {
    const tools = events.filter((event) => event.kind === "tool");
    const pending = tools.filter((event) => !event.done);
    const current = pending[pending.length - 1] ?? tools[tools.length - 1];
    let activity: string;
    if (current?.name) {
      const detail = toolArgPreview(current.arguments ?? {});
      activity =
        detail && detail !== "{}"
          ? `${toolLabel(current.name)} · ${detail}`
          : toolLabel(current.name);
    } else {
      const last = events[events.length - 1];
      activity = last?.kind === "thinking" ? String(last.message) : "";
    }
    return { activity, steps: tools.length, done: tools.filter((event) => event.done).length };
  }, [events]);

  return (
    <div className={styles.wrap} role="status" aria-live="polite">
      <HerAvatar className={styles.avatar} />
      <div className={styles.line}>
        <span className={styles.spinner} aria-hidden="true" />
        <span className={styles.activity}>{status.activity || "她在想…"}</span>
        {status.steps > 0 && (
          <span className={styles.count}>
            {status.done}/{status.steps}
          </span>
        )}
        <span className={styles.count}>{elapsed > 0 ? formatDuration(elapsed) : "…"}</span>
      </div>
      {partial ? (
        <div className={`${ui.card} ${styles.partial}`}>{partial}</div>
      ) : (
        <div className={`${ui.card} ${styles.typing}`} aria-hidden="true">
          <span />
          <span />
          <span />
        </div>
      )}
    </div>
  );
}
