import { AlertTriangle, Info } from "lucide-react";
import { Kpi, SourceBadge } from "./bits";
import { ago, money, num, pct, ratio } from "./format";
import type { DataQuality, Freshness, VerifiedBlock, VerifiedTotals } from "./types";

const NOT_YOURS = "Not available with your permissions";

function share(part: number | null | undefined, whole: number | null | undefined): string | undefined {
  if (part === null || part === undefined || !whole) return undefined;
  return pct((part / whole) * 100);
}

function Tiles({ t, finance }: { t: VerifiedTotals; finance: boolean }) {
  const c = t.currency ?? undefined;
  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(170px,1fr))] gap-2">
      <Kpi source="Verified" label="Verified leads" value={num(t.leads)} sub={t.qualified !== null ? `Qualified ${num(t.qualified)}${share(t.qualified, t.leads) ? ` (${share(t.qualified, t.leads)})` : ""}` : undefined} hint={t.leads === null ? NOT_YOURS : "ERPNext Leads linked to an ad campaign (platform test leads excluded). Qualified = reached an Opportunity."} />
      <Kpi source="Verified" label="Opportunities" value={num(t.opportunities)} hint={t.opportunities === null ? NOT_YOURS : "Opportunities carrying the campaign, or made from one of its leads"} />
      <Kpi source="Verified" label="Orders" value={num(t.orders)} sub={finance && t.order_value !== null ? `Value ${money(t.order_value, c)}` : undefined} hint={t.orders === null ? NOT_YOURS : "Submitted Sales Orders attributed to a campaign (order touch, UTM campaign or Lead → Quotation chain)"} />
      {finance && (
        <Kpi source="Verified" label="Net revenue" value={money(t.revenue, c)} sub={t.returns ? `Gross ${money(t.gross_revenue, c)} · returns ${money(t.returns, c)}` : undefined} hint={t.revenue === null ? NOT_YOURS : "Submitted Sales Invoices minus their credit notes, in the invoice's period. Cancelled documents excluded."} />
      )}
      {finance && <Kpi source="Verified" label="New customers" value={num(t.new_customers)} hint="Attributed invoices that were the customer's first ever invoice" />}
      <Kpi source="Verified" label="Verified CPL" value={money(t.verified_cpl, c)} sub={t.cost_per_qualified !== null ? `Per qualified ${money(t.cost_per_qualified, c)}` : undefined} hint="Platform spend ÷ verified leads (a deliberate mix, labelled as such)" />
      {finance && <Kpi source="Verified" label="MER" value={ratio(t.mer)} hint="Verified net revenue ÷ total platform ad spend" />}
    </div>
  );
}

/** ERPNext / CRM results: their own block with the Verified badge, never added to platform figures. */
export function VerifiedSection({ verified }: { verified: VerifiedBlock | null | undefined }) {
  if (!verified) return null;
  // Money needs the view_finance right and Sales Invoice read permission; counts do not.
  const finance = !!verified.available?.revenue;
  if (verified.error) {
    return <p className="text-xs text-crayon-rose-text">{verified.error}</p>;
  }
  const totals = verified.totals ?? [];
  const multi = totals.length > 1;
  const un = (verified.unattributed ?? []).filter((u) => (u.orders ?? 0) > 0 || (u.revenue ?? 0) !== 0);
  const top = (verified.by_campaign ?? []).slice(0, 8);
  return (
    <section className="space-y-2">
      <div className="flex items-center gap-2">
        <h2 className="text-sm text-ink-1">Verified results</h2>
        <SourceBadge source="Verified" />
        <span className="text-xs text-ink-3 truncate">From ERPNext: never added to the platform figures above.</span>
      </div>
      {!totals.length && <p className="text-xs text-ink-3">No verified leads, orders or revenue for these campaigns in this range.</p>}
      {totals.map((t) => (
        <div key={t.currency ?? "none"} className="space-y-1.5">
          {multi && <h3 className="text-xs text-ink-2">{t.currency}</h3>}
          <Tiles t={t} finance={finance} />
        </div>
      ))}
      {(un.length > 0 || (verified.leads_unlinked ?? 0) > 0) && (
        <p className="flex items-start gap-1.5 text-xs text-ink-3">
          <Info className="size-3.5 shrink-0 mt-0.5" />
          <span>
            {un.map((u) => `Unattributed: ${num(u.orders)} order${u.orders === 1 ? "" : "s"}${finance && u.revenue !== null ? `, ${money(u.revenue, u.currency)} net revenue` : ""}`).join(" · ")}
            {un.length > 0 && " — no order touch, UTM campaign or CRM chain links them to a campaign, so they are not credited to any. "}
            {(verified.leads_unlinked ?? 0) > 0 && `${num(verified.leads_unlinked)} ad lead${verified.leads_unlinked === 1 ? "" : "s"} not linked to a campaign yet.`}
          </span>
        </p>
      )}
      {top.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full text-sm min-w-[620px]">
            <thead>
              <tr className="text-xs text-ink-3 text-left bg-surface-sunken">
                <th className="py-1 px-3">Campaign</th><th className="text-right">Leads</th><th className="text-right">Qualified</th><th className="text-right">Opps</th><th className={finance ? "text-right" : "text-right px-3"}>Orders</th>
                {finance && <th className="text-right px-3">Net revenue</th>}
              </tr>
            </thead>
            <tbody>
              {top.map((r) => (
                <tr key={`${r.campaign}-${r.currency}`} className="border-t border-border">
                  <td className="py-1.5 px-3 truncate max-w-[280px]" title={r.campaign_name ?? r.campaign}>{r.campaign_name ?? r.campaign}</td>
                  <td className="text-right tabular-nums">{num(r.leads)}</td>
                  <td className="text-right tabular-nums">{num(r.qualified)}</td>
                  <td className="text-right tabular-nums">{num(r.opportunities)}</td>
                  <td className={finance ? "text-right tabular-nums" : "text-right tabular-nums px-3"}>{num(r.orders)}</td>
                  {finance && <td className="text-right tabular-nums px-3">{money(r.revenue, r.currency)}</td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

/** Spend reconciliation and manual-import freshness, said out loud next to the figures. */
export function DataQualityNote({ quality, freshness }: { quality: DataQuality | null | undefined; freshness: Freshness | undefined }) {
  const mism = quality?.spend_mismatch ?? [];
  const manual = freshness?.manual ?? [];
  if (!mism.length && !manual.length) return null;
  return (
    <div className="space-y-1">
      {mism.length > 0 && (
        <div className="flex items-start gap-2 rounded-md border border-crayon-amber-base/50 bg-crayon-amber-tint px-3 py-2 text-xs text-crayon-amber-text" role="status">
          <AlertTriangle className="size-4 shrink-0 mt-0.5" />
          <div className="min-w-0 space-y-0.5">
            {mism.map((m) => {
              const w = m.worst;
              return (
                <p key={m.account}>
                  {m.account_name ?? m.account}: account-level spend and the sum of its campaigns differ by more than {quality?.threshold_pct}% on {m.days} day{m.days === 1 ? "" : "s"}
                  {" "}(largest {w.date}: account {w.account_spend === null ? "no row" : money(w.account_spend, w.currency)} vs campaigns {w.campaign_spend === null ? "no rows" : money(w.campaign_spend, w.currency)}). Campaign and brand figures may be incomplete.
                </p>
              );
            })}
          </div>
        </div>
      )}
      {manual.length > 0 && (
        <p className="flex items-center gap-1.5 text-xs text-ink-3">
          <Info className="size-3.5 shrink-0" />
          Imported (manual) accounts: {manual.map((m) => `${m.account_name} — last import ${ago(m.last_import)}`).join(", ")}.
        </p>
      )}
    </div>
  );
}
