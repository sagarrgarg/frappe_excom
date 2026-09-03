import { UserCheck, Users, Tag, Clock, Link2, KanbanSquare, CalendarClock } from "lucide-react";
import type { CrmRecordSummary } from "../../hooks/useCrm";
import type { UnifiedContact } from "../../types";
import type { RecordRef } from "../../hooks/useRecordLinks";
import { formatServerShortDateTime } from "../../utils/datetime";

/**
 * ContextStrip (28px, one line, scrolls). P1 content: assignee · team · tags · linked record · last activity.
 * P3 replaces the first slots with stage · gate chips · next action (lens-aware).
 */
export function ContextStrip({ contact, record, amount, crm, onStage }: { contact: UnifiedContact; record: RecordRef | null; amount?: string; crm?: CrmRecordSummary | null; onStage?: () => void }) {
  const items: { icon: React.ReactNode; text: string; title?: string; tone?: string; onClick?: () => void }[] = [];
  if (crm?.doctype === "Opportunity") {
    items.push({ icon: <KanbanSquare />, text: `${crm.customer_type || ""} · ${crm.pipeline_stage || "—"}`, title: "Pipeline stage", tone: "text-crayon-blue-text font-medium", onClick: onStage });
    if (crm.next_action_at) items.push({ icon: <CalendarClock />, text: `Next: ${crm.next_action_at.slice(0, 16)}`, title: "Next action", tone: new Date(crm.next_action_at) < new Date() ? "text-crayon-rose-text" : undefined });
    if (crm.opportunity_amount) items.push({ icon: <span className="text-xs">₹</span>, text: Number(crm.opportunity_amount).toLocaleString("en-IN"), title: "Amount" });
  } else if (crm?.doctype === "Lead") {
    items.push({ icon: <KanbanSquare />, text: crm.customer_type ? `Lead · ${crm.customer_type} · ${crm.intake_stage || ""}` : `Lead · unclassified`, title: "Intake", tone: crm.customer_type ? undefined : "text-crayon-amber-text" });
  }
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
          <span key={i} title={it.title} onClick={it.onClick} className={`inline-flex items-center gap-1 min-w-0 max-w-[240px] [&_svg]:size-3.5 [&_svg]:text-ink-muted ${it.tone || ""} ${it.onClick ? "cursor-pointer hover:underline" : ""}`}>
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
