import { useContext } from "react";
import useSWR from "swr";
import { FrappeContext, useFrappeGetCall } from "frappe-react-sdk";

interface PinnedMessage {
  name: string;
  content_text: string;
  direction: string;
  creation: string;
  message_type: string;
  created_by_user: string;
  sender_name: string;
}

export function usePinnedMessages(threadId: string) {
  const { data, error, isLoading, mutate } = useFrappeGetCall<{
    message: PinnedMessage[];
  }>(
    threadId
      ? "excom.excom.api.chat.get_pinned_messages"
      : (null as unknown as string),
    threadId ? { thread_id: threadId } : undefined
  );

  return {
    pinnedMessages: data?.message ?? [],
    error,
    isLoading,
    refresh: mutate,
  };
}


/** Pinned messages across every thread of an identity (merged feed). */
export function useIdentityPinnedMessages(threadIds: string[]) {
  const ctx = useContext(FrappeContext);
  const key = threadIds.length ? ["pinned", threadIds.join("|")] : null;
  const { data, mutate } = useSWR(key, async () => {
    if (!ctx) return [] as PinnedMessage[];
    const results = await Promise.allSettled(threadIds.map((id) => ctx.call.get("excom.excom.api.chat.get_pinned_messages", { thread_id: id })));
    const all: PinnedMessage[] = [];
    for (const r of results) if (r.status === "fulfilled") all.push(...((r.value?.message as PinnedMessage[]) || []));
    all.sort((a, b) => (a.creation < b.creation ? 1 : -1));
    return all;
  });
  return { pinnedMessages: data ?? [], refresh: mutate };
}
