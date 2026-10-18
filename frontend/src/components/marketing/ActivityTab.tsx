import { Loader2 } from "lucide-react";
import { useFrappeGetCall } from "@/lib/api";
import { Badge, EmptyState, type Accent } from "../primitives";
import { ago } from "./format";
import type { SyncJobRow } from "./types";
import { useMarketingFilters } from "./useMarketingFilters";

const ACCENT: Record<SyncJobRow["status"], Accent> = { Queued: "neutral", Running: "blue", Success: "green", Partial: "amber", Failed: "rose" };

/** Every sync with who started it and what it wrote. Release 2 adds actions with their actor. */
export function ActivityTab() {
  const { filters } = useMarketingFilters();
  const params = { account: filters.account, page_length: 100 };
  const { data, isLoading } = useFrappeGetCall<{ message: SyncJobRow[] }>("excom.excom_ads.dashboard.activity", params, `ads-activity-${JSON.stringify(params)}`, { refreshInterval: 30_000 });
  const rows = data?.message ?? [];
  if (isLoading && !rows.length) return <div className="flex justify-center py-12"><Loader2 className="size-5 animate-spin text-ink-3" /></div>;
  if (!rows.length) return <EmptyState title="No sync runs yet" />;
  return (
    <div className="rounded-lg border border-border overflow-x-auto">
      <table className="w-full text-sm min-w-[760px]">
        <thead className="bg-surface-sunken"><tr className="text-xs text-ink-3 text-left"><th className="px-3 py-1.5">When</th><th>Account</th><th>Job</th><th>Status</th><th>Window</th><th className="text-right">Calls</th><th className="text-right">Rows</th><th>By</th><th className="px-3">Problem</th></tr></thead>
        <tbody>
          {rows.map((j) => (
            <tr key={j.name} className="border-t border-border align-top">
              <td className="px-3 py-1.5 whitespace-nowrap text-ink-2" title={j.started_at ?? ""}>{ago(j.started_at)}</td>
              <td className="truncate max-w-[180px]">{j.account}</td>
              <td>{j.job_type}</td>
              <td><Badge accent={ACCENT[j.status]}>{j.status}</Badge></td>
              <td className="text-xs text-ink-3 whitespace-nowrap">{j.window_from ? `${j.window_from} → ${j.window_to}` : ""}</td>
              <td className="text-right tabular-nums">{j.api_calls}</td>
              <td className="text-right tabular-nums">{j.records_written}</td>
              <td className="text-xs text-ink-3 truncate max-w-[140px]">{j.triggered_by}</td>
              <td className="px-3 text-xs text-crayon-rose-text max-w-[280px] truncate" title={j.error ?? ""}>{j.error_class ? `${j.error_class}: ${j.error}` : ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
