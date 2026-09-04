import { useMemo } from "react";
import { useFrappeGetCall } from "@/lib/api";
import type { UnifiedContact, ContactKind } from "../types";
import { parseFrappeDateTime } from "../utils/datetime";

interface IdentitySummary { name: string; display_name: string; primary_phone?: string; primary_email?: string; primary_whatsapp?: string; avatar_url?: string; creation: string; kinds: ContactKind[]; company: string; threads: number }

/** A contact with no conversation yet (migrated / form lead) as a UnifiedContact with zero threads, so the record pane can open it. */
export function useIdentityContact(id: string | null, enabled: boolean) {
  const { data, isLoading, error } = useFrappeGetCall<{ message: IdentitySummary }>(id && enabled ? "excom.excom.api.record.get_identity_contact" : null, id && enabled ? { omni_identity: id } : undefined, id && enabled ? `identity-contact-${id}` : undefined, { revalidateOnFocus: false });
  const contact = useMemo<UnifiedContact | null>(() => {
    const s = data?.message;
    if (!s) return null;
    return {
      id: s.name, contactName: s.display_name || s.primary_phone || s.primary_email || s.name, contactAvatar: s.avatar_url || "",
      contactInfo: { phone: s.primary_phone || s.primary_whatsapp || "", email: s.primary_email || "", company: s.company || "" },
      status: "offline", lastMessage: "", timestamp: parseFrappeDateTime(s.creation), totalUnreadCount: 0,
      allAccounts: [], activeAccountId: "", allMessages: [], channels: [], tags: [], threads: [], kinds: s.kinds || [],
    } as unknown as UnifiedContact;
  }, [data]);
  return { contact, isLoading, error };
}
