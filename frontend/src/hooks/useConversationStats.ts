import { useFrappeGetCall } from "frappe-react-sdk";

export interface ConversationStats {
  total_messages: number;
  inbound_count: number;
  outbound_count: number;
  erp_users_replied: boolean;
  avg_response_time_seconds: number | null;
  channels: string[];
}

/**
 * Fetches live conversation statistics for all threads under an Omni Identity.
 * Replaces all hardcoded "~5 min" / "0 messages" values in the sidebar.
 */
export function useConversationStats(omniIdentity: string | null) {
  const { data, error, isLoading, mutate } = useFrappeGetCall<{
    message?: ConversationStats;
  }>(
    omniIdentity ? "excom.excom.api.chat.get_conversation_stats" : "",
    omniIdentity ? { omni_identity: omniIdentity } : undefined,
    omniIdentity ? undefined : null
  );

  const stats: ConversationStats = data?.message ?? {
    total_messages: 0,
    inbound_count: 0,
    outbound_count: 0,
    erp_users_replied: false,
    avg_response_time_seconds: null,
    channels: [],
  };

  return { stats, error, isLoading, refresh: mutate };
}

/**
 * Formats seconds into a human-readable duration string.
 * e.g. 90 -> "1m 30s", 3700 -> "1h 1m", null -> "—"
 */
export function formatResponseTime(seconds: number | null): string {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) {
    const mins = Math.floor(seconds / 60);
    const secs = Math.round(seconds % 60);
    return secs > 0 ? `${mins}m ${secs}s` : `${mins}m`;
  }
  const hours = Math.floor(seconds / 3600);
  const mins = Math.round((seconds % 3600) / 60);
  return mins > 0 ? `${hours}h ${mins}m` : `${hours}h`;
}
