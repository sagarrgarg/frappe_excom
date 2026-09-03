import { useCallback, useEffect, useRef } from "react";
import { useFrappeEventListener } from "frappe-react-sdk";

interface ThreadUpdatedEvent {
  thread: string;
  event: "new_inbound" | "new_outbound";
}

const POLL_INTERVAL_MS = 10_000;

/**
 * Subscribes to realtime thread update events.
 * Includes a polling fallback (every 10s) so the thread list stays
 * current even if Socket.IO is temporarily unavailable.
 * Also refreshes immediately when the tab regains focus.
 */
export function useRealtimeThreads(onThreadUpdate: () => void) {
  const onUpdateRef = useRef(onThreadUpdate);
  onUpdateRef.current = onThreadUpdate;

  // Coalesce bursts (a broadcast or a reassign fires one event per thread) into one refetch.
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const handler = useCallback(
    (_data: ThreadUpdatedEvent) => {
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => { timer.current = null; onUpdateRef.current(); }, 1_000);
    },
    [],
  );

  useFrappeEventListener("excom:thread_updated", handler);

  // Polling fallback
  useEffect(() => {
    const id = setInterval(() => {
      onUpdateRef.current();
    }, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, []);

  // Refresh immediately when tab regains visibility
  useEffect(() => {
    const handleVisibility = () => {
      if (document.visibilityState === "visible") {
        onUpdateRef.current();
      }
    };
    document.addEventListener("visibilitychange", handleVisibility);
    return () => document.removeEventListener("visibilitychange", handleVisibility);
  }, []);
}
