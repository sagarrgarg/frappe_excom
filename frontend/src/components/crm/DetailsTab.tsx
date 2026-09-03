import { useEffect, useMemo, useState } from "react";
import { Loader2, ExternalLink, Save } from "lucide-react";
import { useCrmRecord, useFieldSchema, useCrmActions, type CrmRef, type SchemaField } from "../../hooks/useCrm";
import { Button, Input, Select, Textarea, Field, EmptyState } from "../primitives";
import { deskUrl } from "../../hooks/useRecordLinks";
import { GateChips } from "./GateChips";
import { StagePicker } from "./StagePicker";

/**
 * Details tab (UX-001 U2 / P3 E2): rendered from get_field_schema — a Custom Field added in Desk shows here
 * on reload with no frontend change. Attribution/provenance/stage fields are read-only by contract.
 */
export function DetailsTab({ refr, onChanged }: { refr: CrmRef | null; onChanged?: () => void }) {
  const { record, refresh, isLoading } = useCrmRecord(refr);
  const { schema } = useFieldSchema(refr?.doctype || null, record?.customer_type || "");
  const actions = useCrmActions(() => { refresh(); onChanged?.(); });
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  useEffect(() => { setDraft({}); }, [refr?.name]);
  const dirty = Object.keys(draft).length > 0;

  const renderField = (f: SchemaField) => {
    const v = f.fieldname in draft ? draft[f.fieldname] : record?.[f.fieldname];
    const set = (val: unknown) => setDraft((d) => ({ ...d, [f.fieldname]: val }));
    const ro = f.read_only;
    const common = { disabled: ro, value: v == null ? "" : String(v) };
    let control: React.ReactNode;
    switch (f.fieldtype) {
      case "Select": {
        const opts = (f.options || "").split("\n");
        control = <Select {...common} onChange={(e) => set(e.target.value)}>{opts.map((o) => <option key={o} value={o}>{o || "—"}</option>)}</Select>;
        break;
      }
      case "Check": control = <input type="checkbox" checked={Boolean(v)} disabled={ro} onChange={(e) => set(e.target.checked ? 1 : 0)} className="size-4" />; break;
      case "Small Text": case "Text": case "Long Text": case "Text Editor": control = <Textarea {...common} rows={3} onChange={(e) => set(e.target.value)} />; break;
      case "Date": control = <Input type="date" {...common} onChange={(e) => set(e.target.value)} />; break;
      case "Datetime": control = <Input type="datetime-local" {...common} value={v ? String(v).replace(" ", "T").slice(0, 16) : ""} onChange={(e) => set(e.target.value.replace("T", " "))} />; break;
      case "Int": case "Float": case "Currency": case "Percent": control = <Input type="number" {...common} onChange={(e) => set(e.target.value === "" ? null : Number(e.target.value))} className="tabular-nums" />; break;
      case "Link": case "Dynamic Link":
        control = <div className="flex items-center gap-1 min-w-0"><Input {...common} onChange={(e) => set(e.target.value)} placeholder={f.options} />{v && f.options && f.fieldtype === "Link" && <a href={deskUrl(f.options, String(v))} target="_blank" rel="noreferrer" className="text-ink-3 hover:text-ink-1 shrink-0"><ExternalLink className="size-4" /></a>}</div>;
        break;
      case "Table": control = <p className="text-xs text-ink-3">{Array.isArray(v) ? `${v.length} row${v.length === 1 ? "" : "s"} — edit in Desk` : "—"}</p>; break;
      default: control = <Input {...common} onChange={(e) => set(e.target.value)} />;
    }
    return <Field key={f.fieldname} label={f.label} required={f.reqd} hint={f.description}>{control}</Field>;
  };

  const sections = useMemo(() => schema?.sections.filter((s) => s.fields.length) ?? [], [schema]);

  if (!refr) return <EmptyState title="No CRM record yet" hint="Promote this conversation to a Lead from the ⋯ menu." compact />;
  if (isLoading && !record) return <div className="flex justify-center py-8 text-ink-3"><Loader2 className="size-5 animate-spin" /></div>;
  if (!record) return <EmptyState title="Record unavailable" hint="You may not have permission to read it." compact />;

  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="shrink-0 px-3 py-2 border-b border-border flex items-center gap-2 min-w-0 bg-surface-sunken">
        <span className="text-xs text-ink-3 shrink-0">{refr.doctype}</span>
        <a href={deskUrl(refr.doctype, refr.name)} target="_blank" rel="noreferrer" className="text-sm text-ink-1 truncate hover:underline">{refr.name}</a>
        {record._gate_status && <div className="chip-row flex-1 h-6"><StagePicker compact gate={record._gate_status} onSelect={(s, n) => actions.setStage(refr.name, s, n)} onOverride={(g, r) => actions.overrideGate(refr.name, g, r)} /><GateChips flags={record._gate_status.flags} /></div>}
        {schema?.can_write && <Button size="sm" variant="primary" disabled={!dirty} onClick={async () => { await actions.updateRecord(refr, draft); setDraft({}); }}><Save />Save</Button>}
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto p-3 space-y-4">
        {sections.map((s, i) => (
          <section key={i} className="min-w-0">
            {s.label && <h4 className="text-xs text-ink-3 mb-2">{s.label}</h4>}
            <div className="grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr))]">{s.fields.map(renderField)}</div>
          </section>
        ))}
        {record._stage_log?.length > 0 && (
          <section><h4 className="text-xs text-ink-3 mb-2">Stage history</h4>
            <ul className="text-xs divide-y divide-border rounded-md border border-border">{record._stage_log.map((l: any, i: number) => <li key={i} className="flex items-center gap-2 px-2 h-8 min-w-0"><span className="truncate flex-1 text-ink-1">{l.from_stage || "—"} → {l.to_stage}</span><span className="text-ink-3 tabular-nums shrink-0">{l.to_date?.slice(0, 16)}</span>{l.duration ? <span className="text-ink-3 tabular-nums shrink-0">{Math.round(l.duration / 3600)}h</span> : null}</li>)}</ul>
          </section>
        )}
      </div>
    </div>
  );
}
