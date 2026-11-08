import { Inbox, FileText, Clock, Send, Archive, AlertOctagon, Trash2, type LucideIcon } from "lucide-react";

/**
 * Mail folders, as JMAP / Outlook show them (server: channels/email/mailbox.py folder_where).
 * Inbox is the normal inbox; the others list conversations with an email in that folder.
 * `count` names the key of InboxAccount.folders shown beside it (unread spam, drafts, scheduled);
 * Sent, Archive and Trash show no count, as in every mail app.
 */
export interface MailFolder {
  id: "inbox" | "drafts" | "scheduled" | "sent" | "archive" | "spam" | "trash";
  label: string;
  icon: LucideIcon;
  count?: "drafts" | "scheduled" | "spam";
  empty: string;
}

export const MAIL_FOLDERS: MailFolder[] = [
  { id: "inbox", label: "Inbox", icon: Inbox, empty: "Nothing waiting." },
  { id: "drafts", label: "Drafts", icon: FileText, count: "drafts", empty: "No drafts. Replies you start and leave are kept here, and in the mailbox's Drafts." },
  { id: "scheduled", label: "Scheduled", icon: Clock, count: "scheduled", empty: "Nothing scheduled. Pick a time under Send to send later." },
  { id: "sent", label: "Sent", icon: Send, empty: "Nothing sent from this mailbox yet." },
  { id: "archive", label: "Archive", icon: Archive, empty: "Nothing archived." },
  { id: "spam", label: "Spam", icon: AlertOctagon, count: "spam", empty: "No spam." },
  { id: "trash", label: "Trash", icon: Trash2, empty: "Trash is empty." },
];

export function folderMeta(id: string): MailFolder {
  return MAIL_FOLDERS.find((f) => f.id === id) || MAIL_FOLDERS[0];
}
