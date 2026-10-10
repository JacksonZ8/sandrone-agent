import { useEffect, useState } from "react";
import { api } from "../api";
import type { UsageRange, UsageSummary } from "../types";
import UsageBreakdowns from "./usage/UsageBreakdowns";
import UsageErrors from "./usage/UsageErrors";
import UsageFilters from "./usage/UsageFilters";
import UsageSummaryCards from "./usage/UsageSummaryCards";

function startAt(range: UsageRange): string | undefined {
  if (range === "all") return undefined;
  const date = new Date();
  if (range === "today") date.setHours(0, 0, 0, 0);
  else date.setDate(date.getDate() - (range === "7d" ? 7 : 30));
  return date.toISOString();
}

export default function UsagePanel() {
  const [range, setRange] = useState<UsageRange>("7d");
  const [provider, setProvider] = useState("");
  const [purpose, setPurpose] = useState("");
  const [summary, setSummary] = useState<UsageSummary | null>(null);
  const [loadedKey, setLoadedKey] = useState("");
  const [refreshCount, setRefreshCount] = useState(0);
  const [error, setError] = useState("");
  const requestKey = `${range}|${provider}|${purpose}|${refreshCount}`;
  const loading = loadedKey !== requestKey;

  useEffect(() => {
    let cancelled = false;
    api
      .getUsageSummary({ startAt: startAt(range), provider, purpose })
      .then(
        (next) => {
          if (cancelled) return;
          setSummary(next);
          setError("");
        },
        (caught: unknown) => {
          if (!cancelled) setError(caught instanceof Error ? caught.message : String(caught));
        },
      )
      .finally(() => {
        if (!cancelled) setLoadedKey(requestKey);
      });
    return () => {
      cancelled = true;
    };
  }, [range, provider, purpose, requestKey]);

  return (
    <div className="usage-panel">
      <div className="panel-head">
        <div>
          <p>本地聚合调用、Token、Provider、模型、延迟和脱敏错误；不展示原始日志。</p>
        </div>
      </div>
      <UsageFilters
        range={range}
        provider={provider}
        purpose={purpose}
        providers={summary?.available_providers ?? []}
        purposes={summary?.available_purposes ?? []}
        loading={loading}
        onRangeChange={setRange}
        onProviderChange={setProvider}
        onPurposeChange={setPurpose}
        onRefresh={() => setRefreshCount((count) => count + 1)}
      />
      {error && <div className="error-banner usage-banner">加载用量失败：{error}</div>}
      {summary ? (
        <>
          <UsageSummaryCards metrics={summary.totals} />
          <UsageBreakdowns byPurpose={summary.by_purpose} byModel={summary.by_model} />
          <UsageErrors errors={summary.recent_errors} />
        </>
      ) : !error ? (
        <div className="empty-card">正在加载用量数据…</div>
      ) : null}
    </div>
  );
}
