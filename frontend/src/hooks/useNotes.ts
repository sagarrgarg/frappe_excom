import { useCallback } from "react";
import { useFrappeGetCall, useFrappePostCall } from "frappe-react-sdk";
import { toast } from "sonner";
import type { RecordRef } from "./useRecordLinks";

export interface Note {
  name: string;
  content: string;
  comment_email: string;
  comment_by: string;
  creation: string;
  owner: string;
}

/** Notes tab: core Comment on the linked party (a note about the party, not a thread moment). */
export function useNotes(record: RecordRef | null) {
  const { data, isLoading, error, mutate } = useFrappeGetCall<{ message: Note[] }>(
    record ? "excom.excom.api.record.get_notes" : (null as unknown as string),
    record ? { reference_doctype: record.doctype, reference_name: record.name } : undefined
  );
  const { call, loading: adding } = useFrappePostCall("excom.excom.api.record.add_note");

  const addNote = useCallback(
    async (content: string) => {
      if (!record) return;
      try {
        await call({ reference_doctype: record.doctype, reference_name: record.name, content });
        toast.success("Note added");
        await mutate();
      } catch (e: any) {
        toast.error(e?.message || "Failed to add note");
      }
    },
    [record, call, mutate]
  );

  return { notes: data?.message ?? [], isLoading, error, adding, addNote, refresh: mutate };
}
