import { ArrowLeft, ExternalLink, PanelRight } from "lucide-react";
import { Avatar, Button, Chip, OverflowMenu, Toolbar, useContainerWidth, ContextMenu, type MenuGroup } from "../primitives";
import type { UnifiedContact } from "../../types";
import type { RecordRef } from "../../hooks/useRecordLinks";
import { deskUrl } from "../../hooks/useRecordLinks";
import type { Accent } from "../primitives/Chip";

/** Actions worth a visible icon once the pane is wide enough (≥ 720 px); the rest stay under ⋯. */
const PROMOTED = ["transfer", "tags", "assign", "classify", "promote", "convert", "tocust", "quote", "close", "unarchive"];
const EXPLAIN: Record<string, string> = {
  transfer: "Move this conversation to another team or person. Logged in Activity.",
  tags: "Add or remove tags; filter the inbox by tag:name.",
  assign: "Take ownership. Whoever replies first also claims the linked Lead.",
  classify: "Set the customer type — decides which pipeline the deal follows.",
  promote: "Create a Lead from this conversation and link it to the contact.",
  convert: "Qualify: create an Opportunity at stage Qualified from this Lead.",
  tocust: "Create a Customer straight from this Lead (Online B2C).",
  quote: "Open a new Quotation for this Opportunity in Desk.",
  close: "Close with an outcome; writes the activity log on the Lead / Opportunity / Customer.",
  unarchive: "Bring the conversation back to the inbox and reopen the CRM record.",
};

const ENTITY_ACCENT: Record<string, Accent> = { Lead: "amber", Opportunity: "violet", Customer: "green", Supplier: "sand", Contact: "teal", "Omni Identity": "neutral" };

/**
 * RecordHeader (48px) — T1: avatar · name · record chip. Everything else lives in `⋯` (T3).
 * Collapses the record chip below container width 480 (container, not viewport — §2.5 rule 3).
 */
export function RecordHeader({ contact, record, onBack, showBack, menuGroups, onToggleDetails, detailsToggleVisible }: {
  contact: UnifiedContact;
  record: RecordRef | null;
  onBack?: () => void;
  showBack?: boolean;
  menuGroups: MenuGroup[];
  onToggleDetails?: () => void;
  detailsToggleVisible?: boolean;
}) {
  const { ref, width } = useContainerWidth<HTMLDivElement>();
  const narrow = width > 0 && width < 480;
  const roomy = width >= 720;
  const flat = menuGroups.flat();
  const promoted = roomy ? PROMOTED.map((id) => flat.find((i) => i.id === id)).filter((i): i is NonNullable<typeof i> => Boolean(i)) : [];
  const rest = roomy ? menuGroups.map((g) => g.filter((i) => !PROMOTED.includes(i.id))) : menuGroups;
  const sub = [contact.contactInfo.company, contact.contactInfo.phone || contact.contactInfo.email].filter(Boolean).join(" · ");
  return (
    <ContextMenu groups={menuGroups}>
    <Toolbar ref={ref as any} className="gap-2" data-detail={`${contact.contactName} | ${sub || "No contact details"} | Right-click (or long-press) for actions`}>
      {showBack && <Button variant="ghost" size="icon" aria-label="Back to list" onClick={onBack}><ArrowLeft /></Button>}
      <Avatar name={contact.contactName} src={contact.contactAvatar} size={32} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 min-w-0">
          <h2 className="text-md text-ink-1 truncate min-w-0">{contact.contactName}</h2>
          {record && record.doctype !== "Omni Identity" && !narrow && (
            <a href={deskUrl(record.doctype, record.name)} target="_blank" rel="noreferrer" className="shrink-0 min-w-0 max-w-[40%]">
              <Chip size="sm" accent={ENTITY_ACCENT[record.doctype] || "neutral"} label={`${record.doctype} · ${record.name}`} icon={<ExternalLink />} title={record.title} />
            </a>
          )}
        </div>
        {sub && <p className="text-xs text-ink-3 truncate">{sub}</p>}
      </div>
      {detailsToggleVisible && (
        <Button variant="ghost" size="icon" aria-label="Toggle details (⌘.)" title="Details  ⌘." onClick={onToggleDetails}><PanelRight /></Button>
      )}
      {promoted.length > 0 && (
        <div className="flex items-center gap-0.5 shrink-0" role="toolbar" aria-label="Conversation actions">
          {promoted.map((it) => (
            <Button key={it.id} variant="ghost" size="icon" aria-label={it.label} title={`${it.label}${it.shortcut ? ` (${it.shortcut})` : ""}`} onClick={() => it.onSelect?.()} disabled={it.disabled}
              className={it.danger ? "text-crayon-rose-text" : undefined} data-detail={`${it.label}${it.shortcut ? ` · ${it.shortcut}` : ""} | ${EXPLAIN[it.id] || ""}`}>{it.icon}</Button>
          ))}
        </div>
      )}
      <OverflowMenu groups={rest} label="More actions" />
    </Toolbar>
    </ContextMenu>
  );
}
