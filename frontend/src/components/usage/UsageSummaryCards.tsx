import type { UsageMetrics } from "../../types";

const count = new Intl.NumberFormat("zh-CN");

function rate(value: number | null): string {
  return value === null ? "—" : `${(value * 100).toFixed(1)}%`;
}

export default function UsageSummaryCards({ metrics }: { metrics: UsageMetrics }) {
  return (
    <div className="usage-summary-grid">
      <article className="usage-stat-card">
        <span>调用次数</span>
        <strong>{count.format(metrics.calls)}</strong>
        <small>{metrics.failed_calls ? `${metrics.failed_calls} 次失败` : "没有失败调用"}</small>
      </article>
      <article className="usage-stat-card">
        <span>输入 Token</span>
        <strong>{count.format(metrics.prompt_tokens)}</strong>
        <small>发送给模型的上下文</small>
      </article>
      <article className="usage-stat-card">
        <span>输出 Token</span>
        <strong>{count.format(metrics.completion_tokens)}</strong>
        <small>调用成功率 {rate(metrics.success_rate)}</small>
      </article>
      <article className="usage-stat-card">
        <span>总 Token</span>
        <strong>{count.format(metrics.total_tokens)}</strong>
        <small>{count.format(metrics.success_calls)} 次成功调用</small>
      </article>
      <article className="usage-stat-card">
        <span>平均耗时</span>
        <strong>{metrics.average_latency_ms === null ? "—" : `${count.format(metrics.average_latency_ms)} ms`}</strong>
        <small>最长 {metrics.max_latency_ms === null ? "—" : `${count.format(metrics.max_latency_ms)} ms`}</small>
      </article>
    </div>
  );
}
