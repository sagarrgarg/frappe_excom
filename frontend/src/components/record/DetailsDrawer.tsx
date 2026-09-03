import { useSearchParams } from "react-router-dom";
import { X } from "lucide-react";
import { Drawer, Sheet, Button } from "../primitives";
import { useInbox } from "../shell/InboxProvider";
import { OmniIdentityPanel } from "../OmniIdentityPanel";
import type { Conversation } from "../../types";

/**
 * Details (UX-001 §3.2): the contact profile — identity, channels & accounts, linked ERP, summary, transactions.
 * Tasks / Notes / Activity / AI are tabs beside Chat in the record pane, not here.
 * wide = persistent 300px pane; laptop = push drawer (⌘.); tablet/phone = Sheet via ?panel=details (history-backed).
 */
export function DetailsDrawer() {
  const { bp, selected, detailsOpen, setDetailsOpen, openRecord } = useInbox();
  const [sp] = useSearchParams();
  const contact = selected!;
  const via = sp.get("via") || contact.activeAccountId;

  const conversation: Conversation = {
    id: contact.id, contactName: contact.contactName, contactAvatar: contact.contactAvatar, channel: contact.channels[0],
    lastMessage: contact.lastMessage, timestamp: contact.timestamp, unreadCount: contact.totalUnreadCount, status: contact.status,
    aiStatus: contact.aiStatus, assignedTo: contact.assignedTo, contactInfo: contact.contactInfo,
    activeAccount: contact.allAccounts.find((a) => a.id === via) || contact.allAccounts[0],
    otherAccounts: contact.allAccounts.filter((a) => a.id !== via), messages: [],
  };

  const body = <OmniIdentityPanel conversation={conversation} embedded onAccountSwitch={(id) => openRecord(contact.id, id)} />;

  if (bp === "phone" || bp === "tablet") {
    return (
      <Sheet open={detailsOpen} onOpenChange={setDetailsOpen} title={contact.contactName} side="auto" width="w-[400px]">
        {body}
      </Sheet>
    );
  }
  return (
    <Drawer open={detailsOpen}>
      {bp === "laptop" && (
        <div className="flex items-center h-header-h px-3 border-b border-border shrink-0 min-w-0">
          <span className="text-md truncate flex-1">Details</span>
          <Button variant="ghost" size="icon" aria-label="Close details (⌘.)" onClick={() => setDetailsOpen(false)}><X /></Button>
        </div>
      )}
      {body}
    </Drawer>
  );
}
