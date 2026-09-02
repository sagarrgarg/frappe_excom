import { useFrappeGetCall } from "frappe-react-sdk";

export interface CannedResponse {
  name: string;
  title: string;
  shortcode: string;
  content: string;
  category: string;
  channel: string;
}

/**
 * Fetches canned responses, optionally filtered by search and channel.
 * Pass null to skip fetching (e.g., when popover is closed).
 */
export function useCannedResponses(search: string | null, channel?: string) {
  const { data, error, isLoading, mutate } = useFrappeGetCall<{
    message?: CannedResponse[];
  }>(
    search !== null
      ? "excom.excom.api.chat.get_canned_responses"
      : null,
    search !== null
      ? { search: search || "", channel: channel || "" }
      : undefined,
  );

  return {
    responses: data?.message ?? [],
    error,
    isLoading,
    refresh: mutate,
  };
}
