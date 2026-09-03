import { useFrappeGetCall } from "frappe-react-sdk";
import type { RecordRef } from "./useRecordLinks";

export type ActivityItem =
  | { kind: "version"; id: string; by: string; at: string; changed: { field: string; old: unknown; new: unknown }[] }
  | { kind: "transfer"; id: string; by: string; at: string; from_team: string; to_team: string; note: string; thread: string }
  | { kind: "comment"; id: string; by: string; at: string; text: string }
  | { kind: "closure"; id: string; by: string; at: string; outcome: string; reason?: string };

/** Activity tab: Version + transfer log (server), merged with thread system messages client-side. */
export function useActivity(record: RecordRef | null, threadIds: string[]) {
  const enabled = Boolean(record || threadIds.length);
  const { data, isLoading, error, mutate } = useFrappeGetCall<{ message: ActivityItem[] }>(
    enabled ? "excom.excom.api.record.get_activity" : (null as unknown as string),
    enabled
      ? {
          reference_doctype: record?.doctype || "",
          reference_name: record?.name || "",
          thread_ids: JSON.stringify(threadIds),
        }
      : undefined
  );
  return { items: data?.message ?? [], isLoading, error, refresh: mutate };
}
