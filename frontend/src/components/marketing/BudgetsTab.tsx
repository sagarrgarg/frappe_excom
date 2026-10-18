import { useMemo } from "react";
import { ExternalLink, Loader2, Plus } from "lucide-react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useFrappeGetCall } from "@/lib/api";
import { chartTheme } from "../../lib/chart-theme";
import { Badge, EmptyState, type Accent } from "../primitives";
import { cn } from "../ui/utils";
import { SourceBadge } from "./bits";
import { money, NA, num, ratio } from "./format";
import type { BudgetPlanRow, BudgetPlans, PlanVsActual, PlanVsActualFigures } from "./types";
import { useMarketingFilters } from "./useMarketingFilters";

const T = chartTheme();
const PLAN_ACCENT: Record<BudgetPlanRow["status"], Accent> = { Draft: "amber", Approved: "green", Cancelled: "neutral" };

function monthLabel(m: string): string {
  const [y, mo] = m.split("-").map(Number);
  return new Date(y, mo - 1, 1).toLocaleDateString("en-IN", { month: "short", year: "numeric" });
}

/** Variance vs plan: over plan is bad for spend. Null when either side is missing. */
function Variance({ r }: { r: PlanVsActualFigures }) {
  if (r.variance === null) return <span className="text-ink-3">{NA}</span>;
  const over = r.variance > 0;
  return (
    <span className={cn("tabular-nums", over ? "text-crayon-rose-text" : "text-ink-2")}>
      {over ? "+" : ""}{money(r.variance, r.currency)}{r.variance_pct !== null ? ` (${over ? "+" : ""}${r.variance_pct.toFixed(0)}%)` : ""}
    </span>
  );
}

function Pacing({ v }: { v: number | null }) {
  if (v === null) return <span className="text-ink-3">{NA}</span>;
  const pctv = v * 100;
  return <span className={cn("tabular-nums", pctv > 115 ? "text-crayon-rose-text" : pctv < 85 ? "text-crayon-amber-text" : "text-crayon-green-text")} title="Actual ÷ plan prorated to today">{pctv.toFixed(0)}%</span>;
}

const pair = (a: string, b: string) => (
  <span className="tabular-nums">{a}<span className="text-ink-3"> / {b}</span></span>
);

function PlansList({ data }: { data: BudgetPlans }) {
  if (!data.plans.length) return <p className="text-xs text-ink-3">No budget plans in this range.</p>;
  return (
    <ul className="divide-y divide-border">
      {data.plans.map((p) => (
        <li key={p.name} className="flex flex-wrap items-center gap-2 py-1.5 text-sm min-w-0">
          <a href={`/app/ads-budget-plan/${encodeURIComponent(p.name)}`} target="_blank" rel="noreferrer" className="truncate max-w-[260px] text-ink-1 hover:underline" title={p.name}>{p.plan_name}</a>
          <Badge accent={PLAN_ACCENT[p.status]}>{p.status}</Badge>
          {p.can_approve && <Badge accent="blue" title="Submit it on the Desk to approve">Needs approval</Badge>}
          <span className="text-xs text-ink-3">{p.period_from} → {p.period_to}{p.brand ? ` · ${p.brand}` : ""}</span>
          <span className="ml-auto text-xs tabular-nums text-ink-2">{money(p.total_planned, p.currency)}</span>
          {p.approved_by && <span className="text-xs text-ink-3 truncate max-w-[200px]" title={p.approval_override ? "Approved by its own maker (System Manager override)" : undefined}>approved by {p.approved_by}{p.approval_override ? " (override)" : ""}</span>}
        </li>
      ))}
    </ul>
  );
}

/** Approved budget plans against platform-reported spend, by month × network (× brand). */
export function BudgetsTab() {
  const { apiParams } = useMarketingFilters();
  const key = JSON.stringify(apiParams);
  const pva = useFrappeGetCall<{ message: PlanVsActual }>("excom.excom_ads.budgets.get_plan_vs_actual", apiParams, `ads-pva-${key}`, { refreshInterval: 10 * 60_000, keepPreviousData: true });
  const plans = useFrappeGetCall<{ message: BudgetPlans }>("excom.excom_ads.budgets.get_plans", apiParams, `ads-plans-${key}`, { keepPreviousData: true });
  const d = pva.data?.message;
  const p = plans.data?.message;

  const singleCurrency = (d?.totals.length ?? 0) <= 1;
  const chart = useMemo(() => {
    if (!d || !singleCurrency) return [];
    return d.months.map((m) => {
      const rows = d.rows.filter((r) => r.month === m);
      const planned = rows.some((r) => r.planned_spend !== null) ? rows.reduce((s, r) => s + (r.planned_spend ?? 0), 0) : null;
      const actual = rows.some((r) => r.actual_spend !== null) ? rows.reduce((s, r) => s + (r.actual_spend ?? 0), 0) : null;
      return { month: monthLabel(m), planned, actual };
    });
  }, [d, singleCurrency]);

  if (pva.isLoading && !d) return <div className="flex justify-center py-16"><Loader2 className="size-5 animate-spin text-ink-3" /></div>;
  if (pva.error) return <EmptyState title="Could not load budgets" hint={String((pva.error as { message?: string })?.message ?? "")} />;
  if (!d) return null;
  const currency = d.totals[0]?.currency ?? null;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-ink-3">
          {monthLabel(d.from_date.slice(0, 7))} → {monthLabel(d.to_date.slice(0, 7))} · whole months · only approved plans count · actuals are platform-reported, in company currency
          {d.level === "Campaign" && " · brand figures from campaign rows"}
        </p>
        <div className="flex items-center gap-3 text-xs">
          {p?.can_create && (
            <a href="/app/ads-budget-plan/new" target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 text-crayon-blue-text hover:underline"><Plus className="size-3" />New plan</a>
          )}
          <a href="/app/ads-budget-plan" target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 text-crayon-blue-text hover:underline">Plans on the Desk<ExternalLink className="size-3" /></a>
        </div>
      </div>

      {chart.length > 0 && chart.some((c) => c.planned !== null || c.actual !== null) && (
        <section className="rounded-lg border border-border p-3">
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-sm text-ink-1">Planned vs actual spend by month</h2>
            <SourceBadge source="Platform" />
          </div>
          <div className="h-[220px]">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chart} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
                <CartesianGrid stroke={T.grid} strokeDasharray="3 3" />
                <XAxis dataKey="month" tick={T.tick} />
                <YAxis tick={T.tick} width={60} tickFormatter={(v) => num(v)} />
                <Tooltip contentStyle={T.tooltip} formatter={(v, n) => [v === null || v === undefined ? NA : money(Number(v), currency), n === "planned" ? "Planned" : "Actual"]} />
                <Legend formatter={(n) => (n === "planned" ? "Planned (approved)" : "Actual (platform)")} />
                <Bar dataKey="planned" fill={T.byName.violet} />
                <Bar dataKey="actual" fill={T.byName.blue} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </section>
      )}

      {d.rows.length === 0 ? (
        <EmptyState title="No approved plans or spend in this range" hint="Create a plan on the Desk; another user with the Manage Budgets right approves it by submitting." />
      ) : (
        <section className="rounded-lg border border-border overflow-x-auto">
          <table className="w-full text-sm min-w-[980px]">
            <thead className="bg-surface-sunken">
              <tr className="text-xs text-ink-3 text-left">
                <th className="px-3 py-1.5">Month</th><th className="pr-3">Network</th>{d.by_brand && <th className="pr-3">Brand</th>}
                <th className="text-right">Planned</th><th className="text-right" title="Plan prorated to today for the month in progress">Plan to date</th><th className="text-right">Actual</th><th className="text-right">Variance</th><th className="text-right">Pacing</th>
                <th className="text-right">Leads (actual / target)</th><th className="text-right">CPL (actual / target)</th><th className="text-right">Revenue (actual / target)</th><th className="text-right px-3">ROAS (actual / target)</th>
              </tr>
            </thead>
            <tbody>
              {d.rows.map((r) => (
                <tr key={`${r.month}-${r.provider}-${r.brand}-${r.currency}`} className="border-t border-border">
                  <td className="px-3 py-1.5 whitespace-nowrap">{monthLabel(r.month)}</td>
                  <td className="text-ink-2 pr-3">{r.provider}</td>
                  {d.by_brand && <td className="truncate max-w-[160px] pr-3">{r.brand || <span className="text-ink-3" title="Spend no allocation rule matched">Unallocated</span>}</td>}
                  <td className="text-right tabular-nums">{r.planned_spend === null ? <span className="text-ink-3" title="No approved plan line">Not planned</span> : money(r.planned_spend, r.currency)}</td>
                  <td className="text-right tabular-nums text-ink-2">{money(r.planned_to_date, r.currency)}</td>
                  <td className="text-right tabular-nums">{money(r.actual_spend, r.currency)}{r.spend_unconverted ? <span className="text-xs text-ink-3" title="Spend in another currency without an exchange rate"> +{num(r.spend_unconverted)}</span> : null}</td>
                  <td className="text-right"><Variance r={r} /></td>
                  <td className="text-right"><Pacing v={r.pacing} /></td>
                  <td className="text-right">{pair(num(r.actual_leads), num(r.target_leads))}</td>
                  <td className="text-right">{pair(money(r.actual_cpl, r.currency), money(r.target_cpl, r.currency))}</td>
                  <td className="text-right">{pair(money(r.actual_revenue, r.currency), money(r.target_revenue, r.currency))}</td>
                  <td className="text-right px-3">{pair(ratio(r.actual_roas), ratio(r.target_roas))}</td>
                </tr>
              ))}
              {d.totals.map((t) => (
                <tr key={`total-${t.currency}`} className="border-t border-border-strong bg-surface-sunken">
                  <td className="px-3 py-1.5 text-ink-2" colSpan={d.by_brand ? 3 : 2}>Total{d.totals.length > 1 ? ` (${t.currency})` : ""}</td>
                  <td className="text-right tabular-nums">{money(t.planned_spend, t.currency)}</td>
                  <td className="text-right tabular-nums text-ink-2">{money(t.planned_to_date, t.currency)}</td>
                  <td className="text-right tabular-nums">{money(t.actual_spend, t.currency)}</td>
                  <td className="text-right"><Variance r={t} /></td>
                  <td className="text-right"><Pacing v={t.pacing} /></td>
                  <td className="text-right">{pair(num(t.actual_leads), num(t.target_leads))}</td>
                  <td className="text-right">{pair(money(t.actual_cpl, t.currency), money(t.target_cpl, t.currency))}</td>
                  <td className="text-right">{pair(money(t.actual_revenue, t.currency), money(t.target_revenue, t.currency))}</td>
                  <td className="text-right px-3">{pair(ratio(t.actual_roas), ratio(t.target_roas))}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      <section className="rounded-lg border border-border p-3">
        <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-0.5 mb-1">
          <h2 className="text-sm text-ink-1">Plans</h2>
          <span className="text-xs text-ink-3">Approval is maker-checker: the approver needs Manage Budgets and must not have created or last edited the plan.</span>
        </div>
        {p ? <PlansList data={p} /> : <Loader2 className="size-4 animate-spin text-ink-3" />}
      </section>
    </div>
  );
}
