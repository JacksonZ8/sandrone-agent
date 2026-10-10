import type { UsageModelBreakdown, UsagePurposeBreakdown } from "../../types";

const count = new Intl.NumberFormat("zh-CN");

function successRate(value: number | null): string {
  return value === null ? "—" : `${(value * 100).toFixed(1)}%`;
}

function MetricsCells({ row }: { row: UsagePurposeBreakdown | UsageModelBreakdown }) {
  return (
    <>
      <td>{count.format(row.calls)}</td>
      <td>{successRate(row.success_rate)}</td>
      <td>{count.format(row.total_tokens)}</td>
      <td>{row.average_latency_ms === null ? "—" : `${count.format(row.average_latency_ms)} ms`}</td>
    </>
  );
}

export default function UsageBreakdowns({
  byPurpose,
  byModel,
}: {
  byPurpose: UsagePurposeBreakdown[];
  byModel: UsageModelBreakdown[];
}) {
  return (
    <div className="usage-breakdown-grid">
      <section className="usage-card">
        <div className="usage-card-head"><h3>按用途</h3><span>{byPurpose.length} 项</span></div>
        <div className="usage-table-wrap">
          <table className="usage-table">
            <thead><tr><th>用途</th><th>调用</th><th>成功率</th><th>Token</th><th>平均延迟</th></tr></thead>
            <tbody>
              {byPurpose.map((row) => <tr key={row.purpose}><th>{row.purpose}</th><MetricsCells row={row} /></tr>)}
            </tbody>
          </table>
          {!byPurpose.length && <div className="usage-table-empty">当前范围没有调用记录。</div>}
        </div>
      </section>
      <section className="usage-card">
        <div className="usage-card-head"><h3>按 Provider / 模型</h3><span>仅 LLM 调用</span></div>
        <div className="usage-table-wrap">
          <table className="usage-table">
            <thead><tr><th>Provider / 模型</th><th>调用</th><th>成功率</th><th>Token</th><th>平均延迟</th></tr></thead>
            <tbody>
              {byModel.map((row) => (
                <tr key={`${row.provider}:${row.model}`}>
                  <th><span className="usage-provider">{row.provider}</span><code>{row.model}</code></th>
                  <MetricsCells row={row} />
                </tr>
              ))}
            </tbody>
          </table>
          {!byModel.length && <div className="usage-table-empty">当前范围没有 LLM Token 记录。</div>}
        </div>
      </section>
    </div>
  );
}
