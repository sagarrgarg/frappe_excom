import { useCallback } from "react";
import { useFrappePostCall } from "frappe-react-sdk";
import { useFrappeGetCall } from "@/lib/api";
import { toast } from "sonner";
import type { RecordRef } from "./useRecordLinks";

export interface Note {
  name: string;
  content: string;
  comment_email: string;
  comment_by: string;
  creation: string;
  owner: string;
  on_doctype: string;
  on_name: string;
}

/** Notes tab and chat internal notes share one model: Frappe Comments on the party's record (or thread). */
export function useNotes(identityId: string | null, _record?: RecordRef | null) {
  const { data, isLoading, error, mutate } = useFrappeGetCall<{ message: Note[] }>(
    identityId ? "excom.excom.api.record.get_identity_notes" : null,
    identityId ? { omni_identity: identityId } : undefined
  );
  const { call, loading: adding } = useFrappePostCall("excom.excom.api.record.add_identity_note");

  const addNote = useCallback(
    async (content: string) => {
      if (!identityId) return;
      try {
        await call({ omni_identity: identityId, content });
        toast.success("Note added");
        await mutate();
      } catch (e: any) {
        toast.error(e?.message || "Failed to add note");
      }
    },
    [identityId, call, mutate]
  );

  return { notes: data?.message ?? [], isLoading, error, adding, addNote, refresh: mutate };
}
