import { useEffect, useRef, useState } from "react";
import { useFrappeGetCall } from "@/lib/api";
import { X } from "lucide-react";
import { cn } from "../ui/utils";

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/** To / Cc / Bcc as chips. Type an address (Enter, comma or space commits) or pick a contact / colleague from the suggestions. */
export function RecipientChips({ label, value, onChange, placeholder }: { label: string; value: string; onChange: (csv: string) => void; placeholder?: string }) {
  const chips = value.split(",").map((s) => s.trim()).filter(Boolean);
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const { data } = useFrappeGetCall<{ message: { email: string; name: string; kind: string }[] }>(open && text.length >= 2 ? "excom.excom.api.email.suggest_recipients" : null, { q: text }, undefined, { keepPreviousData: true, revalidateOnFocus: false });
  const suggestions = (data?.message ?? []).filter((s) => !chips.includes(s.email));
  const commit = (raw: string) => { const e = raw.trim().replace(/,$/, ""); if (!e) return; if (!EMAIL.test(e)) return; if (!chips.includes(e)) onChange([...chips, e].join(", ")); setText(""); };
  useEffect(() => { const h = (ev: MouseEvent) => { if (box.current && !box.current.contains(ev.target as Node)) setOpen(false); }; document.addEventListener("mousedown", h); return () => document.removeEventListener("mousedown", h); }, []);
  return (
    <div ref={box} className="relative flex items-center gap-1.5 px-2 min-h-8 min-w-0 flex-wrap">
      <span className="text-xs text-ink-3 w-12 shrink-0">{label}</span>
      {chips.map((c) => <span key={c} className="inline-flex items-center gap-1 rounded-full bg-surface-sunken px-2 h-6 text-xs text-ink-1 max-w-[220px]"><span className="truncate">{c}</span><button type="button" aria-label={`Remove ${c}`} onClick={() => onChange(chips.filter((x) => x !== c).join(", "))} className="text-ink-3 hover:text-ink-1"><X className="size-3" /></button></span>)}
      <input value={text} onChange={(e) => { setText(e.target.value); setOpen(true); }} onFocus={() => setOpen(true)} onBlur={() => commit(text)} placeholder={chips.length ? "" : placeholder} className="flex-1 min-w-[120px] h-7 bg-transparent text-sm outline-none"
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === "," || (e.key === " " && EMAIL.test(text))) { e.preventDefault(); if (open && suggestions[0] && e.key === "Enter" && !EMAIL.test(text)) commit(suggestions[0].email); else commit(text); } if (e.key === "Backspace" && !text && chips.length) onChange(chips.slice(0, -1).join(", ")); if (e.key === "Escape") setOpen(false); }} />
      {open && suggestions.length > 0 && (
        <div className="absolute left-14 top-full z-30 mt-0.5 w-72 max-h-48 overflow-y-auto rounded-md border border-border bg-surface shadow-ex">
          {suggestions.map((s) => <button key={s.email} type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => { commit(s.email); setOpen(false); }} className={cn("w-full text-left px-2 h-8 text-sm hover:bg-surface-hover flex items-center gap-2 min-w-0")}><span className="truncate">{s.name}</span><span className="text-xs text-ink-3 truncate">{s.email}</span><span className="ml-auto text-2xs text-ink-muted shrink-0">{s.kind}</span></button>)}
        </div>
      )}
    </div>
  );
}
