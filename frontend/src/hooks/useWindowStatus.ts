import { useFrappeGetCall } from "@/lib/api";

export interface WindowInfo {
  human_agent_ok?: boolean;
  window_open: boolean;
  last_inbound_at: string | null;
  hours_remaining: number;
}

/** WhatsApp 24h session window for a thread. Refreshes every minute. */
export function useWindowStatus(threadId: string | null, enabled: boolean) {
  const { data, mutate } = useFrappeGetCall<{ message: WindowInfo }>(
    threadId && enabled ? "excom.excom.api.chat.check_24h_window" : null,
    threadId && enabled ? { thread_id: threadId } : undefined,
    undefined,
    { refreshInterval: 60_000, revalidateOnFocus: true }
  );
  return { window: data?.message ?? null, refresh: mutate };
}

export function formatRemaining(hours: number): string {
  const total = Math.max(0, Math.round(hours * 60));
  const h = Math.floor(total / 60);
  const m = total % 60;
  return h > 0 ? `${h}h ${m.toString().padStart(2, "0")}m` : `${m}m`;
}
