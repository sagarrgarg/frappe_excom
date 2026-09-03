import { useEffect, useMemo, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { useNavigate } from "react-router-dom";
import { Search, Inbox, Plus, Radio, BarChart3, Shield, GitMerge, ListChecks, Cog, Settings, Rows3, PanelRight, ArrowLeftRight, Bug, Archive, UserPlus, Eye, Sun, KanbanSquare, Users, MessageSquare, Bookmark } from "lucide-react";
import { useInbox } from "./shell/InboxProvider";
import { Avatar, Kbd } from "./primitives";
import { channelMeta } from "../lib/channels";
import { cn } from "./ui/utils";

interface Item {
  id: string;
  group: "Conversations" | "Views" | "Go to" | "Actions" | "Settings";
  label: string;
  hint?: string;
  icon?: React.ReactNode;
  keywords?: string;
  shortcut?: string;
  run: () => void;
}

/** Fuzzy-ish match: every query token must appear in order-insensitive fashion in label+keywords. */
function score(q: string, it: Item): number {
  if (!q) return 1;
  const hay = `${it.label} ${it.hint || ""} ${it.keywords || ""}`.toLowerCase();
  let s = 0;
  for (const tok of q.toLowerCase().split(/\s+/).filter(Boolean)) {
    const idx = hay.indexOf(tok);
    if (idx < 0) return 0;
    s += idx === 0 ? 3 : hay[idx - 1] === " " ? 2 : 1;
  }
  return s;
}

const RECENT_KEY = "excom_palette_recent";

/**
 * ⌘K palette (T4): threads, saved views, navigation, actions, settings. Recent-first, keyboard-only operable.
 */
export function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const navigate = useNavigate();
  const { allContacts, views, setView, openRecord, setNewOpen, density, setDensity, toggleDetails, selected, closeRecord } = useInbox();
  const [q, setQ] = useState("");
  const [cursor, setCursor] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);
  const [recent, setRecent] = useState<string[]>(() => { try { return JSON.parse(localStorage.getItem(RECENT_KEY) || "[]"); } catch { return []; } });

  useEffect(() => { if (open) { setQ(""); setCursor(0); } }, [open]);

  const items = useMemo<Item[]>(() => {
    const go = (to: string) => () => navigate(to);
    const list: Item[] = [
      ...allContacts.slice(0, 200).map<Item>((c) => ({
        id: `t:${c.id}`, group: "Conversations", label: c.contactName, hint: c.contactInfo.company || c.contactInfo.phone || c.contactInfo.email,
        keywords: `${c.contactInfo.phone} ${c.contactInfo.email} ${c.channels.join(" ")} ${c.tags?.map((t) => t.tag_name).join(" ") || ""}`,
        icon: <Avatar name={c.contactName} src={c.contactAvatar} size={20} />, run: () => openRecord(c.id),
      })),
      ...views.map<Item>((v) => ({ id: `v:${v.id}`, group: "Views", label: v.label, hint: v.hint, icon: <Bookmark />, keywords: "view filter", run: () => setView(v.id) })),
      { id: "new", group: "Actions", label: "New conversation", icon: <Plus />, shortcut: "⌘N", run: () => setNewOpen(true) },
      ...(selected ? [
        { id: "details", group: "Actions" as const, label: "Toggle details", icon: <PanelRight />, shortcut: "⌘.", run: toggleDetails },
        { id: "close", group: "Actions" as const, label: "Close conversation", icon: <Archive />, run: closeRecord },
      ] : []),
      { id: "g:inbox", group: "Go to", label: "Inbox", icon: <Inbox />, shortcut: "g i", run: go("/inbox") },
      { id: "g:today", group: "Go to", label: "Today's actions", icon: <Sun />, shortcut: "g t", run: go("/inbox/today") },
      { id: "g:unread", group: "Go to", label: "Unread", icon: <Eye />, run: go("/inbox/unread") },
      { id: "g:mine", group: "Go to", label: "Assigned to me", icon: <UserPlus />, run: go("/inbox/mine") },
      { id: "g:archived", group: "Go to", label: "Archived", icon: <Archive />, run: go("/inbox/archived") },
      { id: "g:pipeline", group: "Go to", label: "Pipeline", icon: <KanbanSquare />, shortcut: "g p", run: go("/pipeline") },
      { id: "g:intake", group: "Go to", label: "Intake queue", icon: <Inbox />, run: go("/intake") },
      { id: "g:contacts", group: "Go to", label: "Contacts", icon: <Users />, run: go("/contacts") },
      { id: "g:broadcasts", group: "Go to", label: "Broadcasts", icon: <Radio />, shortcut: "g b", run: go("/broadcasts") },
      { id: "g:analytics", group: "Go to", label: "Analytics", icon: <BarChart3 />, shortcut: "g a", run: go("/analytics") },
      { id: "g:teams", group: "Go to", label: "Teams", icon: <Shield />, run: go("/teams") },
      { id: "g:merge", group: "Go to", label: "Merge suggestions", icon: <GitMerge />, run: go("/merge") },
      { id: "g:subs", group: "Go to", label: "Subscribers", icon: <ListChecks />, run: go("/subscribers") },
      { id: "g:rules", group: "Go to", label: "Subscriber rules", icon: <Cog />, run: go("/rules") },
      { id: "g:stress", group: "Go to", label: "Stress harness (dev)", icon: <Bug />, keywords: "dev test", run: go("/dev/stress") },
      { id: "s:settings", group: "Settings", label: "Settings", icon: <Settings />, shortcut: "g s", run: go("/settings") },
      ...["general", "signatures", "notifications", "branding", "accounts", "shortcuts", "canned", "auto-reply", "appearance"].map<Item>((s) => ({ id: `s:${s}`, group: "Settings", label: `Settings › ${s.replace("-", " ")}`, icon: <Settings />, run: go(`/settings?section=${s}`) })),
      { id: "s:density", group: "Settings", label: `Density: switch to ${density === "compact" ? "Comfortable" : "Compact"}`, icon: <Rows3 />, run: () => setDensity(density === "compact" ? "comfortable" : "compact") },
    ];
    return list;
  }, [allContacts, views, navigate, openRecord, setView, setNewOpen, selected, toggleDetails, closeRecord, density, setDensity]);

  const results = useMemo(() => {
    const scored = items.map((it) => ({ it, s: score(q, it) })).filter((x) => x.s > 0);
    if (!q) {
      // Recent first, then conversations capped, then the rest.
      const order = (it: Item) => (recent.includes(it.id) ? 0 : it.group === "Conversations" ? 2 : 1);
      return scored.map((x) => x.it).sort((a, b) => order(a) - order(b) || (recent.indexOf(a.id) - recent.indexOf(b.id))).filter((it, i) => it.group !== "Conversations" || i < 40).slice(0, 40);
    }
    return scored.sort((a, b) => b.s - a.s).map((x) => x.it).slice(0, 40);
  }, [items, q, recent]);

  useEffect(() => { setCursor(0); }, [q]);
  useEffect(() => { listRef.current?.children[cursor]?.scrollIntoView?.({ block: "nearest" }); }, [cursor]);

  const run = (it: Item) => {
    const next = [it.id, ...recent.filter((r) => r !== it.id)].slice(0, 8);
    setRecent(next); try { localStorage.setItem(RECENT_KEY, JSON.stringify(next)); } catch { /* ignore */ }
    onOpenChange(false);
    it.run();
  };

  let lastGroup = "";
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-ink-1/30" />
        <Dialog.Content className="fixed z-50 left-1/2 top-[10vh] -translate-x-1/2 w-[calc(100vw-24px)] max-w-[560px] rounded-xl border border-border bg-surface shadow-ex outline-none flex flex-col max-h-[70vh]">
          <Dialog.Title className="sr-only">Command palette</Dialog.Title>
          <Dialog.Description className="sr-only">Search conversations, views, pages and actions</Dialog.Description>
          <div className="flex items-center gap-2 px-3 h-12 border-b border-border">
            <Search className="size-4 text-ink-muted shrink-0" />
            <input
              autoFocus
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "ArrowDown") { e.preventDefault(); setCursor((i) => Math.min(results.length - 1, i + 1)); }
                else if (e.key === "ArrowUp") { e.preventDefault(); setCursor((i) => Math.max(0, i - 1)); }
                else if (e.key === "Enter") { e.preventDefault(); const it = results[cursor]; if (it) run(it); }
              }}
              placeholder="Search conversations, views, pages, actions…"
              className="flex-1 min-w-0 bg-transparent text-base text-ink-1 placeholder:text-ink-3 outline-none"
              aria-label="Command palette"
            />
            <Kbd>esc</Kbd>
          </div>
          <div ref={listRef} className="flex-1 min-h-0 overflow-y-auto p-1" role="listbox">
            {results.length === 0 && <p className="text-sm text-ink-3 text-center py-8">No matches</p>}
            {results.map((it, i) => {
              const head = it.group !== lastGroup; lastGroup = it.group;
              return (
                <div key={it.id}>
                  {head && <div className="px-2 pt-2 pb-1 text-xs text-ink-3">{it.group}</div>}
                  <button
                    type="button"
                    role="option"
                    aria-selected={i === cursor}
                    onMouseEnter={() => setCursor(i)}
                    onClick={() => run(it)}
                    className={cn("w-full flex items-center gap-2 h-9 px-2 rounded-md text-sm text-ink-1 min-w-0 text-left [&_svg]:size-4 [&_svg]:text-ink-3 [&_svg]:shrink-0", i === cursor && "bg-surface-hover")}
                  >
                    {it.icon || <MessageSquare />}
                    <span className="truncate">{it.label}</span>
                    {it.hint && <span className="text-xs text-ink-3 truncate max-w-[45%]">{it.hint}</span>}
                    {it.shortcut && <Kbd className="ml-auto">{it.shortcut}</Kbd>}
                    {recent.includes(it.id) && !it.shortcut && <span className="ml-auto text-xs text-ink-muted">recent</span>}
                  </button>
                </div>
              );
            })}
          </div>
          <div className="shrink-0 border-t border-border px-3 h-8 flex items-center gap-2 text-xs text-ink-3"><Kbd>↑↓</Kbd> navigate <Kbd>⏎</Kbd> open</div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export const paletteChannelIcon = channelMeta;
