import { useFrappeGetCall, useFrappePostCall } from "frappe-react-sdk";
import { useCallback } from "react";
import { toast } from "sonner";

interface ExcomTag {
  name: string;
  tag_name: string;
  color: string;
  description?: string;
}

interface ThreadTagEntry {
  tag: string;
  tag_name: string;
  color: string;
}

export function useTags() {
  const { data, error, isLoading, mutate } = useFrappeGetCall<{
    message: ExcomTag[];
  }>("excom.excom.api.chat.get_tags");

  return {
    tags: data?.message ?? [],
    error,
    isLoading,
    refresh: mutate,
  };
}

export function useThreadTags(threadId: string) {
  const { data, error, isLoading, mutate } = useFrappeGetCall<{
    message: ThreadTagEntry[];
  }>(
    threadId ? "excom.excom.api.chat.get_thread_tags" : "",
    threadId ? { thread_id: threadId } : undefined,
    threadId ? undefined : null
  );

  const { call: addCall } = useFrappePostCall("excom.excom.api.chat.add_thread_tag");
  const { call: removeCall } = useFrappePostCall("excom.excom.api.chat.remove_thread_tag");

  const addTag = useCallback(
    async (tagName: string) => {
      if (!threadId) return;
      try {
        await addCall({ thread_id: threadId, tag_name: tagName });
        mutate();
      } catch {
        toast.error("Failed to add tag");
      }
    },
    [threadId, addCall, mutate]
  );

  const removeTag = useCallback(
    async (tagName: string) => {
      if (!threadId) return;
      try {
        await removeCall({ thread_id: threadId, tag_name: tagName });
        mutate();
      } catch {
        toast.error("Failed to remove tag");
      }
    },
    [threadId, removeCall, mutate]
  );

  return {
    threadTags: data?.message ?? [],
    error,
    isLoading,
    refresh: mutate,
    addTag,
    removeTag,
  };
}
