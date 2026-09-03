import { useEffect, useMemo, useRef, useState } from "react";
import { Search, Plus, Save, X, Calendar, Tag, Users, Radio, MessageCircle, AtSign, Building2 } from "lucide-react";
import { Chip, Button, Menu, menuItemClass, Input } from "../primitives";
import { useInbox } from "../shell/InboxProvider";
import { useInboxMeta, accountLabel } from "../../hooks/useInboxMeta";
import { useTags } from "../../hooks/useTags";
import { activeChips, parseSearchInput, removeChip, type InboxFilters } from "../../lib/views";
import { CHANNEL_ORDER, channelMeta } from "../../lib/channels";
import { cn } from "../ui/utils";

const todayISO = () => new Date().toISOString().slice(0, 10);
const daysAgoISO = (n: number) => { const d = new Date(); d.setDate(d.getDate() - n); return d.toISOString().slice(0, 10); };

/**
 * One search field, always visible (UX-001 §3.4). Typing `channel:whatsapp after:2026-08-01 text`
 * writes chips; `+ Filter` writes the chip for you. Chip row scrolls, never wraps.
 */
export function SearchBar() {
  const { filters, setFilters, saveView, viewId, views, deleteView } = useInbox();
  const [text, setText] = useState(filters.q);
  const [saving, setSaving] = useState(false);
  const [viewName, setViewName] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const meta = useInboxMeta();
  const { tags } = useTags();

  useEffect(() => { setText(filters.q); }, [filters.q]);

  // Debounced free-text; chips apply on Enter/blur/space-after-chip.
  useEffect(() => {
    const parsed = parseSearchInput(text, filters);
    if (parsed.q === filters.q) return;
    const t = window.setTimeout(() => setFilters(parsed), 300);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text]);

  const commit = () => {
    const parsed = parseSearchInput(text, filters);
    setFilters(parsed);
    setText(parsed.q);
  };

  const chips = useMemo(() => activeChips(filters), [filters]);
  const set = (patch: Partial<InboxFilters>) => setFilters({ ...filters, ...patch });
  const view = views.find((v) => v.id === viewId);
  const canSave = chips.length > 0 || Boolean(filters.q);

  return (
    <div className="shrink-0 border-b border-border bg-surface-sunken px-2 pt-2 pb-1.5 min-w-0">
      <div className="flex items-center gap-1.5 min-w-0">
        <div className="relative flex-1 min-w-0">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-ink-muted pointer-events-none" />
          <Input
            ref={inputRef}
            data-search-input
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") commit(); if (e.key === "Escape") { setText(""); set({ q: "" }); (e.target as HTMLInputElement).blur(); } }}
            onBlur={commit}
            placeholder="Search  ·  try channel:whatsapp after:2026-08-01"
            className="pl-8 pr-7 bg-surface"
            aria-label="Search conversations"
          />
          {text && (
            <button type="button" aria-label="Clear" onClick={() => { setText(""); set({ q: "" }); }} className="absolute right-1.5 top-1/2 -translate-y-1/2 p-1 rounded text-ink-3 hover:text-ink-1">
              <X className="size-3.5" />
            </button>
          )}
        </div>

        {/* + Filter */}
        <Menu.Root modal={false}>
          <Menu.Trigger asChild>
            <Button variant="ghost" size="icon" aria-label="Add filter" title="Add filter"><Plus /></Button>
          </Menu.Trigger>
          <Menu.Portal>
            <Menu.Content align="end" sideOffset={4} className="z-50 min-w-[200px] rounded-lg border border-border bg-surface p-1 shadow-ex">
              <Sub label="Channel" icon={<MessageCircle />}>
                {CHANNEL_ORDER.map((c) => { const m = channelMeta(c); return <Menu.Item key={c} className={menuItemClass} onSelect={() => set({ channel: c, account: "" })}><m.icon />{m.label}</Menu.Item>; })}
              </Sub>
              {meta.accounts.length > 0 && (
                <Sub label="Account" icon={<AtSign />}>
                  {meta.accounts.map((a) => <Menu.Item key={a.name} className={menuItemClass} onSelect={() => set({ account: a.name, channel: a.channel.toLowerCase() })}><span className="truncate">{accountLabel(a)}</span><span className="text-xs text-ink-3 ml-auto">{a.channel}</span></Menu.Item>)}
                </Sub>
              )}
              {meta.teams.length > 0 && (
                <Sub label="Team" icon={<Users />}>
                  <Menu.Item className={menuItemClass} onSelect={() => set({ team: "__general__" })}>Unassigned (General)</Menu.Item>
                  {meta.teams.filter((t) => t.name !== "General").map((t) => <Menu.Item key={t.name} className={menuItemClass} onSelect={() => set({ team: t.name })}>{t.team_name}</Menu.Item>)}
                </Sub>
              )}
              {tags.length > 0 && (
                <Sub label="Tag" icon={<Tag />}>
                  {tags.map((t) => <Menu.Item key={t.name} className={menuItemClass} onSelect={() => set({ tags: filters.tags.includes(t.name) ? filters.tags : [...filters.tags, t.name] })}><span className="size-2 rounded-full shrink-0" style={{ backgroundColor: t.color }} />{t.tag_name}</Menu.Item>)}
                </Sub>
              )}
              <Sub label="Date" icon={<Calendar />}>
                <Menu.Item className={menuItemClass} onSelect={() => set({ from: todayISO(), to: todayISO() })}>Today</Menu.Item>
                <Menu.Item className={menuItemClass} onSelect={() => set({ from: daysAgoISO(1), to: daysAgoISO(1) })}>Yesterday</Menu.Item>
                <Menu.Item className={menuItemClass} onSelect={() => set({ from: daysAgoISO(7), to: todayISO() })}>Last 7 days</Menu.Item>
                <Menu.Item className={menuItemClass} onSelect={() => set({ from: daysAgoISO(30), to: todayISO() })}>Last 30 days</Menu.Item>
                <Menu.Item className={menuItemClass} onSelect={() => { const d = new Date(); d.setDate(1); set({ from: d.toISOString().slice(0, 10), to: todayISO() }); }}>This month</Menu.Item>
                <Menu.Item className={menuItemClass} onSelect={() => { inputRef.current?.focus(); setText((t) => `${t} after:${daysAgoISO(7)} before:${todayISO()}`.trim()); }}>Custom range…</Menu.Item>
              </Sub>
              {meta.broadcasts.length > 0 && (
                <Sub label="Broadcast" icon={<Radio />}>
                  {meta.broadcasts.map((b) => <Menu.Item key={b.name} className={menuItemClass} onSelect={() => set({ broadcast: b.name, bstatus: "" })}><span className="truncate">{b.broadcast_name}</span><span className="text-xs text-ink-3 ml-auto tabular-nums">{b.sent_count}/{b.total_recipients}</span></Menu.Item>)}
                </Sub>
              )}
              {filters.broadcast && (
                <Sub label="Delivery status" icon={<Radio />}>
                  {["Sent", "Failed", "Queued", "Skipped"].map((s) => <Menu.Item key={s} className={menuItemClass} onSelect={() => set({ bstatus: s })}>{s}</Menu.Item>)}
                </Sub>
              )}
              <Sub label="Kind" icon={<Users />}>
                {[["customer", "Customers"], ["supplier", "Suppliers"], ["lead", "Leads & opportunities"], ["employee", "Employees"], ["none", "No ERP record"]].map(([v, l]) => <Menu.Item key={v} className={menuItemClass} onSelect={() => set({ kind: v })}>{l}</Menu.Item>)}
              </Sub>
              <Sub label="Company" icon={<Building2 />}>
                <Menu.Item className={menuItemClass} onSelect={() => { inputRef.current?.focus(); setText((t) => `${t} company:`.trim()); }}>Type company:…</Menu.Item>
              </Sub>
            </Menu.Content>
          </Menu.Portal>
        </Menu.Root>

        {canSave && (
          <Button variant="ghost" size="icon" aria-label="Save as view" title="Save as view" onClick={() => setSaving(true)}><Save /></Button>
        )}
      </div>

      {(chips.length > 0 || saving || (view && !view.builtin)) && (
        <div className="chip-row mt-1.5 h-7">
          {chips.map((c) => (
            <Chip key={`${c.key}:${c.value}`} size="sm" accent="blue" label={c.label} onRemove={() => setFilters(removeChip(filters, c.key, c.value))} />
          ))}
          {chips.length > 1 && (
            <button type="button" className="text-xs text-ink-3 hover:text-ink-1 px-1" onClick={() => setFilters({ ...filters, channel: "", account: "", team: "", tags: [], broadcast: "", bstatus: "", from: "", to: "", company: "", archived: "", kind: "" })}>Clear</button>
          )}
          {view && !view.builtin && (
            <button type="button" className="text-xs text-crayon-rose-text hover:underline px-1" onClick={() => deleteView(view.id)}>Delete view</button>
          )}
        </div>
      )}

      {saving && (
        <form
          className="flex items-center gap-1.5 mt-1.5"
          onSubmit={(e) => { e.preventDefault(); if (viewName.trim()) { saveView(viewName.trim()); setViewName(""); setSaving(false); } }}
        >
          <Input autoFocus value={viewName} onChange={(e) => setViewName(e.target.value)} placeholder="View name" className="h-7 text-xs" />
          <Button size="sm" variant="primary" type="submit" disabled={!viewName.trim()}>Save</Button>
          <Button size="sm" variant="ghost" onClick={() => setSaving(false)}>Cancel</Button>
        </form>
      )}
    </div>
  );
}

function Sub({ label, icon, children }: { label: string; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <Menu.Sub>
      <Menu.SubTrigger className={cn(menuItemClass, "data-[state=open]:bg-surface-hover")}>{icon}<span className="flex-1">{label}</span><span className="text-ink-3">›</span></Menu.SubTrigger>
      <Menu.Portal>
        <Menu.SubContent sideOffset={4} alignOffset={-4} className="z-50 min-w-[180px] max-h-[60vh] overflow-y-auto rounded-lg border border-border bg-surface p-1 shadow-ex">{children}</Menu.SubContent>
      </Menu.Portal>
    </Menu.Sub>
  );
}
