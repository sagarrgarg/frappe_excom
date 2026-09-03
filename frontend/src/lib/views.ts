import type { UnifiedContact } from "../types";
import { currentUser } from "./ui-flag";

/**
 * Saved views + search chips (UX-001 §3.4).
 * A view is a URL: /inbox/:view?channel=&account=&team=&tag=&broadcast=&bstatus=&from=&to=&q=
 * Chips are the URL params; typing `channel:whatsapp after:2026-08-01 foo` in the search box
 * writes the same params.
 */

export interface InboxFilters {
  q: string;
  channel: string;
  account: string;
  team: string;
  tags: string[];
  broadcast: string;
  bstatus: string;
  from: string;
  to: string;
  company: string;
  /** "1" → show archived (Closed) threads instead of open ones. */
  archived: string;
}

export const EMPTY_FILTERS: InboxFilters = {
  q: "", channel: "", account: "", team: "", tags: [], broadcast: "", bstatus: "", from: "", to: "", company: "", archived: "",
};

export const CHIP_KEYS: (keyof InboxFilters)[] = ["channel", "account", "team", "tags", "broadcast", "bstatus", "from", "to", "company", "archived"];

const CHIP_ALIASES: Record<string, keyof InboxFilters> = {
  channel: "channel", ch: "channel",
  account: "account", acc: "account",
  team: "team",
  tag: "tags", tags: "tags",
  broadcast: "broadcast", bc: "broadcast",
  bstatus: "bstatus", status: "bstatus",
  after: "from", from: "from", since: "from",
  before: "to", to: "to", until: "to",
  company: "company", co: "company",
  archived: "archived", is: "archived",
};

export function filtersFromParams(sp: URLSearchParams): InboxFilters {
  return {
    q: sp.get("q") || "",
    channel: sp.get("channel") || "",
    account: sp.get("account") || "",
    team: sp.get("team") || "",
    tags: (sp.get("tag") || "").split(",").filter(Boolean),
    broadcast: sp.get("broadcast") || "",
    bstatus: sp.get("bstatus") || "",
    from: sp.get("from") || "",
    to: sp.get("to") || "",
    company: sp.get("company") || "",
    archived: sp.get("archived") || "",
  };
}

export function paramsFromFilters(f: InboxFilters): URLSearchParams {
  const sp = new URLSearchParams();
  if (f.q) sp.set("q", f.q);
  if (f.channel) sp.set("channel", f.channel);
  if (f.account) sp.set("account", f.account);
  if (f.team) sp.set("team", f.team);
  if (f.tags.length) sp.set("tag", f.tags.join(","));
  if (f.broadcast) sp.set("broadcast", f.broadcast);
  if (f.bstatus) sp.set("bstatus", f.bstatus);
  if (f.from) sp.set("from", f.from);
  if (f.to) sp.set("to", f.to);
  if (f.company) sp.set("company", f.company);
  if (f.archived) sp.set("archived", "1");
  return sp;
}

/** Parse `channel:whatsapp team:export after:2026-08-01 free text` into filters + remaining text. */
export function parseSearchInput(text: string, base: InboxFilters): InboxFilters {
  const next: InboxFilters = { ...base, tags: [...base.tags] };
  const free: string[] = [];
  const re = /(\w+):("([^"]*)"|(\S+))/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    free.push(text.slice(last, m.index));
    last = m.index + m[0].length;
    const key = CHIP_ALIASES[m[1].toLowerCase()];
    const val = (m[3] ?? m[4] ?? "").trim();
    if (!key) { free.push(m[0]); continue; }
    if (key === "tags") { if (val && !next.tags.includes(val)) next.tags.push(val); }
    else if (key === "archived") next.archived = /^(1|true|yes|archived)$/i.test(val) ? "1" : "";
    else (next as any)[key] = val;
  }
  free.push(text.slice(last));
  next.q = free.join(" ").replace(/\s+/g, " ").trim();
  return next;
}

export function activeChips(f: InboxFilters): { key: keyof InboxFilters; value: string; label: string }[] {
  const out: { key: keyof InboxFilters; value: string; label: string }[] = [];
  if (f.channel) out.push({ key: "channel", value: f.channel, label: `channel:${f.channel}` });
  if (f.account) out.push({ key: "account", value: f.account, label: `account:${f.account}` });
  if (f.team) out.push({ key: "team", value: f.team, label: `team:${f.team === "__general__" ? "unassigned" : f.team}` });
  for (const t of f.tags) out.push({ key: "tags", value: t, label: `tag:${t}` });
  if (f.broadcast) out.push({ key: "broadcast", value: f.broadcast, label: `broadcast:${f.broadcast}` });
  if (f.bstatus) out.push({ key: "bstatus", value: f.bstatus, label: `status:${f.bstatus}` });
  if (f.from) out.push({ key: "from", value: f.from, label: `after:${f.from}` });
  if (f.to) out.push({ key: "to", value: f.to, label: `before:${f.to}` });
  if (f.company) out.push({ key: "company", value: f.company, label: `company:${f.company}` });
  if (f.archived) out.push({ key: "archived", value: "1", label: "archived" });
  return out;
}

export function removeChip(f: InboxFilters, key: keyof InboxFilters, value: string): InboxFilters {
  const next = { ...f, tags: [...f.tags] };
  if (key === "tags") next.tags = next.tags.filter((t) => t !== value);
  else (next as any)[key] = "";
  return next;
}

/* ─── Saved views ─── */

export type ViewId = "all" | "unread" | "mine" | "unassigned" | "today" | "sla" | "calls" | "comments" | string;

export interface SavedView {
  id: ViewId;
  label: string;
  /** Client-side predicate id for the built-ins. */
  predicate?: "unread" | "mine" | "unassigned" | "today" | "sla";
  filters: Partial<InboxFilters>;
  builtin?: boolean;
  hint?: string;
}

/** SLA proxy until P3 brings next_action_at/gate flags: unanswered inbound older than this. */
export const SLA_RISK_MS = 60 * 60 * 1000;

export const DEFAULT_VIEWS: SavedView[] = [
  { id: "unread", label: "Unread", predicate: "unread", filters: {}, builtin: true },
  { id: "mine", label: "Assigned to me", predicate: "mine", filters: {}, builtin: true },
  { id: "unassigned", label: "Unassigned", predicate: "unassigned", filters: {}, builtin: true },
  { id: "today", label: "Today's actions", predicate: "today", filters: {}, builtin: true, hint: "Awaiting your reply" },
  { id: "sla", label: "SLA risk", predicate: "sla", filters: {}, builtin: true, hint: "Unanswered for over an hour" },
  { id: "all", label: "All", filters: {}, builtin: true },
  { id: "calls", label: "Calls", filters: { channel: "calls" }, builtin: true },
  { id: "comments", label: "Comments", filters: { channel: "instagram" }, builtin: true, hint: "Instagram public comments" },
  { id: "archived", label: "Archived", filters: { archived: "1" }, builtin: true, hint: "Closed threads — reopen from the row menu" },
];

export function viewPredicate(view: SavedView | undefined): (c: UnifiedContact) => boolean {
  const me = currentUser();
  switch (view?.predicate) {
    case "unread": return (c) => c.totalUnreadCount > 0;
    case "mine": return (c) => Boolean(c.assignedToUser && c.assignedToUser === me);
    case "unassigned": return (c) => !c.assignedToUser && !c.assignedTeam;
    case "today": return (c) => c.lastMessageDirection === "Inbound";
    case "sla": return (c) => c.lastMessageDirection === "Inbound" && Date.now() - c.timestamp.getTime() > SLA_RISK_MS;
    default: return () => true;
  }
}

const SAVED_KEY = "excom_saved_views";

export function loadSavedViews(): SavedView[] {
  try {
    const raw = localStorage.getItem(SAVED_KEY);
    if (!raw) return [];
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? arr : [];
  } catch { return []; }
}

export function persistSavedViews(views: SavedView[]) {
  try { localStorage.setItem(SAVED_KEY, JSON.stringify(views)); } catch { /* ignore */ }
}

export function slugify(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40) || "view";
}
