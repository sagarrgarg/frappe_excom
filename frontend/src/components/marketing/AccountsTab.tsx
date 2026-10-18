import { Loader2, RefreshCw } from "lucide-react";
import { useFrappePostCall } from "frappe-react-sdk";
import { toast } from "sonner";
import { useFrappeGetCall } from "@/lib/api";
import { EmptyState, OverflowMenu } from "../primitives";
import { serverMessage } from "../admin/util";
import { StatusBadge } from "./bits";
import { ago, money, NA } from "./format";
import type { AccountRow } from "./types";
import { useMarketingFilters } from "./useMarketingFilters";

const JOBS = ["Structure", "Metrics", "Settle", "Backfill"] as const;

export function AccountsTab() {
  const { filters } = useMarketingFilters();
  const { data, isLoading, mutate } = useFrappeGetCall<{ message: AccountRow[] }>("excom.excom_ads.dashboard.accounts", filters, `ads-accounts-${JSON.stringify(filters)}`, { refreshInterval: 60_000 });
  const { call } = useFrappePostCall("excom.excom_ads.api.sync_now");
  const rows = data?.message ?? [];

  const sync = async (account: string, job_type: string) => {
    try {
      const r = (await call({ account, job_type })) as { message?: { already_running?: boolean } };
      toast.success(r?.message?.already_running ? `${job_type} sync is already queued` : `${job_type} sync queued`);
      mutate();
    } catch (e) {
      toast.error(serverMessage(e) || "Could not queue the sync");
    }
  };

  if (isLoading && !rows.length) return <div className="flex justify-center py-16"><Loader2 className="size-5 animate-spin text-ink-3" /></div>;
  if (!rows.length) return <EmptyState title="No ad accounts yet" hint="Admin → Meta → Discover lists your ad accounts; enable one to start syncing." />;

  return (
    <div className="grid gap-2 grid-cols-[repeat(auto-fill,minmax(320px,1fr))]">
      {rows.map((a) => (
        <article key={a.name} className="rounded-lg border border-border p-3 min-w-0 space-y-2">
          <header className="flex items-start gap-2 min-w-0">
            <div className="min-w-0 flex-1">
              <h3 className="text-sm text-ink-1 truncate" title={a.account_name}>{a.account_name}</h3>
              <p className="text-xs text-ink-3 truncate">{a.provider} · {a.external_id} · {a.company}{a.brand ? ` · ${a.brand}` : ""}</p>
            </div>
            <StatusBadge status={a.status} />
            {a.can_manage && (
              <OverflowMenu label="Sync now" groups={[JOBS.map((j) => ({ id: j, label: `Sync ${j.toLowerCase()} now`, icon: <RefreshCw />, onSelect: () => sync(a.name, j) }))]} />
            )}
          </header>
          {a.disable_reason && <p className="text-xs text-crayon-rose-text">Platform reason: {a.disable_reason}</p>}
          {a.error_class && (
            <p className="text-xs text-crayon-rose-text line-clamp-3" title={a.last_error ?? ""}>
              {a.error_class}{a.consecutive_failures > 1 ? ` · ${a.consecutive_failures} failures in a row` : ""}: {a.last_error}
            </p>
          )}
          {!a.sync_enabled && <p className="text-xs text-ink-3">Sync is off for this account.</p>}
          <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-0.5 text-xs">
            <dt className="text-ink-3">Campaign tree</dt><dd className="text-ink-2">{ago(a.last_structure_sync)}</dd>
            <dt className="text-ink-3">Results</dt><dd className="text-ink-2">{ago(a.last_metrics_sync)}{a.metrics_synced_from ? ` · ${a.metrics_synced_from} → ${a.metrics_synced_until}` : ""}</dd>
            <dt className="text-ink-3">Currency / zone</dt><dd className="text-ink-2">{a.currency ?? NA} · {a.timezone_name ?? NA}</dd>
            {a.billing && (
              <>
                <dt className="text-ink-3">Billing</dt>
                <dd className="text-ink-2">
                  {a.billing.billing_status !== "Reported" ? `Not available (${a.billing.billing_status})` : (
                    <>Due {money(a.billing.amount_due, a.currency)} · {a.billing.spend_cap === null ? "no account cap" : `headroom ${money(a.billing.headroom, a.currency)} of ${money(a.billing.spend_cap, a.currency)}`}</>
                  )}
                </dd>
              </>
            )}
          </dl>
        </article>
      ))}
    </div>
  );
}
