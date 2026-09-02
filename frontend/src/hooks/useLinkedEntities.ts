import { useFrappeGetCall } from "frappe-react-sdk";

export interface LinkedEntity {
  linked_doctype: string;
  linked_name: string;
  role: string;
  title: string;
}

/**
 * Fetches linked ERP entities from the Omni Identity's linked_entities child table.
 * Used by OmniIdentityPanel to display and open linked Lead/Customer/Contact/Supplier docs.
 */
export function useLinkedEntities(omniIdentity: string | null) {
  const { data, error, isLoading, mutate } = useFrappeGetCall<{
    message?: LinkedEntity[];
  }>(
    omniIdentity ? "excom.excom.api.chat.get_linked_entities" : "",
    omniIdentity ? { omni_identity: omniIdentity } : undefined,
    omniIdentity ? undefined : null
  );

  const linkedEntities = data?.message ?? [];

  return {
    linkedEntities,
    error,
    isLoading,
    refresh: mutate,
  };
}
