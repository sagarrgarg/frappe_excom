import { useState } from "react";
import { Check, ChevronDown, Lock } from "lucide-react";
import { Menu, menuItemClass, Chip, Modal, Button, Field, Textarea } from "../primitives";
import { cn } from "../ui/utils";
import type { GateStatus } from "../../hooks/useCrm";

/**
 * Stage picker — the same control at every width (the kanban board on laptop is a view of this).
 * Blocked stages show the failing gate inline instead of a generic error (P3 §3.10).
 */
export function StagePicker({ gate, onSelect, onOverride, compact }: { gate: GateStatus | null; onSelect: (stage: string, note?: string) => Promise<unknown>; onOverride?: (gate: string, reason: string) => Promise<unknown>; compact?: boolean }) {
  const [confirm, setConfirm] = useState<{ stage: string; blocked: string[] } | null>(null);
  const [note, setNote] = useState("");
  if (!gate) return null;
  const idx = gate.stages.indexOf(gate.current || "");
  return (
    <>
      <Menu.Root modal={false}>
        <Menu.Trigger asChild>
          <button type="button" className={cn("inline-flex items-center gap-1 rounded-md font-medium text-crayon-blue-text bg-crayon-blue-tint hover:brightness-95 min-w-0 max-w-full", compact ? "h-6 px-2 text-xs" : "h-8 px-3 text-sm")}>
            <span className="truncate">{gate.current || "Set stage"}</span><ChevronDown className="size-3.5 shrink-0" />
          </button>
        </Menu.Trigger>
        <Menu.Portal>
          <Menu.Content align="start" sideOffset={4} className="z-50 min-w-[240px] rounded-lg border border-border bg-surface p-1 shadow-ex">
            {gate.stages.map((s, i) => {
              const blocked = gate.blocked[s] || [];
              const current = s === gate.current;
              return (
                <Menu.Item key={s} className={cn(menuItemClass, current && "bg-surface-active")} onSelect={() => (blocked.length ? setConfirm({ stage: s, blocked }) : setConfirm({ stage: s, blocked: [] }))}>
                  <span className={cn("size-5 rounded-full text-2xs flex items-center justify-center shrink-0", i <= idx ? "bg-crayon-blue-base text-white" : "bg-surface-sunken text-ink-3")}>{current ? <Check className="size-3" /> : i + 1}</span>
                  <span className="truncate flex-1">{s}</span>
                  {blocked.length > 0 && <span className="inline-flex items-center gap-1 text-xs text-crayon-amber-text"><Lock className="size-3" />{blocked.join(", ")}</span>}
                </Menu.Item>
              );
            })}
          </Menu.Content>
        </Menu.Portal>
      </Menu.Root>

      <Modal open={Boolean(confirm)} onOpenChange={(v) => !v && setConfirm(null)} title={confirm?.blocked.length ? `Blocked: ${confirm.stage}` : `Move to ${confirm?.stage}`}
        footer={<>
          <Button variant="ghost" onClick={() => setConfirm(null)}>Cancel</Button>
          {confirm?.blocked.length ? (
            onOverride && <Button variant="primary" disabled={!note.trim()} onClick={async () => { for (const g of confirm.blocked) await onOverride(g, note.trim()); await onSelect(confirm.stage, note.trim()); setConfirm(null); setNote(""); }}>Override & move</Button>
          ) : (
            <Button variant="primary" onClick={async () => { await onSelect(confirm!.stage, note.trim()); setConfirm(null); setNote(""); }}>Move</Button>
          )}
        </>}>
        {confirm?.blocked.length ? (
          <div className="space-y-3">
            <p className="text-sm text-ink-1">Gate not cleared: <b>{confirm.blocked.join(", ")}</b>. Managers can override with a reason; it is logged on the record.</p>
            <div className="chip-row h-6">{confirm.blocked.map((g) => <Chip key={g} size="sm" accent="amber" icon={<Lock />} label={g} />)}</div>
            <Field label="Reason" required><Textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} /></Field>
          </div>
        ) : (
          <Field label="Note (optional)"><Textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} placeholder="What happened in this stage?" /></Field>
        )}
      </Modal>
    </>
  );
}
