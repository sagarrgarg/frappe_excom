import { useMemo } from "react";
import { Link } from "react-router-dom";
import { Loader2 } from "lucide-react";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useFrappeGetCall } from "@/lib/api";
import { chartTheme } from "../../lib/chart-theme";
import { EmptyState } from "../primitives";
import { FreshnessBanner, Kpi, SourceBadge, StatusBadge } from "./bits";
import { delta, money, NA, num, pct, ratio } from "./format";
import type { Metrics, Overview } from "./types";
import { useMarketingFilters } from "./useMarketingFilters";
import { DataQualityNote, VerifiedSection } from "./VerifiedSection";

const T = chartTheme();

function Tiles({ cur, prev }: { cur: Metrics; prev: Metrics | undefined }) {
  const c = cur.currency;
  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(170px,1fr))] gap-2">
      <Kpi label="Ad spend" value={money(cur.spend, c)} delta={delta(cur.spend, prev?.spend)} goodWhenUp={false} sub={cur.rows_unconverted ? `+ ${num(cur.spend_unconverted)} in other currencies without a rate` : undefined} hint="Spend in company currency at the dated exchange rate" />
      <Kpi label="Impressions" value={num(cur.impressions)} delta={delta(cur.impressions, prev?.impressions)} />
      <Kpi label="Clicks" value={num(cur.clicks)} sub={`CTR ${pct(cur.ctr)} · CPC ${money(cur.cpc, c)}`} delta={delta(cur.clicks, prev?.clicks)} />
      <Kpi label="Leads" value={num(cur.leads)} sub={`CPL ${money(cur.cpl, c)}`} delta={delta(cur.leads, prev?.leads)} hint="Leads the platform attributes to ads, under its own attribution setting" />
      <Kpi label="Purchases" value={num(cur.purchases)} sub={`Cost / purchase ${money(cur.cost_per_purchase, c)}`} delta={delta(cur.purchases, prev?.purchases)} />
      <Kpi label="Purchase value" value={money(cur.purchase_value, c)} sub={`ROAS ${ratio(cur.roas)}`} delta={delta(cur.purchase_value, prev?.purchase_value)} hint="Platform-attributed conversion value. Verified revenue from ERPNext is shown separately." />
      <Kpi label="Chats started" value={num(cur.conversations)} delta={delta(cur.conversations, prev?.conversations)} hint="Messaging conversations started from ads (click-to-WhatsApp / Messenger)" />
    </div>
  );
}

export function OverviewTab() {
  const { apiParams, set } = useMarketingFilters();
  const { data, isLoading, error } = useFrappeGetCall<{ message: Overview }>("excom.excom_ads.dashboard.overview", apiParams, `ads-overview-${JSON.stringify(apiParams)}`, { refreshInterval: 5 * 60_000, keepPreviousData: true });
  const o = data?.message;

  const series = useMemo(() => (o?.series ?? []).map((r) => ({ date: r.date.slice(5), spend: r.spend, value: r.purchase_value, leads: r.leads })), [o]);

  if (isLoading && !o) return <div className="flex justify-center py-16"><Loader2 className="size-5 animate-spin text-ink-3" /></div>;
  if (error) return <EmptyState title="Could not load the overview" hint={String((error as { message?: string })?.message ?? "")} />;
  if (!o) return null;
  if (!o.totals.length) {
    return (
      <div className="space-y-3">
        <FreshnessBanner freshness={o.freshness} />
        <EmptyState title="No ad results in this range" hint="Pick a longer range, or connect an ad account under Admin → Meta and enable it." />
        <VerifiedSection verified={o.verified} />
      </div>
    );
  }
  const multiCurrency = o.totals.length > 1;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <FreshnessBanner freshness={o.freshness} />
        <span className="text-xs text-ink-3">
          {o.from_date} → {o.to_date} · compared with {o.previous.from_date} → {o.previous.to_date}
          {o.level === "Campaign" && " · brand figures from campaign rows"}
        </span>
      </div>

      {o.totals.map((t) => (
        <div key={t.currency ?? "none"} className="space-y-1.5">
          {multiCurrency && <h2 className="text-sm text-ink-2">{t.currency}</h2>}
          <Tiles cur={t} prev={o.previous.totals.find((p) => p.currency === t.currency)} />
        </div>
      ))}

      <DataQualityNote quality={o.data_quality} freshness={o.freshness} />
      <VerifiedSection verified={o.verified} />

      {!multiCurrency && series.length > 1 && (
        <section className="rounded-lg border border-border p-3">
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-sm text-ink-1">Spend and purchase value by day</h2>
            <SourceBadge source="Platform" />
          </div>
          <div className="h-[240px]">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={series} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
                <CartesianGrid stroke={T.grid} strokeDasharray="3 3" />
                <XAxis dataKey="date" tick={T.tick} />
                <YAxis tick={T.tick} width={60} tickFormatter={(v) => num(v)} />
                <Tooltip contentStyle={T.tooltip} formatter={(v, n) => [n === "leads" ? num(Number(v)) : money(Number(v), o.totals[0].currency), n === "spend" ? "Spend" : n === "value" ? "Purchase value" : "Leads"]} />
                <Legend formatter={(n) => (n === "spend" ? "Spend" : n === "value" ? "Purchase value" : "Leads")} />
                <Line type="monotone" dataKey="spend" stroke={T.byName.blue} strokeWidth={T.strokeWidth} dot={false} />
                <Line type="monotone" dataKey="value" stroke={T.byName.green} strokeWidth={T.strokeWidth} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </section>
      )}

      <div className="grid gap-3 laptop:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        {!multiCurrency && o.by_provider.length > 0 && (
          <section className="rounded-lg border border-border p-3 min-w-0">
            <h2 className="text-sm text-ink-1 mb-2">Spend by network</h2>
            <div className="h-[200px]">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={o.by_provider.map((p) => ({ name: p.provider, spend: p.spend }))} layout="vertical" margin={{ left: 10, right: 10 }}>
                  <CartesianGrid stroke={T.grid} strokeDasharray="3 3" />
                  <XAxis type="number" tick={T.tick} tickFormatter={(v) => num(v)} />
                  <YAxis type="category" dataKey="name" tick={T.tick} width={80} />
                  <Tooltip contentStyle={T.tooltip} formatter={(v) => [money(Number(v), o.totals[0].currency), "Spend"]} />
                  <Bar dataKey="spend" fill={T.byName.blue} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </section>
        )}
        {o.by_brand.length > 0 && (
          <section className="rounded-lg border border-border p-3 min-w-0">
            <h2 className="text-sm text-ink-1 mb-2">By brand</h2>
            <table className="w-full text-sm">
              <thead><tr className="text-xs text-ink-3 text-left"><th className="py-1">Brand</th><th className="text-right">Spend</th><th className="text-right">Leads</th><th className="text-right">ROAS</th></tr></thead>
              <tbody>
                {o.by_brand.map((b) => (
                  <tr key={`${b.brand}-${b.currency}`} className="border-t border-border">
                    <td className="py-1.5 truncate max-w-[200px]">{b.brand ? <button className="text-left hover:underline" onClick={() => set({ brand: b.brand })}>{b.brand}</button> : <span className="text-ink-3" title="Spend no allocation rule matched">Unallocated</span>}</td>
                    <td className="text-right tabular-nums">{money(b.spend, b.currency)}</td>
                    <td className="text-right tabular-nums">{num(b.leads)}</td>
                    <td className="text-right tabular-nums">{ratio(b.roas)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )}
      </div>

      {o.top_campaigns.length > 0 && (
        <section className="rounded-lg border border-border p-3">
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-sm text-ink-1">Top campaigns by spend</h2>
            <Link to={`/marketing/campaigns?${new URLSearchParams(window.location.search).toString()}`} className="text-xs text-crayon-blue-text hover:underline">All campaigns</Link>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm min-w-[640px]">
              <thead><tr className="text-xs text-ink-3 text-left"><th className="py-1">Campaign</th><th>Status</th><th>Network</th><th className="text-right">Spend</th><th className="text-right">Clicks</th><th className="text-right">Leads</th><th className="text-right">CPL</th><th className="text-right">ROAS</th></tr></thead>
              <tbody>
                {o.top_campaigns.map((c) => (
                  <tr key={`${c.campaign}-${c.currency}`} className="border-t border-border">
                    <td className="py-1.5 truncate max-w-[280px]" title={c.campaign_name ?? ""}>{c.campaign_name ?? <span className="text-ink-3">Not synced yet</span>}</td>
                    <td><StatusBadge status={c.status} /></td>
                    <td className="text-ink-2">{c.provider}</td>
                    <td className="text-right tabular-nums">{money(c.spend, c.currency)}</td>
                    <td className="text-right tabular-nums">{num(c.clicks)}</td>
                    <td className="text-right tabular-nums">{num(c.leads)}</td>
                    <td className="text-right tabular-nums">{money(c.cpl, c.currency)}</td>
                    <td className="text-right tabular-nums">{ratio(c.roas)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {o.billing && o.billing.length > 0 && (
        <section className="rounded-lg border border-border p-3">
          <div className="flex items-center justify-between mb-1">
            <h2 className="text-sm text-ink-1">Billing as reported</h2>
            <SourceBadge source="Platform" />
          </div>
          <p className="text-xs text-ink-3 mb-2">No platform reports a prepaid balance. Headroom is shown only where an account spend cap is set.</p>
          <div className="overflow-x-auto">
            <table className="w-full text-sm min-w-[600px]">
              <thead><tr className="text-xs text-ink-3 text-left"><th className="py-1">Account</th><th>Payment</th><th className="text-right">Spent (lifetime)</th><th className="text-right">Cap</th><th className="text-right">Headroom</th><th className="text-right">Amount due</th></tr></thead>
              <tbody>
                {o.billing.map((b) => (
                  <tr key={b.account} className="border-t border-border">
                    <td className="py-1.5 truncate max-w-[220px]">{b.account_name}</td>
                    <td className="text-ink-2">{b.billing_status === "Reported" ? b.payment_model : <span className="text-ink-3">{b.billing_status}</span>}</td>
                    <td className="text-right tabular-nums">{money(b.amount_spent, b.currency)}</td>
                    <td className="text-right tabular-nums">{b.billing_status !== "Reported" ? NA : b.spend_cap === null ? <span className="text-ink-3">No cap</span> : money(b.spend_cap, b.currency)}</td>
                    <td className="text-right tabular-nums">{b.headroom === null ? NA : money(b.headroom, b.currency)}</td>
                    <td className="text-right tabular-nums">{money(b.amount_due, b.currency)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}
