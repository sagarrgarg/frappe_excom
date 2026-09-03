import { useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import useSWR from "swr";
import { FrappeContext, useFrappeEventListener } from "frappe-react-sdk";
import type { ExcomMessage, Message, UnifiedContact } from "../types";
import { parseFrappeDateTime } from "../utils/datetime";

const POLL_MS = 10_000;
const PAGE = 100;

export interface FeedMessage extends Message {
  threadId: string;
  /** Raw row kept for pagination cursors. */
  raw?: { creation: string; provider_timestamp?: string };
}

/** Same mapping as useMessages, kept local so the legacy hook stays untouched. */
function mapMessage(msg: ExcomMessage, threadId: string, account: { id: string; name: string; identifier: string; channel: string }): FeedMessage {
  const statusMap: Record<string, Message["status"]> = { Sent: "sent", Delivered: "delivered", Read: "read", Failed: "failed", Queued: "queued", Scheduled: "scheduled" };
  const typeMap: Record<string, Message["type"]> = {
    Text: "text", Image: "image", Video: "video", Audio: "audio", Document: "document", Sticker: "sticker", Location: "location",
    Template: "template", Email: "email", Interactive: "interactive", Flow: "flow", Reaction: "reaction", Contact: "contact", Button: "button",
  };
  return {
    id: msg.name,
    threadId,
    raw: { creation: msg.creation, provider_timestamp: msg.provider_timestamp },
    content: msg.content_text || "",
    timestamp: parseFrappeDateTime(msg.provider_timestamp || msg.creation),
    sender: msg.direction === "Inbound" ? "contact" : "user",
    status: statusMap[msg.delivery_status],
    type: typeMap[msg.message_type] || "text",
    mediaUrl: msg.media_file || undefined,
    channel: account.channel,
    isInternal: Boolean(msg.is_internal),
    noteOn: (msg as ExcomMessage & { note_on?: { doctype: string; name: string } }).note_on,
    scheduledAt: (msg as ExcomMessage & { scheduled_at?: string }).scheduled_at ? parseFrappeDateTime((msg as ExcomMessage & { scheduled_at?: string }).scheduled_at as string) : undefined,
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

  // Older pages loaded via "Load earlier" are kept per thread and merged in.
  const [older, setOlder] = useState<Record<string, FeedMessage[]>>({});
  const [loadingOlder, setLoadingOlder] = useState(false);
  useEffect(() => { setOlder({}); }, [contact?.id]);

  const fetcher = useCallback(async () => {
    if (!ctx) return { byThread: {}, autoClaimed: [] as string[], hasMore: {} as Record<string, boolean>, failed: [] as string[] };
    // allSettled: one inaccessible/rate-limited thread must not blank the whole feed.
    const results = await Promise.allSettled(
      accounts.map(async (a) => {
        const res = await ctx.call.get("excom.excom.api.chat.get_messages", { thread_id: a.id, limit: PAGE });
        const payload = res?.message;
        const raw: ExcomMessage[] = Array.isArray(payload) ? payload : payload?.messages ?? [];
        const auto: string | null = payload && !Array.isArray(payload) ? payload.auto_claimed_by ?? null : null;
        const hasMore: boolean = payload && !Array.isArray(payload) ? Boolean(payload.has_more ?? raw.length >= PAGE) : raw.length >= PAGE;
        return { id: a.id, messages: raw.map((m) => mapMessage(m, a.id, a)), auto, hasMore };
      })
    );
    const byThread: Record<string, FeedMessage[]> = {};
    const hasMore: Record<string, boolean> = {};
    const autoClaimed: string[] = [];
    const failed: string[] = [];
    results.forEach((r, i) => {
      const id = accounts[i].id;
      if (r.status === "fulfilled") {
        byThread[id] = r.value.messages;
        hasMore[id] = r.value.hasMore;
        if (r.value.auto) autoClaimed.push(id);
      } else {
        failed.push(id);
      }
    });
    if (failed.length === accounts.length && accounts.length) throw (results[0] as PromiseRejectedResult).reason;
    return { byThread, autoClaimed, hasMore, failed };
  }, [ctx, accounts]);

  const { data, error, isLoading, mutate } = useSWR(key, fetcher, { revalidateOnFocus: true, dedupingInterval: 1000, errorRetryInterval: 5000 });

  const messages: FeedMessage[] = useMemo(() => {
    if (!data) return [];
    const seen = new Set<string>();
    const all: FeedMessage[] = [];
    for (const list of [...Object.values(older), ...Object.values(data.byThread)]) {
      for (const m of list) { if (!seen.has(m.id)) { seen.add(m.id); all.push(m); } }
    }
    all.sort((a, b) => a.timestamp.getTime() - b.timestamp.getTime());
    return all;
  }, [data, older]);

  /** Any thread whose latest page was full and whose older pages are not yet exhausted. */
  const canLoadOlder = useMemo(() => {
    if (!data) return false;
    return Object.entries(data.hasMore).some(([id, more]) => more && !(older[id] && older[id].length === 0));
  }, [data, older]);

  const loadOlder = useCallback(async () => {
    if (!ctx || !data || loadingOlder) return;
    setLoadingOlder(true);
    try {
      await Promise.all(
        accounts.map(async (a) => {
          if (!data.hasMore[a.id] || (older[a.id] && older[a.id].length === 0)) return;
          const current = [...(older[a.id] || []), ...(data.byThread[a.id] || [])];
          const oldest = current.reduce<FeedMessage | null>((min, m) => (!min || m.timestamp < min.timestamp ? m : min), null);
          if (!oldest) return;
          const res = await ctx.call.get("excom.excom.api.chat.get_messages", { thread_id: a.id, limit: PAGE, before: oldest.raw?.provider_timestamp || oldest.raw?.creation });
          const raw: ExcomMessage[] = Array.isArray(res?.message) ? res.message : res?.message?.messages ?? [];
          setOlder((prev) => ({ ...prev, [a.id]: [...raw.map((m) => mapMessage(m, a.id, a)), ...(prev[a.id] || [])].concat(raw.length < PAGE ? [] : []) }));
          if (raw.length < PAGE) setOlder((prev) => ({ ...prev, [a.id]: prev[a.id]?.length ? prev[a.id] : [] }));
        })
      );
    } finally {
      setLoadingOlder(false);
    }
  }, [ctx, data, older, accounts, loadingOlder]);

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
    const id = setInterval(() => { if (document.visibilityState === "visible") refreshRef.current(); }, POLL_MS);
    return () => clearInterval(id);
  }, [key?.join("|")]);

  return { messages, isLoading, error, refresh, autoClaimed: data?.autoClaimed ?? [], failedThreads: data?.failed ?? [], canLoadOlder, loadOlder, loadingOlder };
}
