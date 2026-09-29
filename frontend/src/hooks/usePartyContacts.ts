import { useCallback } from "react";
import { useFrappePostCall } from "frappe-react-sdk";
import { toast } from "sonner";
import { useFrappeGetCall } from "@/lib/api";
import { toastError } from "../components/ErrorDialog";
import type { CrmRef } from "./useCrm";

export interface PartyContact {
  name: string;
  full_name: string;
  designation: string;
  company_name: string;
  mobile_no: string;
  email_id: string;
  phones: string[];
  emails: string[];
  is_primary: boolean;
  /** The identity this contact is attached to, if any. */
  omni_identity: string | null;
  /**
   * Whether that identity's conversation is really this person's. Often it is not: a contact added
   * to a lead that already has an identity joins that identity without taking it over, so the
   * conversation still belongs to whoever was there first. Opening it would be the wrong person.
   */
  own_conversation: boolean;
}

export interface NewContact {
  first_name: string;
  last_name?: string;
  designation?: string;
  mobile_no?: string;
  email_id?: string;
  is_primary?: boolean;
}

/**
 * The people on one CRM record.
 *
 * A company is rarely one person, and until now Excom showed one number per lead — so a deal with a
 * buyer, an owner and an accounts clerk had to live as three leads or as two names in a note. These
 * are plain Frappe Contacts linked the way the Desk form links them, so the two cannot disagree.
 */
export function usePartyContacts(refr: CrmRef | null) {
  const key = refr ? `party-contacts-${refr.doctype}-${refr.name}` : null;
  const { data, isLoading, mutate } = useFrappeGetCall<{ message: PartyContact[] }>(
    refr ? "excom.excom.api.party_contacts.list_contacts" : null,
    refr ? { doctype: refr.doctype, name: refr.name } : undefined,
    key,
  );

  const { call: addCall, loading: adding } = useFrappePostCall(
    "excom.excom.api.party_contacts.add_contact",
  );
  const { call: updateCall } = useFrappePostCall(
    "excom.excom.api.party_contacts.update_contact",
  );
  const { call: unlinkCall } = useFrappePostCall(
    "excom.excom.api.party_contacts.unlink_contact",
  );

  const add = useCallback(
    async (person: NewContact) => {
      if (!refr) return false;
      try {
        const res = await addCall({
          doctype: refr.doctype,
          name: refr.name,
          first_name: person.first_name,
          last_name: person.last_name || "",
          designation: person.designation || "",
          mobile_no: person.mobile_no || "",
          email_id: person.email_id || "",
          is_primary: person.is_primary ? 1 : 0,
        });
        const out = (res as { message?: { reused?: boolean; already_here?: boolean } })?.message;
        // Three different things happened, and a single "Saved" would hide two of them — most
        // usefully the one where an agent thinks they created somebody and did not.
        if (out?.already_here) {
          toast.info("Already on this record", {
            description: "That number or email is one of the contacts here.",
          });
        } else if (out?.reused) {
          toast.success("Contact linked", {
            description: "This person was already in your contacts, so their history came with them.",
          });
        } else {
          toast.success("Contact added");
        }
        await mutate();
        return true;
      } catch (err) {
        toastError(err, "Could not add the contact");
        return false;
      }
    },
    [refr, addCall, mutate],
  );

  const update = useCallback(
    async (contact: string, values: { designation?: string; is_primary?: boolean }) => {
      if (!refr) return;
      try {
        await updateCall({
          doctype: refr.doctype,
          name: refr.name,
          contact,
          // Only what changed is sent, so editing the role cannot clear the primary flag.
          ...(values.designation !== undefined ? { designation: values.designation } : {}),
          ...(values.is_primary !== undefined ? { is_primary: values.is_primary ? 1 : 0 } : {}),
        });
        await mutate();
      } catch (err) {
        toastError(err, "Could not save the contact");
      }
    },
    [refr, updateCall, mutate],
  );

  const unlink = useCallback(
    async (contact: string) => {
      if (!refr) return;
      try {
        const res = await unlinkCall({ doctype: refr.doctype, name: refr.name, contact });
        const changed = (res as { message?: { changed?: boolean } })?.message?.changed;
        if (changed) {
          // Said explicitly, because "removed" next to a person's name reads like a deletion and
          // an agent needs to know it was not one.
          toast.success("Removed from this record", {
            description: "The contact and their conversation are still there.",
          });
        }
        await mutate();
      } catch (err) {
        toastError(err, "Could not remove the contact");
      }
    },
    [refr, unlinkCall, mutate],
  );

  const contacts = data?.message ?? [];
  return { contacts, isLoading, adding, add, update, unlink, refresh: mutate };
}
