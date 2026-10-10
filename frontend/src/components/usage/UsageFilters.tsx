import type { UsageRange } from "../../types";

interface Props {
  range: UsageRange;
  provider: string;
  purpose: string;
  providers: string[];
  purposes: string[];
  loading: boolean;
  onRangeChange: (value: UsageRange) => void;
  onProviderChange: (value: string) => void;
  onPurposeChange: (value: string) => void;
  onRefresh: () => void;
}

const ranges: Array<{ value: UsageRange; label: string }> = [
  { value: "today", label: "今天" },
  { value: "7d", label: "近 7 天" },
  { value: "30d", label: "近 30 天" },
  { value: "all", label: "全部" },
];

export default function UsageFilters(props: Props) {
  return (
    <div className="usage-filters">
      <div className="usage-range" aria-label="统计时间范围">
        {ranges.map((item) => (
          <button
            key={item.value}
            className={props.range === item.value ? "filter active" : "filter"}
            onClick={() => props.onRangeChange(item.value)}
          >
            {item.label}
          </button>
        ))}
      </div>
      <label>
        <span>Provider</span>
        <select value={props.provider} onChange={(event) => props.onProviderChange(event.target.value)}>
          <option value="">全部</option>
          {props.providers.map((item) => <option key={item} value={item}>{item}</option>)}
        </select>
      </label>
      <label>
        <span>用途</span>
        <select value={props.purpose} onChange={(event) => props.onPurposeChange(event.target.value)}>
          <option value="">全部</option>
          {props.purposes.map((item) => <option key={item} value={item}>{item}</option>)}
        </select>
      </label>
      <button className="ghost-button usage-refresh" onClick={props.onRefresh} disabled={props.loading}>
        {props.loading ? "刷新中…" : "刷新"}
      </button>
    </div>
  );
}
