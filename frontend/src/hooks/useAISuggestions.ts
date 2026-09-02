import { useFrappeGetCall, useFrappePostCall } from "frappe-react-sdk";

export interface AISuggestion {
  text: string;
  confidence: number;
}

export interface AISummary {
  text: string;
  updated_at: string;
  sentiment: string;
}

export interface AIAction {
  action: string;
  priority: string;
  due: string;
}

export interface AIInsights {
  response_pattern: string;
  engagement_rate: number;
  best_contact_time: string;
}

export interface AISuggestionsData {
  suggested_replies: AISuggestion[];
  summary: AISummary;
  next_actions: AIAction[];
  insights: AIInsights;
}

/**
 * Fetches AI suggestions for a thread.
 * Returns computed data from message history (Phase 2 stub).
 */
export function useAISuggestions(threadId: string | null) {
  const { data, error, isLoading, mutate } = useFrappeGetCall<{
    message?: AISuggestionsData;
  }>(
    threadId ? "excom.excom.api.chat.get_ai_suggestions" : "",
    threadId ? { thread_id: threadId } : undefined,
    threadId ? undefined : null,
  );

  const suggestions: AISuggestionsData = data?.message ?? {
    suggested_replies: [],
    summary: { text: "", updated_at: "", sentiment: "neutral" },
    next_actions: [],
    insights: { response_pattern: "—", engagement_rate: 0, best_contact_time: "—" },
  };

  return {
    suggestions,
    error,
    isLoading,
    refresh: mutate,
  };
}
