"""The several people behind one company.

A lead is a company, and a company is rarely one person. The buyer asks the price, the owner decides,
the accounts clerk pays — and a desk working that deal needs all three on the one record, not three
leads that happen to share a name.

Frappe already models this and Excom simply never showed it: a Contact carries its own phones and
emails, and a Dynamic Link attaches it to the record. Nothing here invents a second model, so what
Excom shows and what the Desk form shows cannot drift apart.

One thing is reported beyond what the Desk form shows, and it is the point of putting these people on
the deal rather than in a note: whether each of them can be reached, and whose conversation is whose.
"""

import frappe
from frappe import _

from excom.excom.api.chat import _check_excom_access
from excom.excom.services.access import deny
from excom.excom.utils.phone import normalize_phone
from excom.excom.utils.ratelimit import user_rate_limit


def _check_record_access(doctype: str, name: str, write: bool = False) -> None:
	"""A CRM record the caller may see — and, when asked, may change.

	What counts as a CRM record comes from the gateway, which is the one place that knows. A second
	copy of that list here is how the two come to disagree.
	"""
	from excom.excom.services import crm_gateway as gw

	if doctype not in gw.crm_doctypes():
		frappe.throw(_("{0} is not a CRM record.").format(doctype))
	if not frappe.db.exists(doctype, name):
		frappe.throw(_("{0} {1} not found").format(_(doctype), name), frappe.DoesNotExistError)
	if not frappe.has_permission(doctype, "write" if write else "read", doc=name):
		deny(
			_("You cannot {0} this {1}.").format("edit" if write else "open", _(doctype)),
			detail=_("{0} {1} belongs to another desk. A manager can reassign it.").format(
				_(doctype), name
			),
		)


def _contacts_on(doctype: str, name: str) -> list[str]:
	return frappe.get_all(
		"Dynamic Link",
		filters={"parenttype": "Contact", "link_doctype": doctype, "link_name": name},
		pluck="parent",
		distinct=True,
	)


def _require_linked(contact: str, doctype: str, name: str) -> None:
	"""A contact may only be changed through a record it actually belongs to.

	Without this, write permission on any one lead would be permission to edit every contact in the
	site: the record check above would pass while the contact it is supposed to guard is somebody
	else's entirely.
	"""
	if not frappe.db.exists(
		"Dynamic Link",
		{"parenttype": "Contact", "parent": contact, "link_doctype": doctype, "link_name": name},
	):
		deny(
			_("That contact is not on this record."),
			detail=_("Open the record they do belong to and change them there."),
		)


# ── the conversation behind each person ───────────────────────────────────────


def _identities_for(contacts: list[str]) -> dict:
	if not contacts:
		return {}
	rows = frappe.get_all(
		"Omni Identity Link",
		filters={
			"parenttype": "Omni Identity",
			"linked_doctype": "Contact",
			"linked_name": ["in", contacts],
		},
		fields=["parent", "linked_name"],
	)
	return {r.linked_name: r.parent for r in rows}


def _identity_rows(identities: list[str]) -> dict:
	if not identities:
		return {}
	rows = frappe.get_all(
		"Omni Identity",
		filters={"name": ["in", identities]},
		fields=["name", "primary_phone", "primary_email"],
	)
	return {r.name: r for r in rows}


def _shortlist(number: str) -> str:
	"""The tail used to narrow the search in SQL. A shortlist, never the answer."""
	digits = normalize_phone(number or "").lstrip("+")
	return digits[-8:] if len(digits) >= 8 else ""


def _same_number(a: str, b: str) -> bool:
	"""Whether two written numbers are one telephone.

	+91 98765-43210, 09876543210 and 9876543210 are the same phone, so a plain string comparison is
	no good. Comparing only the tail is no good either: +91 99000 00881 and +44 1900 000881 end in the
	same nine digits and belong to two different people in two different countries, and treating them
	as one merges two strangers' records.

	So the rule follows what the writing actually claims. Two numbers that both name their country are
	compared in full. A number written without one is only a local part, and matches when it is the
	tail of the other — which is the case this has to get right, because that is how numbers arrive
	from a keyboard.
	"""
	da, db = normalize_phone(a or ""), normalize_phone(b or "")
	if not da or not db:
		return False

	both_international = da.startswith("+") and db.startswith("+")
	da, db = da.lstrip("+").lstrip("0"), db.lstrip("+").lstrip("0")
	if not da or not db:
		return False
	if both_international:
		return da == db

	short, long_ = (da, db) if len(da) <= len(db) else (db, da)
	# Seven digits is the shortest thing worth calling a number; below that a suffix match is a
	# coincidence rather than a telephone.
	return len(short) >= 7 and long_.endswith(short)


def _shape(doc, identity: str | None, ident_row) -> dict:
	phones = [p.phone for p in doc.get("phone_nos", []) if p.phone]
	emails = [e.email_id for e in doc.get("email_ids", []) if e.email_id]
	mobile = doc.mobile_no or doc.phone or (phones[0] if phones else "")
	email = doc.email_id or (emails[0] if emails else "")

	# Whether the conversation on that identity is *this* person's, which it often is not.
	#
	# When a contact is added to a lead that already has an identity, Excom attaches the contact to
	# that same identity and leaves its number alone — so the identity still belongs to whoever was
	# there first. Offering "open chat" on it would drop an agent into the wrong person's
	# conversation, which is worse than not offering it at all, so the fact is reported and the UI
	# obeys it rather than guessing.
	own = False
	if identity and ident_row:
		own = bool(
			(mobile and _same_number(ident_row.primary_phone, mobile))
			or (email and (ident_row.primary_email or "").lower() == email.lower())
		)

	return {
		"name": doc.name,
		"full_name": (" ".join(p for p in [doc.first_name, doc.last_name] if p)).strip() or doc.name,
		"designation": doc.designation or "",
		"company_name": doc.company_name or "",
		"mobile_no": mobile,
		"email_id": email,
		"phones": phones,
		"emails": emails,
		"is_primary": bool(doc.is_primary_contact),
		"omni_identity": identity,
		"own_conversation": own,
	}


@frappe.whitelist()
@user_rate_limit(limit=120, seconds=60)
def list_contacts(doctype: str, name: str) -> list:
	"""Everybody attached to this record, the primary person first."""
	_check_excom_access()
	_check_record_access(doctype, name)

	names = _contacts_on(doctype, name)
	if not names:
		return []

	identities = _identities_for(names)
	rows = _identity_rows(sorted({i for i in identities.values() if i}))
	out = [
		_shape(frappe.get_doc("Contact", n), identities.get(n), rows.get(identities.get(n)))
		for n in names
	]
	out.sort(key=lambda c: (not c["is_primary"], c["full_name"].lower()))
	return out


# ── adding somebody ───────────────────────────────────────────────────────────


def _find_existing(mobile_no: str, email_id: str) -> str | None:
	"""The same person, already on file.

	Somebody who deals with two of your leads is one person. A second Contact for them is how a
	contact list becomes a pile of near-duplicates, each holding half the history.
	"""
	if email_id:
		row = frappe.get_all(
			"Contact Email", filters={"email_id": email_id}, fields=["parent"], limit=1
		)
		if row:
			return row[0].parent

	tail = _shortlist(mobile_no)
	if tail:
		# Shortlisted on the tail because SQL cannot normalise, then decided in Python. A number that
		# merely ends the same way is a different telephone, and merging it here would fold two
		# strangers into one record.
		for row in frappe.get_all(
			"Contact Phone",
			filters={"phone": ["like", "%" + tail]},
			fields=["parent", "phone"],
			limit=50,
		):
			if _same_number(row.phone, mobile_no):
				return row.parent
	return None


def _link(contact: str, doctype: str, name: str) -> bool:
	"""Attach an existing contact to this record, once. True when it was not already there.

	The record check is repeated here although every caller has already made it. It is the line that
	writes past permissions, and a guard one call away cannot be seen by somebody reading this
	function — so the check lives where the bypass lives, and the helper is safe to call from
	anywhere.
	"""
	_check_record_access(doctype, name, write=True)

	doc = frappe.get_doc("Contact", contact)
	for link in doc.get("links", []):
		if link.link_doctype == doctype and link.link_name == name:
			return False
	doc.append("links", {"link_doctype": doctype, "link_name": name})
	doc.save(ignore_permissions=True)
	return True


@frappe.whitelist(methods=["POST"])
@user_rate_limit(limit=30, seconds=60)
def add_contact(
	doctype: str,
	name: str,
	first_name: str,
	last_name: str = "",
	designation: str = "",
	mobile_no: str = "",
	email_id: str = "",
	is_primary: int = 0,
) -> dict:
	"""Put a person on this record, and make them reachable.

	Reachable is the point. `Contact.after_insert` already runs Excom's identity sync, so somebody
	added here turns up with a number that can be dialled — which is the whole reason for putting
	them on the deal instead of in a note.
	"""
	_check_excom_access()
	_check_record_access(doctype, name, write=True)

	first_name = (first_name or "").strip()
	if not first_name:
		frappe.throw(_("A name is required."))

	mobile_no = (mobile_no or "").strip()
	email_id = (email_id or "").strip().lower()
	if not mobile_no and not email_id:
		# A person nobody can reach is a note, and notes have their own place.
		frappe.throw(_("Give a phone number or an email, so this person can be reached."))

	existing = _find_existing(mobile_no, email_id)
	if existing:
		added = _link(existing, doctype, name)
		frappe.db.commit()
		# Said plainly, because "added" and "was already here" are different answers and the second
		# one is not an error.
		return {"contact": existing, "reused": True, "already_here": not added}

	doc = frappe.new_doc("Contact")
	doc.first_name = first_name
	doc.last_name = (last_name or "").strip()
	doc.designation = (designation or "").strip()
	doc.is_primary_contact = 1 if frappe.utils.cint(is_primary) else 0
	if mobile_no:
		doc.append("phone_nos", {"phone": mobile_no, "is_primary_mobile_no": 1})
	if email_id:
		doc.append("email_ids", {"email_id": email_id, "is_primary": 1})
	doc.append("links", {"link_doctype": doctype, "link_name": name})
	doc.insert(ignore_permissions=True)
	frappe.db.commit()

	return {"contact": doc.name, "reused": False, "already_here": False}


# ── changing and removing ─────────────────────────────────────────────────────


@frappe.whitelist(methods=["POST"])
@user_rate_limit(limit=30, seconds=60)
def update_contact(
	doctype: str,
	name: str,
	contact: str,
	designation: str | None = None,
	is_primary: int | None = None,
) -> dict:
	"""What this person is to the deal, which is the part that changes.

	Each field is left alone unless it was actually sent. A blank default that writes anyway is how a
	form editing one field quietly clears another.
	"""
	_check_excom_access()
	_check_record_access(doctype, name, write=True)
	_require_linked(contact, doctype, name)

	doc = frappe.get_doc("Contact", contact)
	if designation is not None:
		doc.designation = designation.strip()
	if is_primary is not None:
		doc.is_primary_contact = 1 if frappe.utils.cint(is_primary) else 0
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	return {"ok": True}


@frappe.whitelist(methods=["POST"])
@user_rate_limit(limit=30, seconds=60)
def unlink_contact(doctype: str, name: str, contact: str) -> dict:
	"""Take a person off this deal without deleting them.

	They may still belong to another record, and their conversation certainly still exists. Removing
	the link is the reversible act; deleting the person is not, and is not offered here.
	"""
	_check_excom_access()
	_check_record_access(doctype, name, write=True)

	doc = frappe.get_doc("Contact", contact)
	keep = [
		row for row in doc.get("links", [])
		if not (row.link_doctype == doctype and row.link_name == name)
	]
	if len(keep) == len(doc.get("links", [])):
		return {"ok": True, "changed": False}

	# The rows that stay are the original rows, so nothing Frappe put on them is lost on the way.
	doc.links = keep
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	return {"ok": True, "changed": True}
