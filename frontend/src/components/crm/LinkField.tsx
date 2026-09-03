import { useEffect, useRef, useState } from "react";
import { useFrappeGetCall } from "frappe-react-sdk";
import { ExternalLink, ChevronDown, X } from "lucide-react";
import { Input } from "../primitives";
import { deskUrl } from "../../hooks/useRecordLinks";
import { cn } from "../ui/utils";

/**
 * Link field picker — searches the target doctype through frappe.desk.search.search_link (same as Desk),
 * so a Link field can only hold a value that exists (no more "Could not find Salutation: m").
 */
export function LinkField({ doctype, value, onChange, disabled, placeholder }: { doctype: string; value: string; onChange: (v: string) => void; disabled?: boolean; placeholder?: string }) {
  const [text, setText] = useState(value);
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { setText(value); }, [value]);
  useEffect(() => { const t = setTimeout(() => setQ(text), 200); return () => clearTimeout(t); }, [text]);
  useEffect(() => {
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) { setOpen(false); setText(value); } };
    document.addEventListener("mousedown", h); return () => document.removeEventListener("mousedown", h);
  }, [value]);
  const { data, isLoading } = useFrappeGetCall<{ message: { value: string; description?: string; label?: string }[] }>(
    open && doctype ? "frappe.desk.search.search_link" : (null as unknown as string),
    open && doctype ? { doctype, txt: q, page_length: 20 } : undefined,
    undefined,
    { revalidateOnFocus: false, keepPreviousData: true }
  );
  const results = data?.message ?? [];
  const pick = (v: string) => { onChange(v); setText(v); setOpen(false); };
  return (
    <div ref={ref} className="relative min-w-0">
      <div className="flex items-center gap-1 min-w-0">
        <div className="relative flex-1 min-w-0">
          <Input value={text} disabled={disabled} placeholder={placeholder || doctype} onFocus={() => !disabled && setOpen(true)} onChange={(e) => { setText(e.target.value); setOpen(true); }}
            onKeyDown={(e) => { if (e.key === "Enter" && results[0]) { e.preventDefault(); pick(results[0].value); } if (e.key === "Escape") { setOpen(false); setText(value); } }} className="pr-7" />
          {value && !disabled ? <button type="button" aria-label="Clear" onClick={() => pick("")} className="absolute right-1.5 top-1/2 -translate-y-1/2 text-ink-3 hover:text-ink-1"><X className="size-3.5" /></button> : <ChevronDown className="absolute right-2 top-1/2 -translate-y-1/2 size-4 text-ink-muted pointer-events-none" />}
        </div>
        {value && <a href={deskUrl(doctype, value)} target="_blank" rel="noreferrer" className="text-ink-3 hover:text-ink-1 shrink-0" title={`Open ${doctype} in Desk`}><ExternalLink className="size-4" /></a>}
      </div>
      {open && !disabled && (
        <div className="absolute z-30 left-0 right-0 mt-1 max-h-56 overflow-y-auto rounded-md border border-border bg-surface shadow-ex">
          {isLoading && !results.length ? <p className="px-2 py-2 text-xs text-ink-3">Searching…</p> : results.length === 0 ? <p className="px-2 py-2 text-xs text-ink-3">No {doctype} matches "{text}"</p> : results.map((r) => (
            <button key={r.value} type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => pick(r.value)} className={cn("w-full text-left px-2 h-8 text-sm hover:bg-surface-hover flex items-center gap-2 min-w-0", r.value === value && "bg-surface-active")}>
              <span className="truncate">{r.value}</span>{r.description && <span className="text-xs text-ink-3 truncate">{r.description}</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
