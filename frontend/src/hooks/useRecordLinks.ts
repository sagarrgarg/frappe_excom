import { useMemo } from "react";
import { useLinkedEntities } from "./useLinkedEntities";

export interface RecordRef {
  doctype: string;
  name: string;
  title: string;
}

const PRIORITY = ["Opportunity", "Lead", "Customer", "Supplier", "Contact"];

/**
 * The "linked party" a Task/Note attaches to: first Opportunity/Lead/Customer/Supplier/Contact
 * linked to the identity, else the Omni Identity itself.
 */
export function useRecordRef(omniIdentity: string | null, displayName: string) {
  const { linkedEntities, isLoading } = useLinkedEntities(omniIdentity);
  const record: RecordRef | null = useMemo(() => {
    if (!omniIdentity) return null;
    for (const dt of PRIORITY) {
      const e = linkedEntities.find((x) => x.linked_doctype === dt);
      if (e) return { doctype: e.linked_doctype, name: e.linked_name, title: e.title || e.linked_name };
    }
    return { doctype: "Omni Identity", name: omniIdentity, title: displayName };
  }, [omniIdentity, linkedEntities, displayName]);
  return { record, linkedEntities, isLoading };
}

export function deskUrl(doctype: string, name: string): string {
  const slug = doctype.toLowerCase().replace(/\s+/g, "-");
  return `${window.location.origin}/app/${encodeURIComponent(slug)}/${encodeURIComponent(name)}`;
}
