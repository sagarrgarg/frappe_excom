import { useCallback, useContext, useEffect, useMemo, useRef } from "react";
import useSWR from "swr";
import { FrappeContext, useFrappeEventListener } from "frappe-react-sdk";
import type { ExcomMessage, Message, UnifiedContact } from "../types";
import { parseFrappeDateTime } from "../utils/datetime";

const POLL_MS = 10_000;

export interface FeedMessage extends Message {
  threadId: string;
}

/** Same mapping as useMessages, kept local so the legacy hook stays untouched. */
function mapMessage(msg: ExcomMessage, threadId: string, account: { id: string; name: string; identifier: string; channel: string }): FeedMessage {
  const statusMap: Record<string, Message["status"]> = { Sent: "sent", Delivered: "delivered", Read: "read", Failed: "failed", Queued: "queued" };
  const typeMap: Record<string, Message["type"]> = {
    Text: "text", Image: "image", Video: "video", Audio: "audio", Document: "document", Sticker: "sticker", Location: "location",
    Template: "template", Email: "email", Interactive: "interactive", Flow: "flow", Reaction: "reaction", Contact: "contact", Button: "button",
  };
  return {
    id: msg.name,
    threadId,
    content: msg.content_text || "",
    timestamp: parseFrappeDateTime(msg.provider_timestamp || msg.creation),
    sender: msg.direction === "Inbound" ? "contact" : "user",
    status: statusMap[msg.delivery_status],
    type: typeMap[msg.message_type] || "text",
    mediaUrl: msg.media_file || undefined,
    channel: account.channel,
    isInternal: Boolean(msg.is_internal),
    isEmail: msg.message_type === "Email",
    contentJson: msg.content_json || undefined,
    rawDirection: msg.direction,
    sentBy: msg.sender_name ? { name: msg.sender_name, avatar: "" } : undefined,
    accountUsed: { id: account.id, name: account.name, identifier: account.identifier, channel: account.channel },
    isPinned: Boolean(msg.is_pinned),
    failureReason: msg.failure_reason || undefined,
    reactions: msg.reactions && typeof msg.reactions === "object" ? msg.reactions : undefined,
    replyTo: msg.reply_to
      ? { id: msg.reply_to, content: msg.reply_to_content || "", sender: msg.reply_to_sender || "", direction: msg.reply_to_direction || "" }
      : undefined,
  };
}

/**
 * Merged conversation feed (UX-001 §6.1): every thread of an identity in one chronological list.
 * One get_messages call per thread, merged client-side. Realtime + 10s poll fallback.
 * Note: get_messages auto-claims unassigned threads on open — with a merged feed that claims
 * every unassigned thread of the identity, i.e. one identity → one owner.
 */
export function useIdentityMessages(contact: UnifiedContact | null) {
  const ctx = useContext(FrappeContext);
  const accounts = contact?.allAccounts ?? [];
  const threadIds = useMemo(() => accounts.map((a) => a.id), [accounts]);
  const key = contact ? ["identity-messages", contact.id, threadIds.join("|")] : null;

  const fetcher = useCallback(async () => {
    if (!ctx) return { byThread: {}, autoClaimed: [] as string[] };
    const results = await Promise.all(
      accounts.map(async (a) => {
        const res = await ctx.call.get("excom.excom.api.chat.get_messages", { thread_id: a.id, limit: 100 });
        const payload = res?.message;
        const raw: ExcomMessage[] = Array.isArray(payload) ? payload : payload?.messages ?? [];
        const auto: string | null = payload && !Array.isArray(payload) ? payload.auto_claimed_by ?? null : null;
        return { id: a.id, messages: raw.map((m) => mapMessage(m, a.id, a)), auto };
      })
    );
    const byThread: Record<string, FeedMessage[]> = {};
    const autoClaimed: string[] = [];
    for (const r of results) {
      byThread[r.id] = r.messages;
      if (r.auto) autoClaimed.push(r.id);
    }
    return { byThread, autoClaimed };
  }, [ctx, accounts]);

  const { data, error, isLoading, mutate } = useSWR(key, fetcher, { revalidateOnFocus: true, dedupingInterval: 1000 });

  const messages: FeedMessage[] = useMemo(() => {
    if (!data) return [];
    const all = Object.values(data.byThread).flat();
    all.sort((a, b) => a.timestamp.getTime() - b.timestamp.getTime());
    return all;
  }, [data]);

  const refresh = useCallback(() => mutate(), [mutate]);
  const refreshRef = useRef(refresh);
  refreshRef.current = refresh;

  const idSet = useMemo(() => new Set(threadIds), [threadIds]);
  const onEvent = useCallback((d: { thread?: string }) => {
    if (d?.thread && idSet.has(d.thread)) refreshRef.current();
  }, [idSet]);
  useFrappeEventListener("excom:message_received", onEvent);
  useFrappeEventListener("excom:message_sent", onEvent);
  useFrappeEventListener("excom:message_status_updated", onEvent);

  useEffect(() => {
    if (!key) return;
    const id = setInterval(() => refreshRef.current(), POLL_MS);
    return () => clearInterval(id);
  }, [key?.join("|")]);

  return { messages, isLoading, error, refresh, autoClaimed: data?.autoClaimed ?? [] };
}
