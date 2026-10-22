import { useMemo, useState } from "react";
import { BellOff, Check, CheckCheck, ExternalLink, Loader2 } from "lucide-react";
import { useFrappePostCall } from "frappe-react-sdk";
import { toast } from "sonner";
import { useFrappeGetCall } from "@/lib/api";
import { deskUrl } from "../../hooks/useRecordLinks";
import { Badge, Button, EmptyState, SegmentedControl } from "../primitives";
import { serverMessage } from "../admin/util";
import { SEVERITY_ACCENT } from "./bits";
import { ago } from "./format";
import type { AlertRow } from "./types";

type View = "open" | "Resolved";
// Worst first, so a Critical alert is never below a page of Info ones; the server's newest-first order breaks ties.
const RANK: Record<AlertRow["severity"], number> = { Critical: 0, High: 1, Medium: 2, Info: 3 };

function when(ts: string): string {
  const d = new Date(ts.replace(" ", "T"));
  return Number.isNaN(d.getTime()) ? ts : d.toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function AlertsTab() {
  const [view, setView] = useState<View>("open");
  const { data, isLoading, mutate } = useFrappeGetCall<{ message: AlertRow[] }>("excom.excom_ads.dashboard.alerts", { status: view }, `ads-alerts-${view}`, { refreshInterval: 60_000 });
  const ack = useFrappePostCall("excom.excom_ads.api.acknowledge_alert");
  const snooze = useFrappePostCall("excom.excom_ads.api.snooze_alert");
  const resolve = useFrappePostCall("excom.excom_ads.api.resolve_alert");
  const rows = useMemo(() => {
    const list = data?.message ?? [];
    return view === "open" ? [...list].sort((a, b) => RANK[a.severity] - RANK[b.severity]) : list;
  }, [data, view]);

  const act = async (fn: (p: Record<string, unknown>) => Promise<unknown>, params: Record<string, unknown>, ok: string) => {
    try {
      await fn(params);
      toast.success(ok);
      mutate();
    } catch (e) {
      toast.error(serverMessage(e) || "That did not work");
    }
  };

  return (
    <div className="space-y-2">
      <SegmentedControl<View> value={view} onChange={setView} variant="segmented" ariaLabel="Alert view" segments={[{ value: "open", label: "Open" }, { value: "Resolved", label: "Resolved" }]} />
      {isLoading && !rows.length ? (
        <div className="flex justify-center py-12"><Loader2 className="size-5 animate-spin text-ink-3" /></div>
      ) : !rows.length ? (
        <EmptyState title={view === "open" ? "No open alerts" : "No resolved alerts yet"} hint={view === "open" ? "Sync failures, token problems, account status changes and rejected ads appear here." : undefined} />
      ) : (
        <ul className="rounded-lg border border-border divide-y divide-border">
          {rows.map((a) => (
            <li key={a.name} className="p-3 flex items-start gap-3 min-w-0">
              <Badge accent={SEVERITY_ACCENT[a.severity]} solid={a.severity === "Critical"}>{a.severity}</Badge>
              <div className="min-w-0 flex-1">
                <p className="text-sm text-ink-1 truncate" title={a.title}>{a.title}</p>
                {a.message && <p className="text-xs text-ink-2 whitespace-pre-line line-clamp-3">{a.message}</p>}
                <p className="text-xs text-ink-3 mt-0.5">
                  {a.alert_type} · {a.status}{a.occurrences > 1 ? ` · seen ${a.occurrences} times` : ""} · last {ago(a.last_seen)}
                  {a.snoozed_until ? ` · snoozed until ${when(a.snoozed_until)}` : ""}{a.acknowledged_by ? ` · acknowledged by ${a.acknowledged_by}` : ""}{a.escalated ? ` · escalated ${ago(a.escalated_at)}` : ""}
                </p>
                {a.reference_doctype && a.reference_name && (
                  <a href={deskUrl(a.reference_doctype, a.reference_name)} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs text-crayon-blue-text hover:underline mt-0.5">
                    <ExternalLink className="size-3" />Open {a.reference_doctype.replace(/^Ads /, "").toLowerCase()}
                  </a>
                )}
              </div>
              {a.status !== "Resolved" && (
                <div className="flex items-center gap-1 shrink-0">
                  {a.status === "Open" && <Button size="sm" variant="ghost" onClick={() => act(ack.call, { name: a.name }, "Acknowledged")}><Check />Acknowledge</Button>}
                  {a.severity !== "Critical" && <Button size="sm" variant="ghost" onClick={() => act(snooze.call, { name: a.name, hours: 24 }, "Snoozed for a day")}><BellOff />Snooze 1 day</Button>}
                  <Button size="sm" variant="ghost" onClick={() => act(resolve.call, { name: a.name }, "Resolved")}><CheckCheck />Resolve</Button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
