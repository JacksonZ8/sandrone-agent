import ui from "../ui/ui.module.css";
import styles from "./ApprovalCard.module.css";
import type { PendingApproval } from "./useChatStream";

interface Props {
  approval: PendingApproval;
  busy: boolean;
  onAnswer: (approved: boolean) => void;
}

export default function ApprovalCard({ approval, busy, onAnswer }: Props) {
  return (
    <section className={`${ui.card} ${ui.lifted} ${styles.card}`} aria-label="写入确认">
      <div className={styles.head}>
        <strong>她请求一次写入操作</strong>
        <span className={ui.hint}>{approval.timeoutSeconds}s 内不处理会自动拒绝</span>
      </div>
      <dl className={styles.fields}>
        <dt>目录</dt>
        <dd>
          <code>{String(approval.arguments.cwd || "设置中的默认目录")}</code>
        </dd>
        <dt>任务</dt>
        <dd>{String(approval.arguments.prompt || "")}</dd>
      </dl>
      <div className={styles.actions}>
        <button type="button" className={ui.pill} onClick={() => onAnswer(false)} disabled={busy}>
          拒绝
        </button>
        <button
          type="button"
          className={`${ui.pill} ${ui.primary}`}
          onClick={() => onAnswer(true)}
          disabled={busy}
        >
          {busy ? "确认中…" : "允许这次写入"}
        </button>
      </div>
    </section>
  );
}
