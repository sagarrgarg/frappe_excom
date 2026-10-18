import { ExternalLink, Loader2, Plus } from "lucide-react";
import { useFrappeGetCall } from "@/lib/api";
import { Badge, EmptyState, type Accent } from "../primitives";
import { SourceBadge } from "./bits";
import { ago, money, NA, num } from "./format";
import type { Availability, BillingDetail, BillingResponse, Figure } from "./types";
import { useMarketingFilters } from "./useMarketingFilters";

const AVAIL_ACCENT: Record<Availability, Accent> = {
  Available: "green",
  Stale: "amber",
  "Not Set": "neutral",
  Unsupported: "neutral",
  "No Data": "neutral",
  "Not Read": "neutral",
  "Permission Missing": "rose",
  Error: "rose",
};

const AVAIL_HINT: Record<Availability, string> = {
  Available: "As read",
  Stale: "An older reading: the latest read failed or is older than the stale limit",
  "Not Set": "The platform reports this, but the account has none set",
  Unsupported: "The platform does not expose this for this account",
  "No Data": "Nothing to read or compute from yet",
  "Not Read": "Not read yet",
  "Permission Missing": "The connection lacks the access this field needs",
  Error: "The last read failed",
};

/** Availability, said out loud. Available needs no badge. */
function Avail({ status }: { status: Availability }) {
  if (status === "Available") return null;
  return <Badge accent={AVAIL_ACCENT[status]} title={AVAIL_HINT[status]}>{status}</Badge>;
}

/** A figure is shown only when it has a value; otherwise "—" with why. Never a 0 for "not available". */
function Fig({ label, fig, render, hint }: { label: string; fig: Figure<number | string | boolean> | undefined; render: (v: never) => string; hint?: string }) {
  if (!fig) return null;
  const has = fig.value !== null && fig.value !== undefined;
  return (
    <>
      <dt className="text-ink-3 truncate" title={hint}>{label}</dt>
      <dd className="flex items-center justify-end gap-1.5 min-w-0">
        <span className={has ? "tabular-nums text-ink-1 truncate" : "text-ink-3"}>{has ? render(fig.value as never) : NA}</span>
        <Avail status={fig.status} />
      </dd>
    </>
  );
}

function Section({ title, badge, children }: { title: string; badge?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="space-y-1">
      <div className="flex items-center justify-between gap-2">
        <h4 className="text-xs text-ink-2">{title}</h4>
        {badge}
      </div>
      <dl className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-0.5 text-xs">{children}</dl>
    </section>
  );
}

const yesNo = (v: boolean) => (v ? "Yes" : "No");

function AccountCard({ a, ledgerLabel, canAdd }: { a: BillingDetail; ledgerLabel: string; canAdd: boolean }) {
  const c = a.currency;
  const m = (v: number) => money(v, c);
  const r = a.reported;
  const k = a.calculated;
  const budget = r?.account_budget;
  const led = a.ledger;
  const deskAccount = encodeURIComponent(a.account);
  return (
    <article className="rounded-lg border border-border p-3 min-w-0 space-y-3">
      <header className="flex items-start gap-2 min-w-0">
        <div className="min-w-0 flex-1">
          <h3 className="text-sm text-ink-1 truncate" title={a.account_name}>{a.account_name}</h3>
          <p className="text-xs text-ink-3 truncate">
            {a.provider} · {a.payment_model || "Unknown"} · as of {ago(a.as_of)}
            {a.read_status && a.read_status !== "OK" ? ` · last read ${a.read_status.toLowerCase()}` : ""}
          </p>
        </div>
        {a.stale && <Badge accent="amber" title="Older than the stale limit in Ads Settings">Stale</Badge>}
      </header>
      {a.last_error && <p className="text-xs text-crayon-rose-text line-clamp-2" title={a.last_error}>{a.last_error}</p>}

      {!r ? (
        <p className="text-xs text-ink-3">Not read yet: the next structure sync records this account's billing.</p>
      ) : (
        <Section title={`Reported by ${a.provider}`} badge={<SourceBadge source="Platform" />}>
          {a.provider === "Google" ? (
            <>
              <Fig label="Billing setup" fig={r.billing_setup_status} render={(v: string) => v} />
              <dt className="text-ink-3">Account budget</dt>
              <dd className="flex items-center justify-end gap-1.5 min-w-0">
                <span className={budget?.value ? "text-ink-1 truncate" : "text-ink-3"}>{budget?.value ? `${budget.value.name ?? budget.value.id} (${budget.value.budget_status})` : NA}</span>
                {budget && <Avail status={budget.status} />}
              </dd>
              {budget?.value && (
                <>
                  <dt className="text-ink-3">Adjusted limit</dt>
                  <dd className="text-right tabular-nums text-ink-1">{budget.value.unlimited ? "Unlimited" : money(budget.value.adjusted_limit ?? budget.value.approved_limit, c)}</dd>
                  <dt className="text-ink-3">Amount served</dt>
                  <dd className="text-right tabular-nums text-ink-1">{money(budget.value.amount_served, c)}</dd>
                  <dt className="text-ink-3">Runs</dt>
                  <dd className="text-right text-ink-2 truncate">{(budget.value.start ?? "").slice(0, 10)} → {budget.value.end === "FOREVER" ? "no end" : (budget.value.end ?? "").slice(0, 10)}</dd>
                </>
              )}
              {budget?.status === "Unsupported" && <dd className="col-span-2 text-ink-3">Google exposes billing only for accounts on monthly invoicing.</dd>}
            </>
          ) : (
            <>
              <Fig label="Spent against cap" fig={r.amount_spent} render={m} hint="Meta amount_spent: lifetime spend, or spend since the cap was last reset" />
              <Fig label="Spend cap" fig={r.spend_cap} render={m} />
              <Fig label="Amount due (bill)" fig={r.amount_due} render={m} hint="Meta balance: the bill amount due. Not a prepaid balance." />
              <Fig label="Prepay account" fig={r.is_prepay} render={yesNo} />
              <Fig label="Funding source" fig={r.funding_type} render={(v: string) => v.replace(/_/g, " ").toLowerCase()} />
            </>
          )}
        </Section>
      )}

      {k && (
        <Section title="Calculated by Excom" badge={<span className="text-xs text-ink-3">from reported figures + daily results</span>}>
          <Fig label="Headroom" fig={k.headroom} render={m} hint={k.headroom.basis ?? undefined} />
          <Fig label={`Average daily spend${k.avg_daily_spend.days ? ` (${k.avg_daily_spend.days} d)` : ""}`} fig={k.avg_daily_spend} render={m} />
          <Fig label="Days of headroom left" fig={k.days_left} render={(v: number) => num(v, false)} />
          <Fig label="Projected to run out" fig={k.exhaustion_date} render={(v: string) => v} />
          {k.note && <dd className="col-span-2 text-ink-3">{k.note}</dd>}
        </Section>
      )}

      <section className="space-y-1 rounded-md bg-surface-sunken px-2 py-1.5">
        <div className="flex items-center justify-between gap-2">
          <h4 className="text-xs text-ink-2">{ledgerLabel}</h4>
          <div className="flex items-center gap-2 text-xs">
            {canAdd && (
              <a href={`/app/ads-billing-record/new?account=${deskAccount}`} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 text-crayon-blue-text hover:underline" title="Record a top-up on the Desk">
                <Plus className="size-3" />Top-up
              </a>
            )}
            {(led?.records ?? 0) > 0 && (
              <a href={`/app/ads-billing-record?account=${deskAccount}`} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 text-crayon-blue-text hover:underline">
                Records<ExternalLink className="size-3" />
              </a>
            )}
          </div>
        </div>
        {!led || led.records === 0 ? (
          <p className="text-xs text-ink-3">No top-ups recorded. Record prepaid top-ups to estimate the remaining balance; no platform reports it.</p>
        ) : (
          <dl className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-0.5 text-xs">
            <dt className="text-ink-3">Estimated balance</dt>
            <dd className="text-right tabular-nums text-ink-1">{led.balance === null ? NA : money(led.balance, c)}</dd>
            <dt className="text-ink-3">Top-ups − refunds ± adjustments</dt>
            <dd className="text-right tabular-nums text-ink-2">{money((led.topups ?? 0) - (led.refunds ?? 0) + (led.adjustments ?? 0), c)}</dd>
            <dt className="text-ink-3">Platform spend since {led.since}</dt>
            <dd className="text-right tabular-nums text-ink-2">{led.spend_since === null || led.spend_since === undefined ? NA : money(led.spend_since, c)}</dd>
            {led.days_left !== null && led.days_left !== undefined && (
              <>
                <dt className="text-ink-3">Days left at recent pace</dt>
                <dd className="text-right tabular-nums text-ink-2">{num(led.days_left, false)}</dd>
              </>
            )}
            {led.warning && <dd className="col-span-2 text-crayon-amber-text">{led.warning}</dd>}
          </dl>
        )}
      </section>
    </article>
  );
}

/** Per-account billing: platform-reported figures, our calculations and the ledger estimate, never mixed. */
export function BillingTab() {
  const { filters } = useMarketingFilters();
  const { data, isLoading, error } = useFrappeGetCall<{ message: BillingResponse }>("excom.excom_ads.billing.get_billing", filters, `ads-billing-${JSON.stringify(filters)}`, { refreshInterval: 5 * 60_000, keepPreviousData: true });
  const b = data?.message;
  if (isLoading && !b) return <div className="flex justify-center py-16"><Loader2 className="size-5 animate-spin text-ink-3" /></div>;
  if (error) return <EmptyState title="Could not load billing" hint={String((error as { message?: string })?.message ?? "")} />;
  if (!b?.accounts.length) return <EmptyState title="No ad accounts in view" />;
  return (
    <div className="space-y-3">
      <p className="text-xs text-ink-3">
        Neither Meta nor Google lets an API read a prepaid balance. Reported figures are exactly what each platform returns; headroom, days left and the ledger balance are calculated here.
        Low Headroom alerts fire below {b.low_headroom_days} days; readings older than {b.stale_after_hours} h are marked Stale.
      </p>
      <div className="grid gap-2 grid-cols-[repeat(auto-fill,minmax(320px,1fr))]">
        {b.accounts.map((a) => <AccountCard key={a.account} a={a} ledgerLabel={b.ledger_label} canAdd={b.can_add_records} />)}
      </div>
    </div>
  );
}
