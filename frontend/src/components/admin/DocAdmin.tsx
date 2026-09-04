import { useEffect, useMemo, useState } from "react";
import { useFrappePostCall } from "frappe-react-sdk";
import { useFrappeGetCall } from "@/lib/api";
import { Plus, Search, Trash2, Save, ExternalLink, Loader2, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { Button, Input, Field, Sheet, EmptyState, Chip } from "../primitives";
import { DataTable } from "../shell/AdminPage";
import { FieldControl, type AdminField } from "./FieldControl";
import { deskUrl } from "../../hooks/useRecordLinks";
import { serverMessage } from "./util";
import { EmbedPanel } from "./EmbedPanel";

interface Schema { doctype: string; title_field: string; single: boolean; read_only: boolean; needs_name?: boolean; sections: { label: string; fields: AdminField[]; depends_on?: string; collapsible?: number }[]; list_fields: string[]; list_labels: Record<string, string>; list_types: Record<string, string> }
type Doc = Record<string, unknown>;

/** Depends-on evaluation for the simple `eval:doc.x == 'y'` / `fieldname` forms Excom uses. */
function visible(f: AdminField, doc: Doc): boolean {
  const d = f.depends_on; if (!d) return true;
  if (d.startsWith("eval:")) { try { return Boolean(new Function("doc", `return (${d.slice(5)})`)(doc)); } catch { return true; } }
  return Boolean(doc[d]);
}

export function useSchema(doctype: string) {
  return useFrappeGetCall<{ message: Schema }>("excom.excom.api.admin.get_schema", { doctype }, `admin-schema-${doctype}`, { revalidateOnFocus: false });
}

/** Form for one doc of an admin doctype (drawer body). Also used stand-alone for the Single "Excom Settings". */
export function DocForm({ doctype, name, schema, onSaved, onDeleted, extraActions }: { doctype: string; name: string; schema: Schema; onSaved?: (name: string) => void; onDeleted?: () => void; extraActions?: React.ReactNode }) {
  const isNew = !name && !schema.single;
  const { data, isLoading, mutate } = useFrappeGetCall<{ message: Doc }>(isNew ? null : "excom.excom.api.admin.get_doc", isNew ? undefined : { doctype, name }, isNew ? undefined : `admin-doc-${doctype}-${name}`, { revalidateOnFocus: false });
  const [draft, setDraft] = useState<Doc>({});
  const { call: save, loading: saving } = useFrappePostCall("excom.excom.api.admin.save_doc");
  const { call: del, loading: deleting } = useFrappePostCall("excom.excom.api.admin.delete_doc");
  useEffect(() => { setDraft({}); }, [name, doctype]);
  const base: Doc = useMemo(() => {
    if (!isNew) return data?.message ?? {};
    const d: Doc = {};
    for (const s of schema.sections) for (const f of s.fields) if (f.default != null && f.default !== "") d[f.fieldname] = f.fieldtype === "Check" ? (f.default === "1" || f.default === "true" ? 1 : 0) : f.default;
    return d;
  }, [data, isNew, schema]);
  const doc: Doc = { ...base, ...draft };
  const dirty = Object.keys(draft).length > 0 && !(isNew && schema.needs_name && !String(draft.__newname ?? "").trim());
  const SENSITIVE = ["Password"];
  const onSave = async () => {
    const touchedSecrets = schema.sections.flatMap((s) => s.fields).filter((f) => SENSITIVE.includes(f.fieldtype) && f.fieldname in draft && draft[f.fieldname]);
    const touchedKeys = schema.sections.flatMap((s) => s.fields).filter((f) => /token|secret|key|phone_id|business_id|app_id/i.test(f.fieldname) && !SENSITIVE.includes(f.fieldtype) && f.fieldname in draft);
    if (touchedSecrets.length || touchedKeys.length) {
      const list = [...touchedSecrets, ...touchedKeys].map((f) => f.label).join(", ");
      if (!window.confirm(`You are changing credentials or integration ids (${list}). A wrong value stops messages or webhooks for this account. Save anyway?`)) return;
    }
    try {
      const r = await save({ doctype, name: name || "", values: JSON.stringify(draft) });
      toast.success(isNew ? "Created" : "Saved"); setDraft({}); mutate(); onSaved?.(r.message.name);
    } catch (e) { toast.error(serverMessage(e)); }
  };
  const onDelete = async () => {
    if (!window.confirm(`Delete ${doctype} "${name}"? This cannot be undone.`)) return;
    try { await del({ doctype, name }); toast.success("Deleted"); onDeleted?.(); } catch (e) { toast.error(serverMessage(e)); }
  };
  if (isLoading && !data) return <div className="flex justify-center py-8 text-ink-3"><Loader2 className="size-5 animate-spin" /></div>;
  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="shrink-0 flex items-center gap-2 px-3 py-2 border-b border-border bg-surface-sunken min-w-0">
        <span className="text-xs text-ink-3 truncate flex-1 min-w-0">{schema.single ? doctype : isNew ? `New ${doctype}` : name}</span>
        {!isNew && !schema.single && <a href={deskUrl(doctype, name)} target="_blank" rel="noreferrer" className="text-ink-3 hover:text-ink-1" title="Open in Desk"><ExternalLink className="size-4" /></a>}
        {extraActions}
        {!schema.read_only && !isNew && !schema.single && <Button size="sm" variant="ghost" onClick={onDelete} disabled={deleting} className="text-crayon-rose-text"><Trash2 />Delete</Button>}
        {!schema.read_only && <Button size="sm" variant="primary" disabled={!dirty || saving} onClick={onSave}><Save />{saving ? "Saving…" : "Save"}</Button>}
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto p-3 space-y-5">
        {isNew && schema.needs_name && (
          <Field label="Name" required hint="This doctype is named by you; pick something readable."><Input value={String(draft.__newname ?? "")} onChange={(e) => setDraft((d) => ({ ...d, __newname: e.target.value }))} autoFocus /></Field>
        )}
        {schema.sections.map((s, i) => {
          if (s.depends_on && !visible({ depends_on: s.depends_on } as AdminField, doc)) return null;
          const fields = s.fields.filter((f) => visible(f, doc));
          if (!fields.length) return null;
          return (
            <section key={i} className="min-w-0">
              {s.label && <h4 className="text-xs font-medium text-ink-3 mb-2 uppercase tracking-wide">{s.label}</h4>}
              <div className="grid grid-cols-1 laptop:grid-cols-2 gap-x-4 gap-y-3">
                {fields.map((f) => (
                  <div key={f.fieldname} className={["Table", "Table MultiSelect", "Code", "JSON", "Text Editor", "Small Text", "Text", "Long Text", "HTML"].includes(f.fieldtype) ? "laptop:col-span-2 min-w-0" : "min-w-0"}>
                    <Field label={f.label} required={Boolean(f.reqd)} hint={f.description}>
                      <FieldControl f={f} value={doc[f.fieldname]} disabled={schema.read_only} onChange={(v) => setDraft((d) => ({ ...d, [f.fieldname]: v }))} />
                    </Field>
                  </div>
                ))}
              </div>
            </section>
          );
        })}
        <EmbedPanel doctype={doctype} name={name} channel={String(doc.channel ?? "")} sourceType={String(doc.source_type ?? "")} />
      </div>
    </div>
  );
}

function cell(type: string, v: unknown): React.ReactNode {
  if (v == null || v === "") return <span className="text-ink-muted">—</span>;
  if (type === "Check") return v ? <Chip size="sm" accent="green" label="Yes" /> : <Chip size="sm" accent="neutral" label="No" />;
  if (type === "Datetime" || type === "Date") return <span className="tabular-nums text-ink-2">{String(v).slice(0, 16)}</span>;
  if (type === "Color") return <span className="inline-flex items-center gap-1.5"><span className="size-3 rounded-full border border-border" style={{ background: String(v) }} />{String(v)}</span>;
  return String(v);
}

/**
 * Generic list + drawer editor for one admin doctype. Search, New, edit, delete, and an
 * optional header action (e.g. "Sync from Meta" for templates).
 */
export function DocAdmin({ doctype, headerAction, hint }: { doctype: string; headerAction?: React.ReactNode; hint?: string }) {
  const { data: sch, isLoading: schLoading } = useSchema(doctype);
  const schema = sch?.message;
  const [q, setQ] = useState("");
  const { data, isLoading, mutate } = useFrappeGetCall<{ message: Doc[] }>("excom.excom.api.admin.list_docs", { doctype, q, limit: 300 }, `admin-list-${doctype}-${q}`, { revalidateOnFocus: false, keepPreviousData: true });
  const [open, setOpen] = useState<string | null>(null); // "" = new
  const rows = data?.message ?? [];
  if (schLoading || !schema) return <div className="flex justify-center py-8 text-ink-3"><Loader2 className="size-5 animate-spin" /></div>;
  if (schema.single) return <DocForm doctype={doctype} name="" schema={schema} />;
  const cols = [
    { key: "__title", label: schema.list_labels[schema.title_field] || "Name", primary: true, render: (r: Doc) => <span className="text-ink-1 font-medium">{String(r[schema.title_field] ?? r.name)}</span> },
    ...schema.list_fields.filter((f) => f !== schema.title_field).map((f) => ({ key: f, label: schema.list_labels[f], render: (r: Doc) => cell(schema.list_types[f], r[f]) })),
    { key: "modified", label: "Modified", render: (r: Doc) => <span className="text-xs text-ink-3 tabular-nums">{String(r.modified).slice(0, 16)}</span>, className: "w-36" },
  ];
  return (
    <div className="flex flex-col min-h-0 h-full">
      <div className="shrink-0 flex items-center gap-2 px-3 py-2 border-b border-border min-w-0 flex-wrap">
        <div className="relative flex-1 min-w-[160px] max-w-sm"><Search className="absolute left-2 top-1/2 -translate-y-1/2 size-4 text-ink-3" /><Input value={q} onChange={(e) => setQ(e.target.value)} placeholder={`Search ${doctype.replace(/^Excom /, "").toLowerCase()}…`} className="pl-8" /></div>
        <span className="text-xs text-ink-3 tabular-nums">{rows.length}</span>
        <Button size="sm" variant="ghost" onClick={() => mutate()} aria-label="Refresh"><RefreshCw /></Button>
        {headerAction}
        {!schema.read_only && <Button size="sm" variant="primary" onClick={() => setOpen("")}><Plus />New</Button>}
      </div>
      {hint && <p className="px-3 py-1.5 text-xs text-ink-3 border-b border-border bg-surface-sunken">{hint}</p>}
      <div className="flex-1 min-h-0 overflow-y-auto">
        {isLoading && !data ? <div className="flex justify-center py-8 text-ink-3"><Loader2 className="size-5 animate-spin" /></div>
          : <DataTable rows={rows} columns={cols} keyOf={(r) => String(r.name)} onRowClick={(r) => setOpen(String(r.name))} empty={<EmptyState title={`No ${doctype.replace(/^Excom /, "").toLowerCase()} yet`} hint={schema.read_only ? undefined : "Create the first one with New."} compact />} />}
      </div>
      <Sheet open={open !== null} onOpenChange={(o) => !o && setOpen(null)} title={open === "" ? `New ${doctype.replace(/^Excom /, "")}` : doctype.replace(/^Excom /, "")} width="w-[560px]" className="!p-0">
        {open !== null && <DocForm doctype={doctype} name={open} schema={schema} onSaved={(n) => { mutate(); setOpen(n); }} onDeleted={() => { mutate(); setOpen(null); }} />}
      </Sheet>
    </div>
  );
}
