import { useState } from "react";
import { useFrappeGetCall, useFrappePostCall } from "frappe-react-sdk";
import { UserPlus2 } from "lucide-react";
import { toast } from "sonner";
import { Modal, Button, Field, Input, Select, Textarea } from "../primitives";
import { useCrmOptions } from "../../hooks/useCrm";
import { useInbox } from "../shell/InboxProvider";

/** Type a lead in (walk-in, phone call, exhibition). Creates identity + Lead and opens the record so Start conversation is next. */
export function NewLeadDialog({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated?: () => void }) {
  const [f, setF] = useState({ name: "", phone: "", email: "", company_name: "", customer_type: "", intake_source: "", notes: "" });
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => setF((v) => ({ ...v, [k]: e.target.value }));
  const options = useCrmOptions();
  const { data: sources } = useFrappeGetCall<{ message: { name: string; source_name: string }[] }>("frappe.client.get_list", { doctype: "Excom Intake Source", fields: JSON.stringify(["name", "source_name"]), filters: JSON.stringify({ enabled: 1 }), limit_page_length: 100 }, "intake-sources-list", { revalidateOnFocus: false });
  const { call, loading } = useFrappePostCall("excom.excom.api.crm.create_lead_manual");
  const { openRecord } = useInbox();
  const submit = async () => {
    try {
      const r = await call(f);
      toast.success(r.message.created ? "Lead created" : "Lead already existed — opened it");
      setF({ name: "", phone: "", email: "", company_name: "", customer_type: "", intake_source: "", notes: "" });
      onClose(); onCreated?.(); openRecord(r.message.identity);
    } catch (e) { toast.error((e as { message?: string })?.message || "Could not create the lead"); }
  };
  return (
    <Modal open={open} onOpenChange={(v) => !v && onClose()} title="New lead" width="max-w-lg"
      footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" disabled={loading || !(f.name || f.phone || f.email)} onClick={submit}><UserPlus2 />Create lead</Button></>}>
      <div className="p-3 grid grid-cols-1 tablet:grid-cols-2 gap-3">
        <Field label="Name" required><Input value={f.name} onChange={set("name")} autoFocus /></Field>
        <Field label="Phone / WhatsApp" hint="10 digits get +91"><Input value={f.phone} onChange={set("phone")} placeholder="98765 43210" /></Field>
        <Field label="Email"><Input type="email" value={f.email} onChange={set("email")} /></Field>
        <Field label="Company"><Input value={f.company_name} onChange={set("company_name")} /></Field>
        <Field label="Customer type"><Select value={f.customer_type} onChange={set("customer_type")}><option value="">Not yet</option>{(options?.customer_types || []).map((t: string) => <option key={t}>{t}</option>)}</Select></Field>
        <Field label="Source" hint="Which team sees it follows the source"><Select value={f.intake_source} onChange={set("intake_source")}><option value="">Manual (no source)</option>{(sources?.message || []).map((s) => <option key={s.name} value={s.name}>{s.source_name}</option>)}</Select></Field>
        <div className="tablet:col-span-2"><Field label="What do they want?"><Textarea rows={2} value={f.notes} onChange={set("notes")} /></Field></div>
      </div>
    </Modal>
  );
}
