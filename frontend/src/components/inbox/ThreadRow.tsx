import { memo } from "react";
import { Archive, ArchiveRestore, UserPlus, Eye, EyeOff, AlertOctagon, Trash2, Copy, Clock } from "lucide-react";
import { Row, Avatar, Badge, Chip, OverflowMenu, ContextMenu, type MenuGroup } from "../primitives";
import type { Accent } from "../primitives/Chip";
import { channelMeta } from "../../lib/channels";
import { SLA_RISK_MS } from "../../lib/views";
import { cn } from "../ui/utils";
import type { UnifiedContact } from "../../types";
import { formatServerTime } from "../../utils/datetime";

export interface RowActions {
  archive: (c: UnifiedContact) => void;
  unarchive: (c: UnifiedContact) => void;
  assignToMe: (c: UnifiedContact) => void;
  toggleRead: (c: UnifiedContact) => void;
  spam: (c: UnifiedContact) => void;
  del?: (c: UnifiedContact) => void;
  copy: (c: UnifiedContact) => void;
  snooze: (c: UnifiedContact) => void;
}

const KIND_ACCENT: Record<string, Accent> = { Customer: "green", Supplier: "sand", Employee: "teal", Opportunity: "violet", Lead: "amber" };

/** "Customer" · "Supplier" · "Lead · Export Importer" · "Opportunity · OEM · Quote" — the first (highest-precedence) link. */
export function kindLabel(c: UnifiedContact): { label: string; accent: Accent } | null {
  const k = c.kinds?.[0];
  if (!k) return null;
  const parts = [k.doctype === "Opportunity" ? "Opp" : k.doctype];
  if (k.customer_type) parts.push(k.customer_type);
  if (k.stage) parts.push(k.stage);
  return { label: parts.join(" · "), accent: KIND_ACCENT[k.doctype] || "neutral" };
}

function relTime(d: Date): string {
  const diff = Date.now() - d.getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return "now";
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h`;
  const day = Math.floor(h / 24);
  if (day < 7) return `${day}d`;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/**
 * ThreadRow (UX-001 §3.4): avatar 32 · name (600, truncate) · channel dots · time
 * second line = preview + tag dots + SLA pip. Unread = weight + 6px blue dot, never a row tint.
 * Row actions: hover (pointer) / visible `⋯` (touch) — T2.
 */
export const ThreadRow = memo(function ThreadRow({ c, selected, onOpen, actions, coarse, isSystemManager }: { c: UnifiedContact; selected: boolean; onOpen: () => void; actions: RowActions; coarse: boolean; isSystemManager: boolean }) {
  const unread = c.totalUnreadCount > 0;
  const archived = c.threads?.[0]?.status === "Closed";
  const kind = kindLabel(c);
  const ownerDisabled = Boolean(c.assignedToUser) && c.assignedToEnabled === false;
  const unassigned = !c.assignedToUser || ownerDisabled;
  const awaiting = c.lastMessageDirection === "Inbound";
  const slaRisk = awaiting && Date.now() - c.timestamp.getTime() > SLA_RISK_MS;
  const groups: MenuGroup[] = [
    [
      { id: "read", label: unread ? "Mark as read" : "Mark as unread", icon: unread ? <EyeOff /> : <Eye />, onSelect: () => actions.toggleRead(c) },
      { id: "assign", label: "Assign to me", icon: <UserPlus />, shortcut: "a", onSelect: () => actions.assignToMe(c) },
      archived
        ? { id: "unarchive", label: "Unarchive", icon: <ArchiveRestore />, shortcut: "e", onSelect: () => actions.unarchive(c) }
        : { id: "archive", label: "Archive", icon: <Archive />, shortcut: "e", onSelect: () => actions.archive(c) },
      { id: "snooze", label: "Snooze (mark unread)", icon: <Clock />, onSelect: () => actions.snooze(c) },
      { id: "copy", label: "Copy contact", icon: <Copy />, onSelect: () => actions.copy(c) },
    ],
    [
      { id: "spam", label: "Mark as spam", icon: <AlertOctagon />, danger: true, onSelect: () => actions.spam(c) },
      ...(isSystemManager && actions.del ? [{ id: "delete", label: "Delete", icon: <Trash2 />, danger: true, onSelect: () => actions.del!(c) }] : []),
    ],
  ];

  const detail = [
    c.contactName,
    [c.contactInfo.phone, c.contactInfo.email].filter(Boolean).join(" · ") || "No phone / email",
    c.contactInfo.company ? `Company: ${c.contactInfo.company}` : "",
    kind ? `${kind.label}${c.kinds?.[0]?.name ? ` · ${c.kinds[0].name}` : ""}` : "No ERP record yet",
    `Team: ${c.assignedTeamName || "—"} · Owner: ${ownerDisabled ? `${c.assignedTo?.name} (disabled)` : c.assignedTo?.name || "Unassigned"}`,
    `Last message: ${formatServerTime(c.timestamp)} · ${c.lastMessageDirection === "Inbound" ? "from them" : "from us"}${unread ? ` · ${c.totalUnreadCount} unread` : ""}`,
    c.tags?.length ? `Tags: ${c.tags.map((t) => t.tag_name).join(", ")}` : "",
    c.closure ? `Closed · ${c.closure.outcome}${c.closure.reason ? ` — ${c.closure.reason}` : ""}` : "",
  ].filter(Boolean).join(" | ");

  return (
    <ContextMenu groups={groups}>
    <Row
      selected={selected}
      onClick={onOpen}
      data-thread-row
      data-id={c.id}
      data-detail={detail}
      className={cn("border-b border-border", selected && "border-l-crayon-blue-base")}
      title={`${c.contactName}${c.contactInfo.company ? " · " + c.contactInfo.company : ""}\n${formatServerTime(c.timestamp)}`}
    >
      <div className="relative shrink-0">
        <Avatar name={c.contactName} src={c.contactAvatar} size={32} />
        {unread && <span className="absolute -left-2 top-1/2 -translate-y-1/2 size-1.5 rounded-full bg-crayon-blue-base" aria-label="Unread" />}
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5 min-w-0">
          <span className={cn("truncate text-sm min-w-0", unread ? "font-semibold text-ink-1" : "font-medium text-ink-1")}>{c.contactName}</span>
          {c.closure && <Chip size="sm" accent={["Resolved", "Converted"].includes(c.closure.outcome) ? "green" : "rose"} label={c.closure.outcome} className="shrink-0" title={c.closure.reason} />}
          {kind ? <Chip size="sm" accent={kind.accent} label={kind.label} className="shrink-0 max-w-[45%]" /> : <Chip size="sm" accent="neutral" label="Unknown" className="shrink-0 opacity-70" title="No ERP record — promote to Lead or link in Desk" />}
          <span className="flex items-center gap-0.5 shrink-0" aria-label={c.channels.join(", ")}>
            {c.channels.slice(0, 4).map((ch) => {
              const m = channelMeta(ch);
              return <m.icon key={ch} className={cn("size-3.5", `text-crayon-${m.accent}-base`)} aria-hidden />;
            })}
          </span>
          <span className={cn("t2-hide ml-auto shrink-0 text-xs tabular-nums", unread ? "text-crayon-blue-text font-medium" : "text-ink-3")}>{relTime(c.timestamp)}</span>
        </div>
        <div className="flex items-center gap-1.5 min-w-0 mt-0.5">
          {c.assignedTeamName && <span className="text-xs text-ink-3 truncate max-w-[30%] shrink-0 rounded bg-surface-sunken px-1" title={`Team: ${c.assignedTeamName}`}>{c.assignedTeamName}</span>}
          {unassigned ? <span className="text-xs text-crayon-amber-text shrink-0" title={ownerDisabled ? `Assignee ${c.assignedTo?.name} is disabled` : "Nobody owns this chat"}>{ownerDisabled ? "Owner disabled" : "Unassigned"}</span> : c.assignedTo?.name && <span className="text-xs text-ink-3 truncate max-w-[40%] shrink-0">{c.assignedTo.name.split(" ")[0]}:</span>}
          <span className={cn("truncate text-xs min-w-0", unread ? "text-ink-1" : "text-ink-3")}>{c.lastMessage || "—"}</span>
          <span className="t2-hide ml-auto flex items-center gap-1 shrink-0">
            {c.tags?.slice(0, 4).map((t) => <span key={t.tag} className="size-2 rounded-full" style={{ backgroundColor: t.color }} title={t.tag_name} />)}
            {c.broadcastDeliveryStatus && <Badge accent={c.broadcastDeliveryStatus === "Failed" ? "rose" : c.broadcastDeliveryStatus === "Sent" ? "green" : "amber"}>{c.broadcastDeliveryStatus}</Badge>}
            {slaRisk ? <span className="size-2 rounded-full bg-crayon-rose-base" title="SLA at risk — unanswered over 1h" /> : awaiting ? <span className="size-2 rounded-full bg-crayon-amber-base" title="Awaiting reply" /> : null}
            {unread && <Badge solid count={c.totalUnreadCount} />}
          </span>
        </div>
      </div>

      {/* T2 row actions: hover on pointer; always on touch */}
      <div className={cn("t2-reveal absolute right-1 top-1/2 -translate-y-1/2 flex items-center gap-0.5 bg-surface-hover rounded-md pl-1", selected && "bg-surface-active")} onClick={(e) => e.stopPropagation()}>
        {!coarse && (
          <>
            {/* Archive is deliberately not a hover button — it sat where people click to open the row. Use e, ⋯ or right-click. */}
            <button type="button" title="Assign to me (a)" aria-label="Assign to me" className="size-7 rounded flex items-center justify-center text-ink-3 hover:text-ink-1 hover:bg-surface" onClick={() => actions.assignToMe(c)}><UserPlus className="size-4" /></button>
            <button type="button" title={unread ? "Mark read" : "Mark unread"} aria-label="Toggle read" className="size-7 rounded flex items-center justify-center text-ink-3 hover:text-ink-1 hover:bg-surface" onClick={() => actions.toggleRead(c)}>{unread ? <Eye className="size-4" /> : <EyeOff className="size-4" />}</button>
          </>
        )}
        <OverflowMenu groups={groups} size="icon-sm" />
      </div>
    </Row>
    </ContextMenu>
  );
});
