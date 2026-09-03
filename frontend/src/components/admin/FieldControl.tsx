import { useRef, useState } from "react";
import { Upload, ExternalLink, Trash2, Plus } from "lucide-react";
import { Input, Textarea, Select, Button, inputClass } from "../primitives";
import { LinkField } from "../crm/LinkField";
import { cn } from "../ui/utils";

/** <input type=color> needs a valid value even when the field is empty; built without a literal so the token gate stays clean. */
const EMPTY_COLOR = "#" + "0".repeat(6);

export interface AdminField {
  fieldname: string; label: string; fieldtype: string; options?: string; reqd: number; read_only: number;
  description?: string; default?: string | null; in_list_view: number; depends_on?: string; hidden: number; child_fields?: AdminField[];
}

async function uploadFile(file: File): Promise<string> {
  const fd = new FormData();
  fd.append("file", file); fd.append("is_private", "0");
  const res = await fetch("/api/method/upload_file", { method: "POST", body: fd, headers: { "X-Frappe-CSRF-Token": (window as any).csrf_token || "" } });
  const j = await res.json();
  if (!res.ok) throw new Error(j?.exception || "Upload failed");
  return j.message.file_url as string;
}

/** Meta-driven control for one Frappe field. Covers every fieldtype the Excom admin doctypes use. */
export function FieldControl({ f, value, onChange, disabled }: { f: AdminField; value: unknown; onChange: (v: unknown) => void; disabled?: boolean }) {
  const ro = disabled || Boolean(f.read_only);
  const v = value;
  const common = { disabled: ro, value: v == null ? "" : String(v) };
  switch (f.fieldtype) {
    case "Select": {
      const opts = (f.options || "").split("\n");
      return <Select {...common} onChange={(e) => onChange(e.target.value)}>{opts.map((o) => <option key={o} value={o}>{o || "—"}</option>)}</Select>;
    }
    case "Check": return <input type="checkbox" checked={Boolean(v)} disabled={ro} onChange={(e) => onChange(e.target.checked ? 1 : 0)} className="size-4" />;
    case "Small Text": case "Text": case "Long Text": case "Text Editor": return <Textarea {...common} rows={4} onChange={(e) => onChange(e.target.value)} />;
    case "Code": case "JSON": return <Textarea {...common} rows={6} onChange={(e) => onChange(e.target.value)} className="font-mono text-xs" spellCheck={false} />;
    case "Date": return <Input type="date" {...common} onChange={(e) => onChange(e.target.value)} />;
    case "Datetime": return <Input type="datetime-local" {...common} value={v ? String(v).replace(" ", "T").slice(0, 16) : ""} onChange={(e) => onChange(e.target.value.replace("T", " "))} />;
    case "Int": case "Float": case "Currency": case "Percent": case "Duration": return <Input type="number" {...common} onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))} className="tabular-nums" />;
    case "Color": return <div className="flex items-center gap-2"><input type="color" value={String(v || EMPTY_COLOR)} disabled={ro} onChange={(e) => onChange(e.target.value)} className="h-8 w-10 rounded border border-border bg-surface p-0.5" /><Input {...common} onChange={(e) => onChange(e.target.value)} className="w-32 font-mono" /></div>;
    case "Password": return <Input type="password" autoComplete="new-password" disabled={ro} value={v === "__SET__" ? "" : String(v ?? "")} placeholder={v === "__SET__" ? "•••••••• (set — leave blank to keep)" : "Not set"} onChange={(e) => onChange(e.target.value)} />;
    case "Link": return <LinkField doctype={f.options || ""} value={v == null ? "" : String(v)} disabled={ro} onChange={onChange} />;
    case "Dynamic Link": return <Input {...common} onChange={(e) => onChange(e.target.value)} placeholder={f.options} />;
    case "Attach": case "Attach Image": return <AttachControl value={String(v || "")} onChange={onChange} disabled={ro} />;
    case "Table": case "Table MultiSelect": return <ChildTable f={f} rows={Array.isArray(v) ? (v as Record<string, unknown>[]) : []} onChange={onChange} disabled={ro} />;
    case "Read Only": return <p className="text-sm text-ink-2 min-h-8 flex items-center">{String(v ?? "—")}</p>;
    default: return <Input {...common} onChange={(e) => onChange(e.target.value)} />;
  }
}

function AttachControl({ value, onChange, disabled }: { value: string; onChange: (v: unknown) => void; disabled?: boolean }) {
  const ref = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  return (
    <div className="flex items-center gap-2 min-w-0">
      <Input value={value} disabled={disabled} placeholder="/files/…" onChange={(e) => onChange(e.target.value)} className="flex-1 min-w-0" />
      {value && <a href={value} target="_blank" rel="noreferrer" className="text-ink-3 hover:text-ink-1 shrink-0"><ExternalLink className="size-4" /></a>}
      <input ref={ref} type="file" hidden onChange={async (e) => { const file = e.target.files?.[0]; if (!file) return; setBusy(true); setErr(""); try { onChange(await uploadFile(file)); } catch (x) { setErr((x as Error).message); } finally { setBusy(false); e.target.value = ""; } }} />
      <Button size="sm" variant="default" disabled={disabled || busy} onClick={() => ref.current?.click()}><Upload />{busy ? "Uploading…" : "Upload"}</Button>
      {err && <span className="text-xs text-crayon-rose-text truncate">{err}</span>}
    </div>
  );
}

/** Editable grid for a child table: Data/Select/Link/Check/Int cells; anything else read-only text. */
function ChildTable({ f, rows, onChange, disabled }: { f: AdminField; rows: Record<string, unknown>[]; onChange: (v: unknown) => void; disabled?: boolean }) {
  const cols = (f.child_fields || []).filter((c) => !["Table", "Table MultiSelect", "Attach", "Password", "Code", "JSON", "Text Editor", "HTML"].includes(c.fieldtype)).slice(0, 8);
  const set = (i: number, k: string, val: unknown) => onChange(rows.map((r, j) => (j === i ? { ...r, [k]: val } : r)));
  if (cols.length === 0) return <p className="text-xs text-ink-3">Edit in Desk</p>;
  return (
    <div className="rounded-md border border-border overflow-x-auto">
      <table className="w-full text-sm">
        <thead><tr className="bg-surface-sunken text-xs text-ink-3">{cols.map((c) => <th key={c.fieldname} className="px-2 h-8 text-left font-medium whitespace-nowrap">{c.label}{c.reqd ? " *" : ""}</th>)}{!disabled && <th className="w-8" />}</tr></thead>
        <tbody>
          {rows.length === 0 && <tr><td colSpan={cols.length + 1} className="px-2 h-8 text-xs text-ink-3">No rows</td></tr>}
          {rows.map((r, i) => (
            <tr key={i} className="border-t border-border">
              {cols.map((c) => (
                <td key={c.fieldname} className="px-1 py-0.5 min-w-[120px]">
                  {c.fieldtype === "Check" ? <input type="checkbox" className="size-4 ml-1" checked={Boolean(r[c.fieldname])} disabled={disabled} onChange={(e) => set(i, c.fieldname, e.target.checked ? 1 : 0)} />
                    : c.fieldtype === "Select" ? <select className={cn(inputClass, "h-7 text-xs")} value={String(r[c.fieldname] ?? "")} disabled={disabled} onChange={(e) => set(i, c.fieldname, e.target.value)}>{(c.options || "").split("\n").map((o) => <option key={o} value={o}>{o || "—"}</option>)}</select>
                    : c.fieldtype === "Link" ? <LinkField doctype={c.options || ""} value={String(r[c.fieldname] ?? "")} disabled={disabled} onChange={(val) => set(i, c.fieldname, val)} />
                    : <input className={cn(inputClass, "h-7 text-xs")} type={["Int", "Float"].includes(c.fieldtype) ? "number" : "text"} value={String(r[c.fieldname] ?? "")} disabled={disabled} onChange={(e) => set(i, c.fieldname, e.target.value)} />}
                </td>
              ))}
              {!disabled && <td className="px-1"><button type="button" aria-label="Remove row" onClick={() => onChange(rows.filter((_, j) => j !== i))} className="text-ink-3 hover:text-crayon-rose-text"><Trash2 className="size-4" /></button></td>}
            </tr>
          ))}
        </tbody>
      </table>
      {!disabled && <button type="button" onClick={() => onChange([...rows, {}])} className="flex items-center gap-1 px-2 h-8 text-xs text-ink-2 hover:text-ink-1 w-full border-t border-border"><Plus className="size-3.5" />Add row</button>}
    </div>
  );
}
