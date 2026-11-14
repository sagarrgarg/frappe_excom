"""Find people on a Lead or Prospect (HLD-009 §3.5): the button on the record's People section.

Two calls, so credits are spent only on people someone chose:

    find_people(doctype, name)        the company's people by job title (Apollo's search is free);
                                      the list is kept on the server for 30 minutes
    add_people(doctype, name, picks)  looks up the chosen ones (credits) and puts each one with an
                                      address on the record as a Contact, linked if already on file

`add_people` takes only picks from that server-kept list, never names or URLs from the browser, so
nobody can spend credits looking up whoever they like. At most 5 a call and `people_daily_per_user`
a day per person; one lookup at a time per record. Each Contact keeps the provider, date and user
who added it, for anyone who asks where we got their details.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, nowdate

from excom.excom.api.chat import _check_excom_access
from excom.excom.api.party_contacts import _check_record_access, put_on_record
from excom.excom.utils.ratelimit import user_rate_limit
from excom.excom_ai import research
from excom.excom_ai.doctype.excom_ai_company.excom_ai_company import normalise_domain

DOCTYPES = ("Lead", "Prospect")
MAX_PICKS = 5
KEEP_SECONDS = 1800
LOOKUP_SECONDS = 30 * 86400  # what a paid lookup found (or that it found no address), shared by everyone
LOCK_SECONDS = 600  # above the worst case: 5 people × 2 providers × 40 s


def _check_people_access(doctype: str, name: str) -> None:
	if doctype not in DOCTYPES:
		frappe.throw(_("People can be found for a Lead or a Prospect."))
	_check_excom_access()
	_check_record_access(doctype, name, write=True)
	if not cint(frappe.db.get_single_value("Excom AI Settings", "people_on_demand")):
		frappe.throw(_("Finding people is switched off in Excom AI Settings."))


def record_domain(doctype: str, name: str) -> str | None:
	"""The company's own web domain: its website, else a work address on the record or its contacts."""
	doc = frappe.get_doc(doctype, name)
	candidates = [doc.get("website")]
	if doctype == "Lead":
		candidates.append(doc.get("email_id"))
	else:
		for lead in frappe.get_all("Prospect Lead", {"parent": name}, pluck="lead"):
			candidates.append(frappe.db.get_value("Lead", lead, "email_id"))
	candidates += frappe.db.sql_list("""SELECT ce.email_id FROM `tabContact Email` ce JOIN `tabDynamic Link` dl
		ON dl.parent = ce.parent AND dl.parenttype = 'Contact' WHERE dl.link_doctype = %s AND dl.link_name = %s""",
		(doctype, name))
	for value in candidates:
		value = (value or "").strip().lower()
		domain = normalise_domain(value.split("@", 1)[1] if "@" in value else value)
		if domain and domain not in research.FREE_MAIL:
			return domain
	return None


def _key(doctype: str, name: str) -> str:
	return f"excom_ai_people:{frappe.session.user}:{doctype}:{name}"


def pick_id(c: dict) -> str:
	"""A candidate's own id, stable across searches, so a pick can never point at somebody else."""
	return f"{c.get('provider') or ''}:{c.get('external_id') or c.get('linkedin_url') or ''}"


def _public(c: dict) -> dict:
	looked = bool(c.get("done"))
	return {"pick": pick_id(c), "full_name": c.get("full_name") or c.get("first_name"), "title": c.get("title"),
		"seniority": c.get("seniority"), "linkedin_url": c.get("linkedin_url") or (c.get("result") or {}).get("linkedin_url"),
		"has_email": False if looked and not c.get("known_email") else c.get("has_email"),
		"provider": c.get("provider"), "known_email": c.get("known_email"), "looked_up": looked}


def _lookup_key(c: dict) -> str:
	return f"excom_ai_people_lookup:{pick_id(c)}"


def _can_see_contact(contact: str) -> bool:
	"""A Contact counts as known to this user only through a record they may read: another desk's
	contacts are theirs."""
	for link in frappe.get_all("Dynamic Link", {"parenttype": "Contact", "parent": contact},
			["link_doctype", "link_name"]):
		if frappe.has_permission(link.link_doctype, "read", doc=link.link_name):
			return True
	return False


def _known(c: dict) -> tuple[str | None, str | None]:
	"""(address, where it came from) when we already hold a usable address for this candidate: a Contact
	the user may see (same provider id or LinkedIn URL), or research the user may see whose address
	checked out."""
	from excom.excom_ai.permissions import visible_agents

	if frappe.get_meta("Contact").has_field("ai_people_id"):
		for field, value in (("ai_people_id", pick_id(c) if c.get("external_id") else None),
				("ai_linkedin_url", c.get("linkedin_url"))):
			if not value:
				continue
			for contact in frappe.get_all("Contact", {field: value}, ["name", "email_id", "ai_found_via"], limit=5):
				if contact.email_id and _can_see_contact(contact.name):
					return contact.email_id, (contact.ai_found_via or "").split(",")[0] or None
	person = research.known_person(None, c)
	if not person:
		return None, None
	p = frappe.db.get_value("Excom AI Person", person, ["email", "email_status", "status", "consent_verdict",
		"email_source", "company"], as_dict=True)
	if not p.email or p.email_status not in ("Verified", "Risky") or p.status in ("Invalid", "Excluded") \
			or p.consent_verdict == "Blocked":
		return None, None
	names = visible_agents()
	if names is not None and frappe.db.get_value("Excom AI Company", p.company, "agent") not in names:
		return None, None  # another desk's research stays theirs
	return p.email, p.email_source


@frappe.whitelist(methods=["POST"])
@user_rate_limit(limit=20, seconds=3600)
def find_people(doctype: str, name: str) -> dict:
	_check_people_access(doctype, name)
	domain = record_domain(doctype, name)
	if not domain:
		frappe.throw(_("Add the company's website (or a work email) to this {0} first.").format(_(doctype)))
	titles = research.people_titles()
	found, used = research.people_search(domain, titles, limit=25)
	from excom.excom_ai.tools import people as people_tools

	keep, seen = [], set()
	for c in people_tools.rank(found, titles):
		if research.skipped_title(c.get("title")) or pick_id(c) in seen or research.forgotten(c):
			continue
		seen.add(pick_id(c))
		c["known_email"], c["known_via"] = _known(c)
		looked = None if c["known_email"] else frappe.cache.get_value(_lookup_key(c))
		if looked:  # paid for already, by anyone, in the last 30 days
			c.update({"done": 1, "result": looked})
			if looked.get("email"):
				c.update({"known_email": looked["email"], "known_via": looked.get("email_provider") or looked.get("provider")})
		keep.append(c)
	keep = keep[:15]
	before = {pick_id(c): c for c in (frappe.cache.get_value(_key(doctype, name)) or {}).get("candidates") or []}
	for c in keep:
		old = before.get(pick_id(c))
		if old and old.get("done"):  # looked up in the last list: keep what was paid for
			c.update({k: old.get(k) for k in ("done", "result", "known_email", "known_via")})
	frappe.cache.set_value(_key(doctype, name), {"domain": domain, "candidates": keep}, expires_in_sec=KEEP_SECONDS)
	return {"domain": domain, "provider": research.PROVIDER_LABEL.get(used or ""), "candidates": [_public(c) for c in keep],
		"searched": bool(used)}


@frappe.whitelist(methods=["POST"])
@user_rate_limit(limit=20, seconds=3600)
def add_people(doctype: str, name: str, picks) -> dict:
	_check_people_access(doctype, name)
	picks = list(dict.fromkeys(str(p) for p in (frappe.parse_json(picks) if isinstance(picks, str) else picks or [])))
	if not picks:
		frappe.throw(_("Choose who to add."))
	if len(picks) > MAX_PICKS:
		frappe.throw(_("At most {0} people at a time.").format(MAX_PICKS))
	key = _key(doctype, name)
	kept = frappe.cache.get_value(key)
	if not kept:
		frappe.throw(_("The list has expired. Search again."))
	by_id = {pick_id(c): c for c in kept["candidates"]}
	if any(p not in by_id for p in picks):
		frappe.throw(_("Search again: that person is not on the list."))
	chosen = [by_id[p] for p in picks]
	paid = sum(1 for c in chosen if not c.get("known_email") and not c.get("done"))
	day_key = frappe.cache.make_key(f"excom_ai_people_day:{frappe.session.user}:{nowdate()}")
	limit = cint(frappe.db.get_single_value("Excom AI Settings", "people_daily_per_user"))
	if limit and paid:
		# counted first, so two calls at once cannot both slip under the limit
		total = frappe.cache.incrby(day_key, paid)
		frappe.cache.expire(day_key, 2 * 86400)
		if total > limit:
			frappe.cache.decrby(day_key, paid)
			frappe.throw(_("You can look up {0} people a day; {1} left today.").format(limit, max(0, limit - (total - paid))))
	lock = frappe.cache.make_key(f"excom_ai_people_lock:{doctype}:{name}")
	token = frappe.generate_hash(length=16)
	if not frappe.cache.set(lock, token, nx=True, ex=LOCK_SECONDS):
		if limit and paid:
			frappe.cache.decrby(day_key, paid)
		frappe.throw(_("Someone is already adding people here. Try again in a minute."))
	try:
		return _add(doctype, name, kept, chosen, key, day_key if limit and paid else None)
	finally:
		# only our own lock: one that expired and was taken by someone else stays theirs
		if frappe.safe_decode(frappe.cache.get(lock) or b"") == token:
			frappe.cache.delete(lock)


def _add(doctype: str, name: str, kept: dict, chosen: list[dict], key: str, day_key: str | None = None) -> dict:
	added, no_email, failed = [], [], []
	for c in chosen:
		if c.get("known_email"):
			p = {**c, "email": c["known_email"], "email_provider": c.get("known_via") or c.get("provider")}
		elif c.get("done"):
			p = c.get("result") or c  # looked up already in this list: never paid for twice
		else:
			found = research.enrich_one(c, kept["domain"], any_address=True)
			if found is None:
				# nobody answered (switched off, out of budget): nothing was paid, nothing is remembered
				if day_key:
					frappe.cache.decrby(day_key, 1)
				p = c
			else:
				p = found
				# what was paid for is kept at once, whatever happens next: with this list, and for everyone
				c.update({"done": 1, "result": p})
				if p.get("email"):
					c.update({"known_email": p["email"], "known_via": p.get("email_provider") or p.get("provider")})
				frappe.cache.set_value(key, kept, expires_in_sec=KEEP_SECONDS)
				frappe.cache.set_value(_lookup_key(c), p, expires_in_sec=LOOKUP_SECONDS)
		if research.forgotten(p):
			continue
		if not p.get("email"):
			no_email.append({"full_name": p.get("full_name"), "linkedin_url": p.get("linkedin_url")})
			continue
		via = research.PROVIDER_LABEL.get((p.get("email_provider") or p.get("provider") or "").lower()) \
			or p.get("email_provider") or p.get("provider")
		try:
			out = put_on_record(doctype, name, p.get("first_name") or p.get("full_name") or p["email"], p.get("last_name") or "",
				p.get("title") or "", "", p["email"], 0, extra={"ai_linkedin_url": p.get("linkedin_url"),
				"ai_people_id": pick_id(c) if c.get("external_id") else None,
				"ai_found_via": _("{0}, {1}, added by {2}").format(via, nowdate(), frappe.session.user)})
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title="Excom AI: could not add a person found", message=frappe.get_traceback())
			failed.append({"full_name": p.get("full_name"), "email": p["email"]})
			continue
		added.append({"contact": out["contact"], "full_name": p.get("full_name"), "email": p["email"],
			"linkedin_url": p.get("linkedin_url"), "reused": out["reused"]})
	return {"added": added, "no_email": no_email, "failed": failed}


@frappe.whitelist(methods=["POST"])
def forget_person(person: str | None = None, email: str | None = None, linkedin_url: str | None = None,
		reason: str = "") -> dict:
	"""A data-removal request (GDPR Art. 17, DPDP Act): the research record is deleted and the person's
	address, LinkedIn URL and provider ids are kept only as hashes, so neither research nor the Find
	people button finds or adds them again. CRM records (Leads, Contacts) are removed the usual way.
	Excom Admins only."""
	frappe.only_for(("Excom Admin", "System Manager"))
	from excom.excom_ai.doctype.excom_ai_people_suppression.excom_ai_people_suppression import add
	from excom.excom_ai.tools.people import linkedin

	values = {"email": email, "linkedin_url": linkedin_url}
	if person:
		row = frappe.db.get_value("Excom AI Person", person, ["email", "linkedin_url", "apollo_id", "prospeo_id"],
			as_dict=True)
		if not row:
			frappe.throw(_("No such person."), frappe.DoesNotExistError)
		values.update({k: v for k, v in row.items() if v})
	if not any(values.values()):
		frappe.throw(_("Give the person, their email or their LinkedIn URL."))
	add("Email", values.get("email"), reason)
	add("LinkedIn", linkedin(values.get("linkedin_url")) or values.get("linkedin_url"), reason)
	add("Apollo", values.get("apollo_id"), reason)
	add("Prospeo", values.get("prospeo_id"), reason)
	gone = []
	for field in ("email", "linkedin_url", "apollo_id", "prospeo_id"):
		if values.get(field):
			gone += frappe.get_all("Excom AI Person", {field: values[field]}, pluck="name")
	for name in set(gone):
		frappe.delete_doc("Excom AI Person", name, force=True, ignore_permissions=True)
	frappe.db.commit()
	return {"forgotten": len(set(gone))}
