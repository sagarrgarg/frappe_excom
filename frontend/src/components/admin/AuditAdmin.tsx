import { useFrappeGetCall } from "@/lib/api";
import { Loader2 } from "lucide-react";
import { EmptyState, Avatar } from "../primitives";
import { DataTable } from "../shell/AdminPage";
import { deskUrl } from "../../hooks/useRecordLinks";

interface Row { id: string; doctype: string; docname: string; by: string; user: string; at: string; changes: string[] }

/** Who changed what across every admin-managed record (Frappe Version log, secrets masked). */
export function AuditAdmin() {
  const { data, isLoading } = useFrappeGetCall<{ message: Row[] }>("excom.excom.api.admin.get_audit", { limit: 300 }, "admin-audit", { revalidateOnFocus: false });
  const rows = data?.message ?? [];
  const cols = [
    { key: "at", label: "When", render: (r: Row) => <span className="text-xs tabular-nums text-ink-2">{r.at.slice(0, 16)}</span>, className: "w-36" },
    { key: "by", label: "By", primary: true, render: (r: Row) => <span className="inline-flex items-center gap-1.5"><Avatar name={r.by} size={20} /><span className="text-ink-1">{r.by}</span></span> },
    { key: "doc", label: "Record", render: (r: Row) => <a href={deskUrl(r.doctype, r.docname)} target="_blank" rel="noreferrer" className="hover:underline"><span className="text-ink-3">{r.doctype.replace(/^Excom /, "")} · </span>{r.docname}</a> },
    { key: "changes", label: "Changes", render: (r: Row) => <span className="text-xs text-ink-2 whitespace-normal">{r.changes.length ? r.changes.join(" · ") : "created / saved"}</span>, className: "!max-w-none !whitespace-normal" },
  ];
  return (
    <div className="flex flex-col min-h-0 h-full">
      <p className="px-3 py-1.5 text-xs text-ink-3 border-b border-border bg-surface-sunken">Every save through Admin or Desk on channel accounts, templates, teams, tags, canned responses, stickers, notifications, intake sources, settings and assignment rules. Passwords are masked.</p>
      <div className="flex-1 min-h-0 overflow-y-auto">
        {isLoading && !data ? <div className="flex justify-center py-8 text-ink-3"><Loader2 className="size-5 animate-spin" /></div>
          : <DataTable rows={rows} columns={cols} keyOf={(r) => r.id} empty={<EmptyState title="No changes recorded yet" compact />} />}
      </div>
    </div>
  );
}
