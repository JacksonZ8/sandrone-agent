import type { UsageError } from "../../types";

export default function UsageErrors({ errors }: { errors: UsageError[] }) {
  return (
    <section className="usage-card usage-errors-card">
      <div className="usage-card-head"><h3>最近错误</h3><span>最多 20 条 · 已脱敏</span></div>
      {!errors.length ? (
        <div className="usage-table-empty">当前筛选范围没有失败调用。</div>
      ) : (
        <div className="usage-error-list">
          {errors.map((item, index) => (
            <article className="usage-error-row" key={`${item.created_at}:${index}`}>
              <div className="usage-error-meta">
                <time>{new Date(item.created_at).toLocaleString("zh-CN")}</time>
                <span className={`usage-status ${item.status}`}>{item.status}</span>
                <span>{item.purpose}</span>
                <span>{item.provider} / {item.model}</span>
                {item.latency_ms !== null && <span>{item.latency_ms} ms</span>}
              </div>
              <p>{item.error || "未提供错误详情。"}</p>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
