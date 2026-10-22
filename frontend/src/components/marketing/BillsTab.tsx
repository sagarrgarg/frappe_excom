import { useState } from "react";
import { CheckCheck, ExternalLink, Loader2 } from "lucide-react";
import { useFrappePostCall } from "frappe-react-sdk";
import { toast } from "sonner";
import { useFrappeGetCall } from "@/lib/api";
import { Badge, Button, EmptyState, SegmentedControl, type Accent } from "../primitives";
import { serverMessage } from "../admin/util";
import { money } from "./format";

/** One row of excom.excom_ads.api_bills.review_queue. */
type BillRow = {
  name: string;
  status: "Fetched" | "Draft Created" | "Submitted" | "Needs Review" | "Duplicate" | "Error";
  provider: string;
  source_type: "API" | "Manual";
  document_type: string;
  bill_no: string;
  bill_date: string;
  company: string;
  supplier: string | null;
  currency: string;
  net_amount: number;
  tax_amount: number;
  total_amount: number;
  purchase_invoice: string | null;
  differences: string[];
  gstr2b_status: string | null;
  can_approve: boolean;
};
type Queue = { rows: BillRow[]; total: number; can_approve: boolean };
type View = "open" | "Submitted";

const STATUS: Record<string, Accent> = { "Needs Review": "amber", "Draft Created": "blue", Error: "rose", Duplicate: "neutral", Fetched: "neutral", Submitted: "green" };

/** Ad bills waiting for a person: drafts to approve (maker-checker) and bills with findings. */
export function BillsTab() {
  const [view, setView] = useState<View>("open");
  const { data, isLoading, mutate } = useFrappeGetCall<{ message: Queue }>("excom.excom_ads.api_bills.review_queue", view === "open" ? {} : { status: "Submitted" }, `ads-bills-${view}`);
  const approve = useFrappePostCall("excom.excom_ads.api_bills.approve_bill");
  const [busy, setBusy] = useState<string | null>(null);
  const rows = data?.message?.rows ?? [];

  const onApprove = async (b: BillRow) => {
    const ack = b.differences.length > 0;
    if (ack && !window.confirm(`Approve ${b.provider} bill ${b.bill_no} with these findings?\n\n${b.differences.join("\n")}`)) return;
    setBusy(b.name);
    try {
      await approve.call({ name: b.name, acknowledge_differences: ack ? 1 : 0 });
      toast.success(`Submitted ${b.purchase_invoice}`);
      mutate();
    } catch (e) {
      toast.error(serverMessage(e) || "Could not approve this bill");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <SegmentedControl<View> value={view} onChange={setView} variant="segmented" ariaLabel="Bill view" segments={[{ value: "open", label: "To review" }, { value: "Submitted", label: "Submitted" }]} />
        <a href="/app/ads-bill-document/new" target="_blank" rel="noreferrer" className="ml-auto inline-flex items-center gap-0.5 text-xs text-crayon-blue-text hover:underline">Upload a bill<ExternalLink className="size-3" /></a>
      </div>
      {isLoading && !rows.length ? (
        <div className="flex justify-center py-12"><Loader2 className="size-5 animate-spin text-ink-3" /></div>
      ) : !rows.length ? (
        <EmptyState title={view === "open" ? "No ad bills to review" : "No submitted ad bills yet"} hint={view === "open" ? "Platform invoices (monthly invoicing) and uploaded bills appear here until a second person approves them." : undefined} />
      ) : (
        <ul className="rounded-lg border border-border divide-y divide-border">
          {rows.map((b) => (
            <li key={b.name} className="p-3 flex items-start gap-3 min-w-0">
              <Badge accent={STATUS[b.status] ?? "neutral"}>{b.status}</Badge>
              <div className="min-w-0 flex-1">
                <p className="text-sm text-ink-1 truncate">
                  <a href={`/app/ads-bill-document/${encodeURIComponent(b.name)}`} target="_blank" rel="noreferrer" className="hover:underline">{b.provider} {b.document_type.toLowerCase()} {b.bill_no}</a>
                  <span className="text-ink-3"> · {b.supplier ?? "supplier not resolved"} · {b.bill_date}</span>
                </p>
                <p className="text-xs text-ink-2 tabular-nums">
                  {money(b.net_amount, b.currency)} + tax {money(b.tax_amount, b.currency)} = {money(b.total_amount, b.currency)} · {b.source_type === "API" ? "from the platform" : "uploaded"}
                  {b.purchase_invoice && (
                    <> · <a href={`/app/purchase-invoice/${encodeURIComponent(b.purchase_invoice)}`} target="_blank" rel="noreferrer" className="text-crayon-blue-text hover:underline">{b.purchase_invoice}</a></>
                  )}
                  {b.gstr2b_status ? ` · GSTR-2B ${b.gstr2b_status}` : ""}
                </p>
                {b.differences.length > 0 && (
                  <ul className="mt-1 text-xs text-crayon-amber-text list-disc pl-4 space-y-0.5">
                    {b.differences.map((d, i) => <li key={i}>{d}</li>)}
                  </ul>
                )}
              </div>
              {view === "open" && !b.can_approve && data?.message?.can_approve && b.purchase_invoice && (b.status === "Draft Created" || b.status === "Needs Review") && (
                <span className="text-xs text-ink-3 shrink-0 max-w-[180px] text-right" title="Maker-checker: whoever typed or edited a bill cannot approve it">You prepared or edited this bill; another person approves it</span>
              )}
              {view === "open" && b.can_approve && (
                <Button size="sm" variant="ghost" disabled={busy === b.name} onClick={() => onApprove(b)}>
                  {busy === b.name ? <Loader2 className="animate-spin" /> : <CheckCheck />}Approve
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
