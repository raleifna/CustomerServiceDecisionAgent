import { Activity, AlertTriangle, BarChart3, Bot, Clock, Loader, Percent, ReceiptText, ShieldAlert, UserCheck } from "lucide-react";
import { useEffect, useState } from "react";

import { yuan } from "../shop/api";
import { actionStatusLabels, decisionLabels, requestStatusLabels, returnStatusLabels, staffFetch } from "./api";

type Count<K extends string> = { [key in K]: string } & { count: number };

export type StaffStats = {
  as_of: string;
  window_days: number;
  totals: { requests: number; open: number; awaiting_human: number; decided: number; decided_by_system: number; decided_by_human: number };
  rates: { automation_rate: number | null; escalation_rate: number | null; approval_rate: number | null };
  approved_amount_total: number;
  avg_human_handling_hours: number | null;
  decisions: Count<"decision">[];
  reasons: Count<"reason">[];
  statuses: Count<"status">[];
  daily: { date: string; created: number; auto: number; human: number }[];
  returns: Count<"status">[];
  actions: Count<"status">[];
  llm: { total_calls: number; success_rate: number | null; avg_latency_ms: number | null; failure_count: number; circuit_breaker_trips: number } | null;
};

// Categorical order is fixed (validated: light surface, adjacent CVD ΔE ≥ 22).
const SERIES = [
  { key: "auto", label: "自动裁决", color: "#f2541b" },
  { key: "human", label: "人工裁决", color: "#2f6bff" },
  { key: "pending", label: "处理中", color: "#0f8b7a" },
] as const;

const pct = (value: number | null) => (value == null ? "—" : `${Math.round(value * 100)}%`);

export function DashboardPage({ token }: { token: string }) {
  const [days, setDays] = useState(14);
  const [stats, setStats] = useState<StaffStats | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setError(null);
    staffFetch<StaffStats>(token, `/stats?days=${days}`)
      .then((data) => alive && setStats(data))
      .catch((err) => alive && setError(err instanceof Error ? err.message : "加载失败"));
    return () => { alive = false; };
  }, [token, days]);

  if (error) return <div className="staff-warning"><AlertTriangle size={14} />{error}</div>;
  if (!stats) return <div className="staff-empty-state"><Loader size={18} className="spin" />加载数据…</div>;

  const kpis = [
    { label: "自动化处理率", value: pct(stats.rates.automation_rate), hint: `${stats.totals.decided_by_system} / ${stats.totals.decided} 单无需人工`, icon: Bot },
    { label: "转人工率", value: pct(stats.rates.escalation_rate), hint: `全部 ${stats.totals.requests} 单中需人工介入的比例`, icon: ShieldAlert },
    { label: "批准率", value: pct(stats.rates.approval_rate), hint: "已裁决申请中批准 / 部分批准", icon: Percent },
    { label: "人工平均处理时长", value: stats.avg_human_handling_hours == null ? "—" : `${stats.avg_human_handling_hours} 小时`, hint: "从提交到人工给出结论", icon: Clock },
    { label: "批准退款金额", value: yuan(stats.approved_amount_total), hint: "已批准申请的金额合计", icon: ReceiptText },
    { label: "未结工单", value: String(stats.totals.open), hint: `其中 ${stats.totals.awaiting_human} 单等待人工`, icon: UserCheck },
  ];

  return (
    <div className="staff-page dashboard">
      <div className="dashboard__filters">
        <span className="muted">统计口径：全部售后申请 · 截至 {stats.as_of}</span>
        <div className="segmented">
          {[7, 14, 30].map((value) => (
            <button key={value} className={days === value ? "active" : ""} onClick={() => setDays(value)}>近 {value} 天趋势</button>
          ))}
        </div>
      </div>

      <div className="kpi-grid">
        {kpis.map(({ label, value, hint, icon: Icon }) => (
          <div key={label} className="kpi-tile">
            <span className="kpi-tile__label"><Icon size={14} />{label}</span>
            <b>{value}</b>
            <small>{hint}</small>
          </div>
        ))}
      </div>

      <section className="panel">
        <h2><BarChart3 size={17} />每日新增申请与处理方式</h2>
        <DailyChart daily={stats.daily} />
      </section>

      <div className="dashboard__grid">
        <section className="panel">
          <h2>售后原因分布</h2>
          <BarList rows={stats.reasons.map((row) => ({ label: row.reason, count: row.count }))} />
        </section>
        <section className="panel">
          <h2>裁决结果</h2>
          <BarList rows={stats.decisions.map((row) => ({ label: decisionLabels[row.decision] ?? row.decision, count: row.count }))} empty="暂无已裁决的申请" />
        </section>
        <section className="panel">
          <h2>工单当前状态</h2>
          <BarList rows={stats.statuses.map((row) => ({ label: requestStatusLabels[row.status] ?? row.status, count: row.count }))} />
        </section>
        <section className="panel">
          <h2>退货与履约</h2>
          <h3 className="dashboard__sub">退货单</h3>
          <BarList rows={stats.returns.map((row) => ({ label: returnStatusLabels[row.status] ?? row.status, count: row.count }))} empty="暂无退货单" />
          <h3 className="dashboard__sub">履约任务</h3>
          <BarList rows={stats.actions.map((row) => ({ label: actionStatusLabels[row.status] ?? row.status, count: row.count }))} empty="暂无履约任务" />
        </section>
      </div>

      <section className="panel">
        <h2><Activity size={17} />大模型调用健康</h2>
        {stats.llm ? (
          <div className="kpi-grid kpi-grid--compact">
            <div className="kpi-tile"><span className="kpi-tile__label">调用次数</span><b>{stats.llm.total_calls}</b><small>本次服务启动以来</small></div>
            <div className="kpi-tile"><span className="kpi-tile__label">成功率</span><b>{pct(stats.llm.success_rate)}</b><small>失败 {stats.llm.failure_count} 次</small></div>
            <div className="kpi-tile"><span className="kpi-tile__label">平均耗时</span><b>{stats.llm.avg_latency_ms == null ? "—" : `${Math.round(stats.llm.avg_latency_ms)} ms`}</b><small>单次调用</small></div>
            <div className="kpi-tile"><span className="kpi-tile__label">熔断次数</span><b>{stats.llm.circuit_breaker_trips}</b><small>连续失败触发保护</small></div>
          </div>
        ) : <p className="muted">仅管理员（admin 角色）可查看大模型调用指标。</p>}
      </section>
    </div>
  );
}

function DailyChart({ daily }: { daily: StaffStats["daily"] }) {
  const [hover, setHover] = useState<number | null>(null);
  const rows = daily.map((row) => ({ ...row, pending: Math.max(0, row.created - row.auto - row.human) }));
  const max = Math.max(1, ...rows.map((row) => row.created));
  const ticks = niceTicks(max);
  const top = ticks[ticks.length - 1];
  const total = rows.reduce((sum, row) => sum + row.created, 0);

  return (
    <div className="daily-chart">
      <div className="chart-legend">
        {SERIES.map((series) => <span key={series.key}><i style={{ background: series.color }} />{series.label}</span>)}
        <span className="muted">合计 {total} 单</span>
      </div>
      <div className="daily-chart__plot" onMouseLeave={() => setHover(null)}>
        <div className="daily-chart__axis">
          {[...ticks].reverse().map((tick) => <span key={tick}>{tick}</span>)}
        </div>
        <div className="daily-chart__bars">
          {ticks.map((tick) => <i key={tick} className="daily-chart__grid" style={{ bottom: `calc(22px + (100% - 22px) * ${tick / top})` }} />)}
          {rows.map((row, index) => (
            <div
              key={row.date}
              className={`daily-chart__col ${hover === index ? "hover" : ""}`}
              onMouseEnter={() => setHover(index)}
              onFocus={() => setHover(index)}
              tabIndex={0}
              aria-label={`${row.date}：新增 ${row.created}，自动 ${row.auto}，人工 ${row.human}，处理中 ${row.pending}`}
            >
              <div className="daily-chart__stack" style={{ height: `${(row.created / top) * 100}%` }}>
                {SERIES.map((series) => {
                  const value = row[series.key];
                  return value > 0 ? <span key={series.key} style={{ flexGrow: value, background: series.color }} /> : null;
                })}
              </div>
              <small>{row.date.slice(5).replace("-", "/")}</small>
              {hover === index && (
                <div className={`chart-tooltip ${index > rows.length / 2 ? "chart-tooltip--left" : ""}`}>
                  <b>{row.date}</b>
                  <span>新增 <em>{row.created}</em></span>
                  {SERIES.map((series) => <span key={series.key}><i style={{ background: series.color }} />{series.label}<em>{row[series.key]}</em></span>)}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
      <details className="chart-table">
        <summary>查看数据表</summary>
        <table>
          <thead><tr><th>日期</th><th>新增</th>{SERIES.map((series) => <th key={series.key}>{series.label}</th>)}</tr></thead>
          <tbody>{rows.map((row) => <tr key={row.date}><td>{row.date}</td><td>{row.created}</td><td>{row.auto}</td><td>{row.human}</td><td>{row.pending}</td></tr>)}</tbody>
        </table>
      </details>
    </div>
  );
}

function BarList({ rows, empty = "暂无数据" }: { rows: { label: string; count: number }[]; empty?: string }) {
  if (rows.length === 0) return <p className="muted bar-list__empty">{empty}</p>;
  const max = Math.max(...rows.map((row) => row.count));
  const total = rows.reduce((sum, row) => sum + row.count, 0);
  return (
    <ul className="bar-list">
      {rows.map((row) => (
        <li key={row.label} title={`${row.label}：${row.count}（${Math.round((row.count / total) * 100)}%）`}>
          <span className="bar-list__label">{row.label}</span>
          <span className="bar-list__track"><i style={{ width: `${(row.count / max) * 100}%` }} /></span>
          <span className="bar-list__value">{row.count}<small>{Math.round((row.count / total) * 100)}%</small></span>
        </li>
      ))}
    </ul>
  );
}

function niceTicks(max: number): number[] {
  const step = max <= 5 ? 1 : max <= 10 ? 2 : max <= 25 ? 5 : Math.ceil(max / 5 / 5) * 5;
  const ticks = [];
  for (let value = 0; value <= max + step - 1; value += step) ticks.push(value);
  if (ticks[ticks.length - 1] < max) ticks.push(ticks[ticks.length - 1] + step);
  return ticks;
}
