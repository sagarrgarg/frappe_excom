import { useMemo } from "react";
import { useFrappeEventListener } from "frappe-react-sdk";
import { useFrappeGetCall } from "@/lib/api";
import type { CallSummary } from "@/components/voice/CallCard";

/**
 * Every call belonging to one contact, keyed by call name.
 *
 * Fetched once per contact rather than once per timeline card: a thread with forty calls would
 * otherwise open forty requests, and the card only needs fields the list already returns.
 *
 * The feed refreshes itself when a call ends or its recording lands, so a card fills in while the
 * agent is still looking at it.
 */
export function useIdentityCalls(omniIdentity: string | null | undefined) {
  const { data, isLoading, mutate } = useFrappeGetCall<{ message: CallSummary[] }>(
    omniIdentity ? "excom.excom.api.voice.call_history" : null,
    omniIdentity ? { omni_identity: omniIdentity, limit: 100 } : undefined,
    omniIdentity ? `calls:${omniIdentity}` : null,
    { revalidateOnFocus: false },
  );

  const calls = data?.message ?? [];

  const byName = useMemo(() => {
    const map: Record<string, CallSummary> = {};
    for (const c of calls) map[c.name] = c;
    return map;
  }, [calls]);

  useFrappeEventListener("excom:call_ended", () => void mutate());
  useFrappeEventListener("excom:call_updated", () => void mutate());

  return { calls, byName, isLoading, refresh: mutate };
}

/**
 * The call id a timeline message points at.
 *
 * `content_json` is `{"call": "<name>"}` on a Call message. Parsing is defensive because the field
 * is free-form text on every other message type.
 */
export function callIdFromMessage(contentJson: string | undefined): string | null {
  if (!contentJson) return null;
  try {
    const parsed = JSON.parse(contentJson);
    const id = parsed?.call;
    return typeof id === "string" && id ? id : null;
  } catch {
    return null;
  }
}
