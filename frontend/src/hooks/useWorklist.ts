import { useFrappeGetCall } from "@/lib/api";

/**
 * What is waiting for this agent, kept apart by kind.
 *
 * The inbox badge used to be a sum the browser worked out over the hundred conversations it
 * happened to have loaded, so on a busy desk it was simply wrong. This comes from the server, over
 * everything, through the same visibility rule the inbox itself uses — so a number here never
 * points at something the inbox will then refuse to open.
 */

export interface UnreadStream {
  threads: number;
  messages: number;
}

export interface TaskPreview {
  name: string;
  description: string;
  date: string | null;
  priority: string;
  reference_type?: string;
  reference_name?: string;
}

export interface CallPreview {
  name: string;
  who: string;
  omni_identity: string | null;
  at: string;
}

export interface Worklist {
  whatsapp: UnreadStream;
  email: UnreadStream;
  other: UnreadStream;
  tasks: { count: number; items: TaskPreview[] };
  calls: { count: number; items: CallPreview[] };
  /** Conversations waiting, not messages — one person sending six lines is one person waiting. */
  unread_threads: number;
  needs_attention: number;
  at: string;
}

const EMPTY: Worklist = {
  whatsapp: { threads: 0, messages: 0 },
  email: { threads: 0, messages: 0 },
  other: { threads: 0, messages: 0 },
  tasks: { count: 0, items: [] },
  calls: { count: 0, items: [] },
  unread_threads: 0,
  needs_attention: 0,
  at: "",
};

/** Slow enough not to be a second heartbeat; a task falling due is not a per-second event. */
const POLL_MS = 60_000;

export function useWorklist() {
  const { data, isLoading, mutate } = useFrappeGetCall<{ message: Worklist }>(
    "excom.excom.api.worklist.get_worklist",
    undefined,
    "excom-worklist",
    { refreshInterval: POLL_MS, revalidateOnFocus: true },
  );

  return {
    worklist: data?.message ?? EMPTY,
    // Nothing has come back yet, so callers can tell "no work" from "not asked yet" and avoid
    // flashing a zero over a badge that is about to say three.
    ready: Boolean(data?.message),
    isLoading,
    refresh: mutate,
  };
}
