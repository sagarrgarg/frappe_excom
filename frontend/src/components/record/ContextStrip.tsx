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


/**
 * The same facts as one quiet line under the contact's name, instead of a bar of its own.
 *
 * The bar repeated what the header already said (the linked Lead appeared in the header chip, in
 * the bar, and again in the details panel) and pushed the conversation down by another row. Here
 * only what changes the next move is kept: an Opportunity's stage, next action and amount, an
 * unclassified Lead, who owns the conversation, and its tags. The last-activity time is in the feed
 * itself; the team is in the details panel.
 */
export function ContextLine({ contact, crm, onStage, reach }: { contact: UnifiedContact; crm?: CrmRecordSummary | null; onStage?: () => void; reach?: string }) {
  const items: { text: string; title?: string; tone?: string; onClick?: () => void }[] = [];
  if (reach) items.push({ text: reach });
  if (crm?.doctype === "Opportunity") {
    items.push({ text: `${crm.customer_type ? crm.customer_type + " · " : ""}${crm.pipeline_stage || "—"}`, title: "Pipeline stage", tone: "text-crayon-blue-text font-medium", onClick: onStage });
    if (crm.next_action_at) items.push({ text: `Next ${crm.next_action_at.slice(0, 16)}`, title: "Next action", tone: new Date(crm.next_action_at) < new Date() ? "text-crayon-rose-text" : undefined });
    if (crm.opportunity_amount) items.push({ text: `₹${Number(crm.opportunity_amount).toLocaleString("en-IN")}`, title: "Amount" });
  } else if (crm?.doctype === "Lead" && !crm.customer_type) {
    items.push({ text: "Lead not classified", title: "Classify it from ⋯ to choose its pipeline", tone: "text-crayon-amber-text" });
  }
  if (contact.assignedTo) items.push({ text: contact.assignedTo.name, title: "Assigned to" });
  else items.push({ text: "Unassigned", title: "Nobody owns this conversation yet", tone: "text-crayon-amber-text" });
  return (
    <p className="flex items-center gap-1.5 min-w-0 text-xs text-ink-3 overflow-hidden whitespace-nowrap">
      {items.map((it, i) => (
        <span key={i} title={it.title} onClick={it.onClick} className={`truncate ${i === 0 ? "shrink-0 max-w-[60%]" : "min-w-0 shrink"} ${it.tone || ""} ${it.onClick ? "cursor-pointer hover:underline" : ""}`}>
          {i > 0 && <span className="text-border-strong pr-1.5" aria-hidden>·</span>}{it.text}
        </span>
      ))}
      {contact.tags?.slice(0, 4).map((t) => (
        <span key={t.tag} className="size-2 rounded-full shrink-0" style={{ backgroundColor: t.color }} title={t.tag_name} />
      ))}
    </p>
  );
}
