import { useMemo, useState } from "react";
import { Loader2 } from "lucide-react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useFrappeGetCall } from "@/lib/api";
import { chartTheme } from "../../lib/chart-theme";
import { Button, EmptyState, SegmentedControl, Select } from "../primitives";
import { Kpi, SourceBadge } from "./bits";
import { ago, money, num, pct } from "./format";
import type { WebGroup, WebMetrics, WebReport, WebRow } from "./types";
import { useMarketingFilters } from "./useMarketingFilters";

const T = chartTheme();
const PAGE = 50;
const GROUPS: { value: WebGroup; label: string }[] = [
  { value: "source_medium", label: "Source / medium" },
  { value: "campaign", label: "Campaign" },
  { value: "landing_page", label: "Landing page" },
  { value: "date", label: "Day" },
];

function label(r: WebRow, g: WebGroup): string {
  if (g === "source_medium") return `${r.session_source ?? "(not set)"} / ${r.session_medium ?? "(not set)"}`;
  if (g === "campaign") return r.campaign_title ?? r.session_campaign_name ?? "(not set)";
  if (g === "landing_page") return r.landing_page || "(not set)";
  return r.date ?? "";
}

function Tiles({ t, note }: { t: WebMetrics; note?: string }) {
  const c = t.currency ?? undefined;
  return (
    <div className="grid grid-cols-[repeat(auto-fit,minmax(170px,1fr))] gap-2">
      <Kpi source="Analytics" label="Sessions" value={num(t.sessions)} sub={`New users ${num(t.new_users)}`} />
      <Kpi source="Analytics" label="Engaged sessions" value={num(t.engaged_sessions)} sub={`Engagement ${pct(t.engagement_rate)}`} hint="Sessions over 10 s, with a key event, or with 2+ page views" />
      <Kpi source="Analytics" label="Key events" value={num(t.key_events)} sub={t.key_events_per_session !== null ? `${t.key_events_per_session.toFixed(2)} per session` : undefined} hint="Events marked as key events in GA4 (formerly 'conversions')" />
      <Kpi source="Analytics" label="Transactions" value={num(t.transactions)} sub={`Per session ${pct(t.transactions_per_session)}`} />
      <Kpi source="Analytics" label="Purchase revenue" value={money(t.purchase_revenue, c)} sub={t.revenue_per_session !== null ? `${money(t.revenue_per_session, c)} per session` : undefined} hint="As measured on the website by GA4 (minus refunds it was told about). Verified revenue from ERPNext is on the Overview." />
      <Kpi source="Analytics" label="Users (per day × source)" value={num(t.total_users)} hint={note} />
    </div>
  );
}

/** GA4: what the website measured. Its own source ("Analytics"): never Verified, never platform ad figures. */
export function WebsiteTab() {
  const { apiParams } = useMarketingFilters();
  const [property, setProperty] = useState<string>("");
  const [group, setGroup] = useState<WebGroup>("source_medium");
  const [start, setStart] = useState(0);
  const params = { ...apiParams, property: property || undefined, group_by: group, start, page_length: PAGE };
  const { data, isLoading, error } = useFrappeGetCall<{ message: WebReport }>("excom.excom_ads.analytics.web_report", params, `ads-web-${JSON.stringify(params)}`, { keepPreviousData: true, refreshInterval: 10 * 60_000 });
  const r = data?.message;
  const props = r?.properties ?? [];
  const current = props.find((p) => p.name === property) ?? (props.length === 1 ? props[0] : undefined);
  const series = useMemo(() => (r?.series ?? []).map((s) => ({ date: s.date.slice(5), sessions: s.sessions, key: s.key_events })), [r]);

  if (isLoading && !r) return <div className="flex justify-center py-16"><Loader2 className="size-5 animate-spin text-ink-3" /></div>;
  if (error) return <EmptyState title="Could not load website analytics" hint={String((error as { message?: string })?.message ?? "")} />;
  if (!r) return null;
  if (!props.length) return <EmptyState title="No GA4 property connected" hint="An admin enables a Google Analytics 4 property on a Google connection whose scopes include analytics.readonly." />;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        {props.length > 1 && (
          <Select className="h-7 text-xs w-[220px]" value={property} onChange={(e) => { setProperty(e.target.value); setStart(0); }} aria-label="GA4 property">
            <option value="">All properties</option>
            {props.map((p) => <option key={p.name} value={p.name}>{p.property_name}</option>)}
          </Select>
        )}
        <SegmentedControl<WebGroup> variant="segmented" value={group} onChange={(g) => { setGroup(g); setStart(0); }} segments={GROUPS} ariaLabel="Group by" />
        <span className="ml-auto"><SourceBadge source="Analytics" /></span>
      </div>

      {current && (
        <p className="text-xs text-ink-3">
          {current.property_name}{current.website ? ` · ${current.website}` : ""} · days in {current.time_zone ?? "the property's time zone"} · synced {ago(current.last_success_at)}
          {current.error_class && <span className="text-crayon-rose-text"> · last sync: {current.error_class}{current.last_error ? ` (${current.last_error.slice(0, 160)})` : ""}</span>}
          {current.data_loss_other_row ? " · GA4 grouped some low-volume rows into “(other)”." : ""}
          {current.subject_to_thresholding ? " · Some rows are withheld by GA4 thresholding." : ""}
        </p>
      )}

      {!r.totals.length ? (
        <EmptyState compact title="No website sessions in this range" hint="GA4 needs about 12 hours to process a day. Pick a longer range." />
      ) : (
        r.totals.map((t) => (
          <div key={t.currency ?? "none"} className="space-y-1.5">
            {r.totals.length > 1 && <h2 className="text-sm text-ink-2">{t.currency}</h2>}
            <Tiles t={t} note={r.users_note} />
          </div>
        ))
      )}

      {r.totals.length === 1 && series.length > 1 && (
        <section className="rounded-lg border border-border p-3">
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-sm text-ink-1">Sessions and key events by day</h2>
            <SourceBadge source="Analytics" />
          </div>
          <div className="h-[220px]">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={series} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
                <CartesianGrid stroke={T.grid} strokeDasharray="3 3" />
                <XAxis dataKey="date" tick={T.tick} />
                <YAxis yAxisId="sessions" tick={T.tick} width={50} tickFormatter={(v) => num(v)} />
                {/* Key events are ~1-3% of sessions: on the sessions axis they would be a flat line at zero. */}
                <YAxis yAxisId="key" orientation="right" tick={T.tick} width={40} tickFormatter={(v) => num(v)} />
                <Tooltip contentStyle={T.tooltip} formatter={(v, n) => [num(Number(v)), n === "sessions" ? "Sessions" : "Key events"]} />
                <Legend formatter={(n) => (n === "sessions" ? "Sessions (left axis)" : "Key events (right axis)")} />
                <Line yAxisId="sessions" type="monotone" dataKey="sessions" stroke={T.byName.blue} strokeWidth={T.strokeWidth} dot={false} />
                <Line yAxisId="key" type="monotone" dataKey="key" stroke={T.byName.green} strokeWidth={T.strokeWidth} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </section>
      )}

      {r.rows.length > 0 && (
        <div className="rounded-lg border border-border overflow-x-auto">
          <table className="w-full text-sm min-w-[720px]">
            <thead>
              <tr className="text-xs text-ink-3 text-left bg-surface-sunken">
                <th className="py-1.5 px-3">{GROUPS.find((g) => g.value === group)?.label}</th>
                <th className="text-right">Sessions</th><th className="text-right">Engaged</th><th className="text-right">Key events</th><th className="text-right">Transactions</th><th className="text-right px-3">Revenue</th>
              </tr>
            </thead>
            <tbody>
              {r.rows.map((row, i) => (
                <tr key={`${label(row, group)}-${row.currency}-${i}`} className="border-t border-border">
                  <td className="py-1.5 px-3 truncate max-w-[340px]" title={group === "campaign" && row.session_campaign_id ? `${label(row, group)} · ID ${row.session_campaign_id}${row.campaign ? " · linked to the ad campaign" : ""}` : label(row, group)}>{label(row, group)}</td>
                  <td className="text-right tabular-nums">{num(row.sessions)}</td>
                  <td className="text-right tabular-nums">{pct(row.engagement_rate)}</td>
                  <td className="text-right tabular-nums">{num(row.key_events)}</td>
                  <td className="text-right tabular-nums">{num(row.transactions)}</td>
                  <td className="text-right tabular-nums px-3">{money(row.purchase_revenue, row.currency)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {(start > 0 || start + PAGE < r.total_rows) && (
            <div className="flex items-center gap-2 px-3 py-1.5 border-t border-border text-xs text-ink-3">
              <span>{start + 1}–{Math.min(start + PAGE, r.total_rows)} of {r.total_rows}</span>
              <Button size="sm" variant="ghost" disabled={start === 0} onClick={() => setStart(Math.max(0, start - PAGE))}>Previous</Button>
              <Button size="sm" variant="ghost" disabled={start + PAGE >= r.total_rows} onClick={() => setStart(start + PAGE)}>Next</Button>
            </div>
          )}
        </div>
      )}
      <p className="text-xs text-ink-3">GA4 attributes sessions by its own rules. These figures are not ad-platform results and not ERPNext records, and are never added to either.</p>
    </div>
  );
}
