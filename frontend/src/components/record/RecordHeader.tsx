import { ArrowLeft, ExternalLink, PanelRight } from "lucide-react";
import { Avatar, Button, Chip, OverflowMenu, Toolbar, useContainerWidth, type MenuGroup } from "../primitives";
import type { UnifiedContact } from "../../types";
import type { RecordRef } from "../../hooks/useRecordLinks";
import { deskUrl } from "../../hooks/useRecordLinks";
import type { Accent } from "../primitives/Chip";

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
  const sub = [contact.contactInfo.company, contact.contactInfo.phone || contact.contactInfo.email].filter(Boolean).join(" · ");
  return (
    <Toolbar ref={ref as any} className="gap-2">
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
      <OverflowMenu groups={menuGroups} label="Conversation actions" />
    </Toolbar>
  );
}
