import { useFrappeGetCall } from "frappe-react-sdk";

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
    threadId ? "excom.excom.api.chat.get_pinned_messages" : "",
    threadId ? { thread_id: threadId } : undefined,
    threadId ? undefined : null
  );

  return {
    pinnedMessages: data?.message ?? [],
    error,
    isLoading,
    refresh: mutate,
  };
}
