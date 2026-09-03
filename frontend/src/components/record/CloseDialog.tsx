import { useState } from "react";
import { useFrappePostCall } from "frappe-react-sdk";
import { CheckCircle2, XCircle, Ban, Copy, ArrowUpRight, AlertOctagon } from "lucide-react";
import { toast } from "sonner";
import { Modal, Button, Field, Input, Textarea } from "../primitives";
import { cn } from "../ui/utils";
import type { UnifiedContact } from "../../types";

const OUTCOMES = [
  { id: "Resolved", label: "Resolved", hint: "Answered / done", icon: <CheckCircle2 />, negative: false },
  { id: "Converted", label: "Converted", hint: "Became an order / customer", icon: <ArrowUpRight />, negative: false },
  { id: "Lost", label: "Lost", hint: "Went elsewhere, price, timing", icon: <XCircle />, negative: true },
  { id: "Not Interested", label: "Not interested", hint: "No requirement", icon: <Ban />, negative: true },
  { id: "Duplicate", label: "Duplicate", hint: "Same enquiry exists", icon: <Copy />, negative: true },
  { id: "Spam", label: "Spam", hint: "Junk / unsolicited", icon: <AlertOctagon />, negative: true },
];
const REASONS: Record<string, string[]> = {
  Lost: ["Price too high", "Chose competitor", "No response from customer", "Timing / budget", "Out of service area"],
  "Not Interested": ["Just enquiring", "Wrong product", "No requirement now"],
  Resolved: ["Query answered", "Order placed", "Issue fixed", "Information shared"],
  Converted: ["Order received", "Quotation accepted"],
  Duplicate: ["Same person, other number", "Re-submitted enquiry"],
  Spam: ["Promotional", "Bot / automated", "Abusive"],
};

/**
 * Closure scene: pick an outcome, a reason and a note. Archives every open conversation of the
 * contact, writes the activity log on the linked Lead / Opportunity / Customer (Desk timeline) and,
 * for negative outcomes, closes the CRM record too.
 */
export function CloseDialog({ open, onClose, contact, crm, onDone }: { open: boolean; onClose: () => void; contact: UnifiedContact; crm?: { doctype: string; name: string } | null; onDone: () => void }) {
  const [outcome, setOutcome] = useState("Resolved");
  const [reason, setReason] = useState("");
  const [note, setNote] = useState("");
  const [closeCrm, setCloseCrm] = useState(true);
  const { call, loading } = useFrappePostCall("excom.excom.api.record.close_conversation");
  const o = OUTCOMES.find((x) => x.id === outcome)!;
  const crmLabel = crm ? (crm.doctype === "Lead" ? "mark the Lead Do Not Contact" : crm.doctype === "Opportunity" ? "mark the Opportunity Lost" : null) : null;
  const submit = async () => {
    try {
      const r = await call({ omni_identity: contact.id, outcome, reason, note, close_crm: o.negative && closeCrm ? 1 : 0 });
      const crmS = r.message?.crm?.status;
      toast.success(`Closed · ${outcome}${crmS ? ` · ${r.message.crm.doctype} → ${crmS}` : ""}`);
      onDone(); onClose();
    } catch (e) { toast.error((e as { message?: string })?.message || "Could not close"); }
  };
  return (
    <Modal open={open} onOpenChange={(v) => !v && onClose()} title={`Close · ${contact.contactName}`} width="max-w-lg"
      footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant={o.negative ? "danger" : "primary"} disabled={loading} onClick={submit}>{loading ? "Closing…" : `Close as ${o.label}`}</Button></>}>
      <div className="p-3 space-y-3">
        <div className="grid grid-cols-2 tablet:grid-cols-3 gap-1.5" role="radiogroup" aria-label="Outcome">
          {OUTCOMES.map((x) => (
            <button key={x.id} type="button" role="radio" aria-checked={outcome === x.id} onClick={() => { setOutcome(x.id); setReason(""); }}
              className={cn("rounded-md border px-2 py-2 text-left min-w-0 [&_svg]:size-4", outcome === x.id ? (x.negative ? "border-crayon-rose-text bg-crayon-rose-tint" : "border-crayon-green-text bg-crayon-green-tint") : "border-border hover:bg-surface-hover")}>
              <div className="flex items-center gap-1.5 text-sm text-ink-1">{x.icon}{x.label}</div>
              <div className="text-xs text-ink-3 truncate">{x.hint}</div>
            </button>
          ))}
        </div>
        <Field label="Reason">
          <div className="chip-row mb-1.5">{(REASONS[outcome] || []).map((r) => <button key={r} type="button" onClick={() => setReason(r)} className={cn("rounded-full border px-2 h-6 text-xs shrink-0", reason === r ? "bg-surface-active border-border text-ink-1" : "border-border text-ink-2 hover:text-ink-1")}>{r}</button>)}</div>
          <Input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Short reason (goes on the record timeline)" />
        </Field>
        <Field label="Note" hint="Optional. Written to the activity log of the conversation and the linked record."><Textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} /></Field>
        {o.negative && crmLabel && (
          <label className="flex items-center gap-2 text-sm text-ink-1"><input type="checkbox" className="size-4" checked={closeCrm} onChange={(e) => setCloseCrm(e.target.checked)} />Also {crmLabel} ({crm!.name})</label>
        )}
        <p className="text-xs text-ink-3">All open conversations of this contact are archived. You can reopen from the Archived view or the ⋯ menu.</p>
      </div>
    </Modal>
  );
}
