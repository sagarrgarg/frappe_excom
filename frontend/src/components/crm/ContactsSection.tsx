import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Loader2, MessageSquare, Plus, Star, Trash2, UserPlus, X } from "lucide-react";
import { usePartyContacts, type PartyContact } from "../../hooks/usePartyContacts";
import { Avatar, Badge, Button, Input, OverflowMenu, Select } from "../primitives";
import { CallButton } from "../voice/CallButton";
import type { CrmRef } from "../../hooks/useCrm";

/**
 * The people on this record.
 *
 * A deal is with a company, and inside the company a salesperson talks to several people: the one
 * who asks the price, the one who decides, the one who pays. Excom used to show one number per lead,
 * so that reality had to be kept in a note or split across duplicate leads. This is where it lives
 * instead — every person reachable from the row they are on.
 */
export function ContactsSection({ refr, canWrite }: { refr: CrmRef; canWrite: boolean }) {
  const { contacts, isLoading, adding, add, update, unlink } = usePartyContacts(refr);
  const [formOpen, setFormOpen] = useState(false);

  return (
    <section className="min-w-0">
      <div className="flex items-center gap-2 mb-1.5">
        <h4 className="text-xs text-ink-3">People</h4>
        {contacts.length > 0 && <span className="text-xs text-ink-muted">· {contacts.length}</span>}
        <div className="flex-1" />
        {canWrite && !formOpen && (
          <Button size="sm" variant="ghost" onClick={() => setFormOpen(true)}>
            <Plus />
            Add person
          </Button>
        )}
      </div>

      {isLoading && !contacts.length ? (
        <div className="flex justify-center py-4 text-ink-3">
          <Loader2 className="size-4 animate-spin" />
        </div>
      ) : contacts.length === 0 && !formOpen ? (
        <p className="text-xs text-ink-3">
          {canWrite
            ? "Nobody added yet. Add the people you deal with here — the buyer, whoever signs off, whoever pays."
            : "Nobody has been added to this record."}
        </p>
      ) : (
        <ul className="divide-y divide-border rounded-md border border-border">
          {contacts.map((c) => (
            <ContactRow
              key={c.name}
              contact={c}
              canWrite={canWrite}
              onUpdate={(v) => update(c.name, v)}
              onRemove={() => unlink(c.name)}
            />
          ))}
        </ul>
      )}

      {formOpen && (
        <AddPersonForm
          busy={adding}
          onCancel={() => setFormOpen(false)}
          onSubmit={async (person) => {
            if (await add(person)) setFormOpen(false);
          }}
        />
      )}
    </section>
  );
}

function ContactRow({
  contact,
  canWrite,
  onUpdate,
  onRemove,
}: {
  contact: PartyContact;
  canWrite: boolean;
  onUpdate: (values: { designation?: string; is_primary?: boolean }) => void;
  onRemove: () => void;
}) {
  const navigate = useNavigate();
  const [editingRole, setEditingRole] = useState(false);
  const [role, setRole] = useState(contact.designation);

  const menu = [
    [
      {
        id: "role",
        label: contact.designation ? "Change role" : "Add a role",
        onSelect: () => {
          setRole(contact.designation);
          setEditingRole(true);
        },
      },
      {
        id: "primary",
        label: contact.is_primary ? "Not the main contact" : "Make main contact",
        icon: <Star />,
        onSelect: () => onUpdate({ is_primary: !contact.is_primary }),
      },
    ],
    [
      {
        id: "remove",
        label: "Remove from this record",
        icon: <Trash2 />,
        danger: true,
        // Said on the menu item itself, because "remove" beside a person's name reads as a deletion.
        hint: "The contact and their conversation stay.",
        onSelect: onRemove,
      },
    ],
  ];

  return (
    <li className="flex items-center gap-2 px-2 py-1.5 min-w-0">
      <Avatar name={contact.full_name} size={28} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5 min-w-0">
          <span className="text-sm text-ink-1 truncate">{contact.full_name}</span>
          {contact.is_primary && <Badge>Main</Badge>}
        </div>
        {editingRole ? (
          <form
            className="flex items-center gap-1 mt-1"
            onSubmit={(e) => {
              e.preventDefault();
              onUpdate({ designation: role });
              setEditingRole(false);
            }}
          >
            <Input
              autoFocus
              value={role}
              onChange={(e) => setRole(e.target.value)}
              placeholder="Purchase manager, Owner, Accounts…"
              className="h-6 text-xs"
              onKeyDown={(e) => e.key === "Escape" && setEditingRole(false)}
            />
            <Button size="sm" variant="primary" type="submit">
              Save
            </Button>
            <Button size="sm" variant="ghost" type="button" onClick={() => setEditingRole(false)}>
              <X />
            </Button>
          </form>
        ) : (
          <p className="text-xs text-ink-3 truncate">
            {[contact.designation, contact.mobile_no, contact.email_id].filter(Boolean).join(" · ") ||
              "No role or contact details"}
          </p>
        )}
      </div>

      <div className="flex items-center gap-1 shrink-0">
        {/* The person's own number, so the call reaches them rather than whoever the record's
            identity belongs to. */}
        <CallButton number={contact.mobile_no} displayName={contact.full_name} size="sm" variant="ghost" />
        {/* Offered only when the conversation on that identity is really this person's. When a
            contact joins a lead's existing identity, the thread there is somebody else's, and
            sending an agent into it would be worse than not offering the button. */}
        {contact.own_conversation && contact.omni_identity && (
          <Button
            size="sm"
            variant="ghost"
            title="Open this person's conversation"
            onClick={() => navigate("/t/" + contact.omni_identity)}
          >
            <MessageSquare />
          </Button>
        )}
        {canWrite && <OverflowMenu groups={menu} size="icon-sm" />}
      </div>
    </li>
  );
}

/** Common roles on a deal, offered as suggestions — the field stays free text. */
const ROLE_HINTS = [
  "Owner",
  "Director",
  "Purchase Manager",
  "Procurement",
  "Accounts",
  "Technical / Quality",
  "Logistics",
  "Assistant",
];

function AddPersonForm({
  busy,
  onSubmit,
  onCancel,
}: {
  busy: boolean;
  onSubmit: (person: {
    first_name: string;
    last_name: string;
    designation: string;
    mobile_no: string;
    email_id: string;
  }) => void;
  onCancel: () => void;
}) {
  const [first, setFirst] = useState("");
  const [last, setLast] = useState("");
  const [designation, setDesignation] = useState("");
  const [mobile, setMobile] = useState("");
  const [email, setEmail] = useState("");

  const reachable = Boolean(mobile.trim() || email.trim());
  const ready = Boolean(first.trim()) && reachable;

  return (
    <form
      className="mt-2 rounded-md border border-border p-2 space-y-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (!ready) return;
        onSubmit({
          first_name: first.trim(),
          last_name: last.trim(),
          designation: designation.trim(),
          mobile_no: mobile.trim(),
          email_id: email.trim(),
        });
      }}
    >
      <div className="grid gap-2 [grid-template-columns:repeat(auto-fit,minmax(min(100%,140px),1fr))]">
        <Input autoFocus value={first} onChange={(e) => setFirst(e.target.value)} placeholder="First name" />
        <Input value={last} onChange={(e) => setLast(e.target.value)} placeholder="Last name" />
        <Select value={designation} onChange={(e) => setDesignation(e.target.value)}>
          <option value="">Role (optional)</option>
          {ROLE_HINTS.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </Select>
        <Input value={mobile} onChange={(e) => setMobile(e.target.value)} placeholder="Phone, with country code" inputMode="tel" />
        <Input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="Email" type="email" />
      </div>
      <div className="flex items-center gap-2">
        {/* Why the button is off, not merely that it is: a phone or an email is what makes this
            person callable, which is the reason for adding them here at all. */}
        <p className="text-xs text-ink-3 flex-1 min-w-0">
          {!first.trim()
            ? "A name is needed."
            : !reachable
              ? "Add a phone number or an email, so you can reach them."
              : "They will be reachable from this record straight away."}
        </p>
        <Button size="sm" variant="ghost" type="button" onClick={onCancel}>
          Cancel
        </Button>
        <Button size="sm" variant="primary" type="submit" disabled={!ready || busy}>
          {busy ? <Loader2 className="animate-spin" /> : <UserPlus />}
          Add
        </Button>
      </div>
    </form>
  );
}
