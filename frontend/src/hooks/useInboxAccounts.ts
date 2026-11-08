import { useMemo } from "react";
import { useFrappeGetCall } from "@/lib/api";
import { CHANNEL_ORDER } from "../lib/channels";

/** One number, mailbox or page in the inbox list (api.chat.get_inbox_accounts). */
export interface InboxAccount {
  name: string;
  label: string;
  channel: string;
  identifier: string;
  active: boolean;
  open_threads: number;
  unread_threads: number;
  /** 0: visible through a desk, but this person's teams are not allowed to reply from it. */
  can_send: number;
  /** Mailboxes: folders with something in them (drafts, scheduled, unread spam). */
  folders?: Partial<Record<"drafts" | "scheduled" | "spam", number>>;
  /** Mailboxes: "Gmail" or "JMAP" (Stalwart). */
  server?: string;
}

export interface InboxAccountGroup {
  channel: string;
  accounts: InboxAccount[];
  unread: number;
}

const ORDER = [...CHANNEL_ORDER.slice(0, 4), "voice", ...CHANNEL_ORDER.slice(4)];

/** The accounts this person can see conversations on, grouped by channel, with unread counts. */
export function useInboxAccounts() {
  const { data, mutate, isLoading } = useFrappeGetCall<{ message: InboxAccount[] }>(
    "excom.excom.api.chat.get_inbox_accounts",
    undefined,
    "excom-inbox-accounts",
    { refreshInterval: 60_000, revalidateOnFocus: true },
  );
  const accounts = data?.message ?? [];
  const groups: InboxAccountGroup[] = useMemo(() => {
    const by = new Map<string, InboxAccount[]>();
    for (const a of accounts) by.set(a.channel, [...(by.get(a.channel) || []), a]);
    const rank = (c: string) => (ORDER.indexOf(c) < 0 ? 99 : ORDER.indexOf(c));
    return [...by.entries()]
      .sort(([a], [b]) => rank(a) - rank(b))
      .map(([channel, list]) => ({ channel, accounts: list, unread: list.reduce((s, a) => s + a.unread_threads, 0) }));
  }, [accounts]);
  const unread = accounts.reduce((s, a) => s + a.unread_threads, 0);
  // Folder counts across every mailbox, for "All inboxes".
  const folders = useMemo(() => {
    const out: Record<string, number> = {};
    for (const a of accounts) for (const [k, v] of Object.entries(a.folders || {})) out[k] = (out[k] || 0) + (v || 0);
    return out;
  }, [accounts]);
  const hasMail = accounts.some((a) => a.channel === "email");
  return { accounts, groups, unread, folders, hasMail, isLoading, refresh: mutate };
}

/** "WhatsApp · GGIL Sales" under the WhatsApp heading reads "GGIL Sales". */
export function shortLabel(label: string): string {
  return label.replace(/^(WhatsApp|Instagram|Messenger|Email)\s*·\s*/i, "");
}
