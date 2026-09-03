import { UserCheck, Users, Tag, Clock, Link2 } from "lucide-react";
import type { UnifiedContact } from "../../types";
import type { RecordRef } from "../../hooks/useRecordLinks";
import { formatServerShortDateTime } from "../../utils/datetime";

/**
 * ContextStrip (28px, one line, scrolls). P1 content: assignee · team · tags · linked record · last activity.
 * P3 replaces the first slots with stage · gate chips · next action (lens-aware).
 */
export function ContextStrip({ contact, record, amount }: { contact: UnifiedContact; record: RecordRef | null; amount?: string }) {
  const items: { icon: React.ReactNode; text: string; title?: string; tone?: string }[] = [];
  if (contact.assignedTo) items.push({ icon: <UserCheck />, text: contact.assignedTo.name, title: "Assigned to" });
  else items.push({ icon: <UserCheck />, text: "Unassigned", tone: "text-crayon-amber-text" });
  if (contact.assignedTeamName || contact.assignedTeam) items.push({ icon: <Users />, text: contact.assignedTeamName || contact.assignedTeam!, title: "Team" });
  if (record && record.doctype !== "Omni Identity") items.push({ icon: <Link2 />, text: `${record.doctype}: ${record.title}`, title: record.name });
  if (amount) items.push({ icon: <span className="text-xs">₹</span>, text: amount, title: "Amount" });
  items.push({ icon: <Clock />, text: formatServerShortDateTime(contact.timestamp), title: "Last activity" });

  return (
    <div className="h-context-h shrink-0 border-b border-border bg-surface-sunken px-3 min-w-0 flex items-center">
      <div className="chip-row w-full h-full text-xs text-ink-2">
        {items.map((it, i) => (
          <span key={i} title={it.title} className={`inline-flex items-center gap-1 min-w-0 max-w-[240px] [&_svg]:size-3.5 [&_svg]:text-ink-muted ${it.tone || ""}`}>
            {it.icon}
            <span className="truncate">{it.text}</span>
            {i < items.length - 1 && <span className="text-border-strong pl-1.5">·</span>}
          </span>
        ))}
        {contact.tags && contact.tags.length > 0 && (
          <span className="inline-flex items-center gap-1 [&_svg]:size-3.5 [&_svg]:text-ink-muted">
            <Tag />
            {contact.tags.map((t) => (
              <span key={t.tag} className="inline-flex items-center gap-1 text-xs text-ink-2 max-w-[140px]" title={t.tag_name}>
                <span className="size-2 rounded-full shrink-0" style={{ backgroundColor: t.color }} />
                <span className="truncate">{t.tag_name}</span>
              </span>
            ))}
          </span>
        )}
      </div>
    </div>
  );
}
