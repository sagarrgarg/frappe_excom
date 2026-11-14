"""Research for Sales agents (HLD-007 §9, Appendix C.1), Phase 1C.

Research is not CRM: what an agent finds stays in Excom AI Company and Excom AI Person until
someone there is about to be contacted. The work is four task kinds, queued by the Sales planner
and each safe to run again:

    discover         a source (OSM, Serper, Volza…) gives companies: one row per domain,
                     excluded at once if they are a customer, a colleague's lead, do-not-contact,
                     a competitor or a group company
    profile_company  the website profiler reads the site; a fit score from the agent's scope
                     (and the model's judgement when one is set up)
    find_contacts    published addresses and, only where the domain's pattern is known, named
                     people's addresses; no guessing an address another person could own
    verify_email     the verifiers give each address an honest status; Precleared a consent verdict
    find_people      named buyers by job title from Apollo or Prospeo (HLD-009): LinkedIn URL, title
                     and, where the provider has one, a work email; once per company

`promote()` turns a ready person into a Lead and the company into a Prospect, through Excom's own
CRM gateway, so conversation, team visibility, claiming and opportunities work as for any lead.
Existing records are linked, never copied, and the exclusions are checked again first.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_days, cint, now_datetime

from excom.excom_ai import tools
from excom.excom_ai.doctype.excom_ai_company.excom_ai_company import normalise_domain
from excom.excom_ai.engine import registry
from excom.excom_ai.engine.prompts import untrusted

DISCOVERY = ("osm", "serper", "dataforseo", "scavio", "tinyfish")
VERIFIERS = ("reoon", "zerobounce", "hunter")
SOURCE_LABEL = {"osm": "OSM", "serper": "Serper", "dataforseo": "DataForSEO", "scavio": "Scavio", "tinyfish": "TinyFish",
	"volza": "Volza"}
FIT_FOR_CONTACTS = 50
PER_TICK = {"profile_company": 5, "find_contacts": 5, "verify_email": 10, "find_people": 3}
PEOPLE_PROVIDERS = ("apollo", "prospeo")  # search: Apollo's is free; Prospeo's only when Apollo is off or refused
PROVIDER_LABEL = {"apollo": "Apollo", "prospeo": "Prospeo"}
ROLE_WORDS = ("info", "sales", "ventas", "contacto", "contact", "compras", "import", "export", "comercial", "hello", "office")


# ── exclusions ───────────────────────────────────────────────────────────────


def customer_domains() -> dict[str, str]:
	"""Email and website domains of existing customers -> customer."""
	out = {}
	for r in frappe.db.sql("""SELECT dl.link_name, ce.email_id FROM `tabContact Email` ce
		JOIN `tabDynamic Link` dl ON dl.parent = ce.parent AND dl.parenttype = 'Contact' AND dl.link_doctype = 'Customer'
		WHERE IFNULL(ce.email_id, '') LIKE '%%@%%'""", as_dict=True):
		out.setdefault(r.email_id.split("@", 1)[1].lower(), r.link_name)
	for r in frappe.get_all("Customer", {"website": ["is", "set"]}, ["name", "website"]):
		d = normalise_domain(r.website)
		if d:
			out.setdefault(d, r.name)
	return out


FREE_MAIL = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "live.com", "icloud.com", "aol.com", "protonmail.com",
	"yahoo.co.in", "rediffmail.com", "gmx.com", "yandex.com", "mail.com"}


# Where an address counts as a named person's own, for an agent with "Email only named people" ticked.
PEOPLE_FINDER_SOURCES = ("Apollo", "Prospeo")


def people_finder_only(agent) -> bool:
	return bool(agent and cint(agent.get("people_finder_only")))


def not_a_named_person(email: str | None, person=None) -> str | None:
	"""Why this address is not a person's own address from the People Finder (a shared inbox, a guessed or
	published address, or nobody we know); None when it is. Used only for agents with the box ticked."""
	from excom.excom_ai.tools.email import is_role

	email = (email or "").strip().lower()
	if not email:
		return _("no address")
	if is_role(email):
		return _("{0} is a shared inbox, not a person").format(email)
	if person is None or isinstance(person, str):
		person = frappe.db.get_value("Excom AI Person", person or {"email": email},
			["is_inbox", "seniority", "email_source"], as_dict=True)
	if not person:
		return _("{0} was not found by the People Finder").format(email)
	if cint(person.get("is_inbox")) or person.get("seniority") == "Inbox":
		return _("{0} is a shared inbox, not a person").format(email)
	if person.get("email_source") not in PEOPLE_FINDER_SOURCES:
		return _("{0} came from {1}, not the People Finder (Apollo or Prospeo)").format(
			email, person.get("email_source") or _("an unknown source"))
	return None


def exclusion(domain: str | None, emails: list[str] | None = None, agent: str | None = None) -> tuple[str | None, str | None]:
	"""(reason, customer) when a company must not be written to cold; (None, None) when it may."""
	from excom.excom_ai.service_user import is_service_user

	domain = normalise_domain(domain)
	emails = [e.lower() for e in emails or [] if e]
	if domain and domain not in FREE_MAIL:
		customer = customer_domains().get(domain)
		if customer:
			return "Existing customer", customer
		for d in frappe.get_single("Excom AI Settings").dnc_domains or []:
			if domain == d.domain or domain.endswith("." + d.domain):
				return ("Competitor" if d.reason == "Competitor" else "Group company" if d.reason == "Group company"
					else "Do not contact"), None
		fields = ["name", "lead_owner"] + (["ai_agent"] if frappe.get_meta("Lead").has_field("ai_agent") else [])
		leads = frappe.get_all("Lead", {"email_id": ["like", f"%@{domain}"], "status": ["not in", ("Converted", "Do Not Contact")]},
			fields)
		# A lead this same agent made (promoted from its research) is its own, not a colleague's.
		if any(lead.lead_owner and not is_service_user(lead.lead_owner) and not (agent and lead.get("ai_agent") == agent)
				for lead in leads):
			return "Open lead or deal (people)", None
	for e in emails:
		if frappe.db.exists("Omni Identity", {"primary_email": e, "do_not_contact": 1}):
			return "Do not contact", None
	return None, None


def exclude(company, reason: str, customer: str | None = None) -> None:
	company.db_set({"status": "Excluded", "exclusion_reason": reason, "customer": customer}, update_modified=False)


# ── discover ─────────────────────────────────────────────────────────────────


def query_for(agent, source: str) -> dict:
	"""What to look for, from the agent's scope: its products' words, buyer types and markets."""
	words = []
	for row in agent.keyword_rows or []:
		if row.included and row.resolved_as in ("Vocabulary", "AI proposal") and row.link_doctype in ("Item Group", "Brand", "Item"):
			words.append(row.keyword)
	words = words or [p.item_group for p in agent.products or [] if p.item_group]
	buyer = [w for w in ("importer", "distributor", "wholesaler") if w in (agent.audience or "").lower()] or ["importer", "distributor"]
	return {"keywords": words, "buyer_types": buyer, "countries": [m.country for m in agent.markets or [] if m.country],
		"languages": sorted({m.language for m in agent.markets or [] if m.language}), "category": ", ".join(words),
		"limit": 25, "source": source}


@registry.task_kind("discover", priority=registry.PRIORITY["discover"], effort="classify")
def discover(task, agent) -> str:
	params = frappe.parse_json(task.params or "{}") or {}
	source = params.get("source") or "osm"
	query = params.get("query") or query_for(agent, source)
	if source == "volza":
		found = tools.use("volza", "import_file", file_url=params["file_url"])
	else:
		from excom.excom_ai.tools import discovery as discovery_tools

		# a paid search runs several queries: charge what it will really cost
		found = tools.use(source, "search", query=query, cost=discovery_tools.estimate(source, query))
	new = known = excluded = 0
	for c in found or []:
		name, created = upsert_company(c, agent, task, source)
		if not name:
			continue
		new += created
		known += not created
		company = frappe.get_doc("Excom AI Company", name)
		if company.status == "Excluded":
			excluded += 1
		elif created:
			from excom.excom_ai.engine import tasks

			tasks.add(agent.name, "profile_company", subject=("Excom AI Company", name))
	return _("{0} from {1}: {2} new, {3} already known, {4} excluded").format(len(found or []), source, new, known, excluded)


def upsert_company(c: dict, agent, task, source: str) -> tuple[str | None, bool]:
	"""One row per domain (or external id) across all agents. Returns (name, created)."""
	domain = normalise_domain(c.get("domain") or c.get("website"))
	if not domain and not c.get("external_id"):
		return None, False
	existing = (frappe.db.get_value("Excom AI Company", {"domain": domain}) if domain else None) or (
		frappe.db.get_value("Excom AI Company", {"external_id": c.get("external_id")}) if c.get("external_id") else None)
	if existing:
		doc = frappe.get_doc("Excom AI Company", existing)
		if not doc.agent:
			doc.db_set("agent", agent.name, update_modified=False)  # unclaimed: this agent takes it
		_observe(doc.name, None, c.get("signals"), source, task, c.get("source_url"))
		return existing, False
	country = c.get("country") if c.get("country") and frappe.db.exists("Country", c.get("country")) else None
	doc = frappe.get_doc({
		"doctype": "Excom AI Company", "company_name": (c.get("name") or domain or "?")[:140], "domain": domain,
		"external_id": c.get("external_id"), "website": c.get("website"), "country": country, "city": c.get("city"),
		"location_text": c.get("location_text") or ", ".join(filter(None, (c.get("city"), c.get("country")))),
		"phone": c.get("phone"), "industry": c.get("industry"), "buyer_type": c.get("buyer_type") or None,
		"source": SOURCE_LABEL.get(source, "Import"), "source_url": c.get("source_url"), "found_by_task": task.name if task else None,
		"agent": agent.name, "status": "New",
	})
	reason, customer = exclusion(domain)
	if reason:
		doc.update({"status": "Excluded", "exclusion_reason": reason, "customer": customer})
	try:
		doc.insert(ignore_permissions=True)
	except frappe.UniqueValidationError:
		frappe.clear_last_message()
		return (frappe.db.get_value("Excom AI Company", {"domain": domain}) if domain else None), False
	_observe(doc.name, None, c.get("signals"), source, task, c.get("source_url"))
	return doc.name, True


def _observe(company: str | None, person: str | None, signals: dict | None, collector: str, task, url: str | None = None) -> None:
	"""Record signals the agent may collect (Confirmed ones); the rest are dropped, as people decided."""
	confirmed = set(frappe.get_all("Excom AI Signal Code", {"status": "Confirmed"}, pluck="name"))
	for code, value in (signals or {}).items():
		if code not in confirmed or value in (None, ""):
			continue
		frappe.get_doc({"doctype": "Excom AI Observation", "company": company, "person": person, "signal": code,
			"collector": collector, "value_num": value if isinstance(value, int | float) and not isinstance(value, bool) else None,
			"value_text": None if isinstance(value, int | float) and not isinstance(value, bool) else str(value)[:500],
			"confidence": 0.8, "evidence_url": url, "task": task.name if task else None}).insert(ignore_permissions=True)
	if company and signals:
		latest = {}
		for o in frappe.get_all("Excom AI Observation", {"company": company}, ["signal", "value_text", "value_num"],
				order_by="observed_on asc"):
			latest[o.signal] = o.value_text if o.value_text is not None else o.value_num
		frappe.db.set_value("Excom AI Company", company, "signals", frappe.as_json(latest), update_modified=False)


# ── profile ──────────────────────────────────────────────────────────────────

FIT_SCHEMA = {
	"type": "object",
	"properties": {
		"fit_score": {"type": "integer"},
		"buyer_type": {"type": "string", "enum": ["Importer", "Distributor", "Wholesaler", "Retailer", "Online store",
			"Manufacturer", "Other"]},
		"size_band": {"type": "string", "enum": ["1-10", "11-50", "51-200", "201-1000", "1000+", ""]},
		"notes": {"type": "string"},
	},
	"required": ["fit_score", "buyer_type", "size_band", "notes"],
	"additionalProperties": False,
}


@registry.task_kind("profile_company", priority=registry.PRIORITY["research"], effort="classify")
def profile_company(task, agent) -> str:
	company = frappe.get_doc("Excom AI Company", task.subject_name)
	if company.status not in ("New", "Profiled"):
		return _("{0} is {1}").format(company.company_name, company.status)
	try:
		facts = tools.use("web_reader", "profile", url=company.website) if company.website else {}
	except Exception as e:
		# a site the safety guard refuses, or one that does not answer: noted, not fatal
		company.db_set({"status": "Profiled", "fit_score": 0, "profile_checked_on": now_datetime(),
			"research_notes": _("The website could not be read: {0}").format(str(e)[:200])}, update_modified=False)
		return _("{0}: website not readable").format(company.company_name)
	reason, customer = exclusion(company.domain, facts.get("emails"))
	if reason:
		exclude(company, reason, customer)
		return _("{0}: excluded ({1})").format(company.company_name, reason)
	score, notes, buyer, size = _fit(agent, company, facts, task)
	language = (facts.get("language") or "")[:2] or None
	company.db_set({
		"website_facts": frappe.as_json({k: facts.get(k) for k in ("title", "description", "emails", "phones",
			"has_online_store", "links", "language")}),
		"description": (facts.get("description") or company.description or "")[:500] or None,
		"language": language if language and frappe.db.exists("Language", language) else company.language,
		"fit_score": score, "research_notes": notes, "buyer_type": buyer or company.buyer_type, "size_band": size or company.size_band,
		"profile_checked_on": now_datetime(), "status": "Profiled",
	}, update_modified=False)
	_observe(company.name, None, {"WEBSITE_LANGUAGE": language, "HAS_ONLINE_STORE": 1 if facts.get("has_online_store") else None,
		"BUYER_TYPE": buyer, "PUBLISHED_INBOX": ", ".join(facts.get("emails") or [])[:200] or None}, "web_reader", task,
		company.website)
	if score >= FIT_FOR_CONTACTS:
		from excom.excom_ai.engine import tasks

		tasks.add(agent.name, "find_contacts", subject=("Excom AI Company", company.name))
	return _("{0}: fit {1}").format(company.company_name, score)


def _fit(agent, company, facts: dict, task) -> tuple[int, str, str | None, str | None]:
	"""A fit score from the agent's scope; the model's judgement when it is set up."""
	q = query_for(agent, "")
	text = " ".join(filter(None, [facts.get("title"), facts.get("description"), facts.get("text")])).lower()
	score = 0
	why = []
	if company.country and company.country in q["countries"]:
		score += 30
		why.append(_("in a target market"))
	if any(w.lower() in text for w in q["keywords"]):
		score += 25
		why.append(_("sells the category"))
	if company.buyer_type in ("Importer", "Distributor", "Wholesaler") or any(w in text for w in ("import", "distribu", "mayorista", "wholesale")):
		score += 25
		why.append(_("trade buyer"))
	if facts.get("emails"):
		score += 15
		why.append(_("a published address"))
	if facts.get("has_online_store"):
		score += 5
	notes, buyer, size = "; ".join(why), company.buyer_type, company.size_band
	from excom.excom_ai.builder.brief import ai_ready

	if ai_ready() and text:
		from excom.excom_ai.engine import runner

		try:
			r = runner.ai(task, agent, prompt=_("Does this company fit the agent's audience? Score 0-100.") + "\n\n"
				+ untrusted(text[:6000], company.website or company.company_name), output_schema=FIT_SCHEMA)
			score, notes = max(0, min(100, int(r.data["fit_score"]))), r.data["notes"][:1000]
			buyer, size = r.data["buyer_type"], r.data["size_band"] or size
		except Exception:
			frappe.log_error(title="Excom AI: fit scoring by the model failed", message=frappe.get_traceback())
	return min(score, 100), notes, buyer, size


# ── contacts ─────────────────────────────────────────────────────────────────


@registry.task_kind("find_contacts", priority=registry.PRIORITY["research"], effort="classify")
def find_contacts(task, agent) -> str:
	company = frappe.get_doc("Excom AI Company", task.subject_name)
	if company.status in ("Excluded", "Promoted") or not company.domain:
		return _("{0} is {1}").format(company.company_name, company.status)
	facts = frappe.parse_json(company.website_facts or "{}") or {}
	known = []
	if tools.available("hunter"):
		from excom.excom_ai.tools import email as email_tools

		try:
			known = tools.use("hunter", "domain_search", domain=company.domain, cost=email_tools.HUNTER_SEARCH_COST) or []
		except tools.ToolRefused:
			known = []
	# Hunter's named addresses are evidence for the domain's pattern, and come back among the addresses.
	found = tools.use("email_finder", "find", domain=company.domain, published=facts.get("emails") or [], people=[], known=known)
	addresses = list(found.get("addresses") or [])
	try:
		from excom.excom_ai.tools import email as email_tools

		email_tools.save_domain(found)
	except Exception:
		frappe.log_error(title="Excom AI: could not save the email domain facts", message=frappe.get_traceback())
	made = 0
	for a in addresses:
		email = (a.get("email") or "").lower().strip()
		if not email or frappe.db.exists("Excom AI Person", {"email": email}):
			continue
		local = email.split("@", 1)[0]
		inbox = bool(a.get("is_inbox")) or local in ROLE_WORDS
		frappe.get_doc({"doctype": "Excom AI Person", "company": company.name, "email": email,
			"first_name": a.get("first_name"), "last_name": a.get("last_name"), "title": a.get("title"), "is_inbox": inbox,
			"email_source": {"Published": "Published", "Pattern": "Pattern", "Hunter": "Hunter"}.get(a.get("source"), "Published"),
			"email_status": "Guess" if a.get("source") == "Pattern" else "Unknown", "language": company.language,
			"linkedin_url": a.get("linkedin_url") if frappe.get_meta("Excom AI Person").has_field("linkedin_url") else None,
			"status": "New"}).insert(ignore_permissions=True)
		made += 1
	company.db_set({"status": "Contacts found" if made or frappe.db.exists("Excom AI Person", {"company": company.name})
		else "No contacts", "contacts_checked_on": now_datetime()}, update_modified=False)
	from excom.excom_ai.engine import tasks

	_verify_new(agent, company.name)
	_observe(company.name, None, {"CONTACT_FOUND": 1 if made else None}, "email_finder", task)
	return _("{0}: {1} addresses").format(company.company_name, made)


@registry.task_kind("verify_email", priority=registry.PRIORITY["check"], effort="classify")
def verify_email(task, agent) -> str:
	person = frappe.get_doc("Excom AI Person", task.subject_name)
	if not person.email:
		return _("{0}: no address to check").format(person.full_name or person.name)
	result, used = {"status": "Unknown"}, None
	for key in VERIFIERS:
		if not tools.available(key):
			continue
		try:
			result, used = tools.use(key, "verify", email=person.email), key
			break
		except tools.ToolRefused:
			continue
	status = result.get("status") or "Unknown"
	if status == "Unknown" and person.email_status == "Guess":
		status = "Guess"  # a pattern guess no verifier confirmed stays a guess: honest, not promoted to verified
	by = {"reoon": "Reoon", "zerobounce": "ZeroBounce", "hunter": "Hunter"}.get(used, "None")
	if not used and person.email_source in ("Apollo", "Prospeo") and person.provider_email_status in ("Verified", "Risky"):
		# none of our verifiers answered: the provider's own check counts, and says whose it was
		status, by, used = person.provider_email_status, person.email_source, person.email_source.lower()
	values = {"email_status": status, "verified_on": now_datetime() if used else None,
		"verified_by": by,
		"catch_all": 1 if result.get("catch_all") else 0}
	country = frappe.db.get_value("Excom AI Company", person.company, "country")
	if tools.available("precleared"):
		try:
			c = tools.use("precleared", "check", email=person.email, country=country)
			values.update({"consent_verdict": c.get("verdict") or "Unknown", "consent_checked_on": now_datetime()})
		except tools.ToolRefused:
			pass
	ready = status in ("Verified", "Risky") and values.get("consent_verdict", person.consent_verdict) != "Blocked"
	if ready and people_finder_only(agent) and not_a_named_person(person.email, person):
		ready = False  # kept for reference; this agent writes only to named people from the People Finder
	values["status"] = "Invalid" if status == "Invalid" else "Ready" if ready else person.status
	person.db_set(values, update_modified=False)
	_observe(person.company, person.name, {"EMAIL_STATUS": status}, used or "none", task)
	return _("{0}: {1}").format(person.email, status)


def _verify_new(agent, company: str) -> None:
	from excom.excom_ai.engine import tasks

	for p in frappe.get_all("Excom AI Person", {"company": company, "email_status": ["in", ("Unknown", "Guess")],
			"email": ["is", "set"]}, pluck="name"):
		tasks.add(agent.name, "verify_email", subject=("Excom AI Person", p))


# ── named people: Apollo, Prospeo (HLD-009) ──────────────────────────────────


def _words(field: str) -> list[str]:
	import re

	return [w.strip() for w in re.split(r"[,\n]", frappe.db.get_single_value("Excom AI Settings", field) or "") if w.strip()]


def people_titles() -> list[str]:
	return _words("people_titles")


def skipped_title(title: str | None) -> bool:
	"""A title to skip, by whole words: "intern" skips "Marketing Intern", never "International Buyer"."""
	import re

	t = (title or "").lower()
	return any(re.search(rf"(?<!\w){re.escape(w.lower())}(?!\w)", t) for w in _words("people_exclude_titles"))


def people_search(domain: str, titles: list[str], limit: int = 10) -> tuple[list[dict], str | None]:
	"""Candidates from the first provider that answers. Apollo's search is free (cost 0, so a nearly
	spent budget does not refuse it); a refusal (off, no key, budget, 403 without a master key, 429)
	falls through to Prospeo. Nothing raises: research carries on with what it has."""
	for key in PEOPLE_PROVIDERS:
		if not tools.available(key):
			continue
		try:
			return tools.use(key, "search", domain=domain, titles=titles, limit=limit,
				cost=0 if key == "apollo" else None) or [], key
		except tools.ToolRefused:
			continue
		except Exception:
			frappe.log_error(title=f"Excom AI: {PROVIDER_LABEL[key]} search failed", message=frappe.get_traceback())
			continue
		finally:
			frappe.db.commit()  # what was spent stays counted, whatever happens next
	return [], None


def known_person(company: str | None, c: dict) -> str | None:
	"""The Excom AI Person this candidate already is: by provider id, LinkedIn URL or email (never an
	empty value), or, for Apollo's search (last name hidden), the same first name and title here."""
	for field, value in (("apollo_id", c.get("external_id") if c.get("provider") == "Apollo" else None),
			("prospeo_id", c.get("external_id") if c.get("provider") == "Prospeo" else None),
			("linkedin_url", c.get("linkedin_url")), ("email", c.get("email"))):
		if value:
			name = frappe.db.get_value("Excom AI Person", {field: value})
			if name:
				return name
	if company and c.get("first_name") and c.get("title") and c.get("provider") == "Apollo":
		# Apollo's search hides the last name: the same first name and title here, found some other way
		# (a person already found through Apollo has an id, and a different id is a different person)
		return frappe.db.get_value("Excom AI Person", {"company": company, "first_name": c["first_name"], "title": c["title"],
			"apollo_id": ["is", "not set"]})
	return None


def pick(candidates: list[dict], titles: list[str], n: int, company: str | None = None) -> list[dict]:
	from excom.excom_ai.tools import people as people_tools

	keep = [c for c in candidates if not skipped_title(c.get("title")) and c.get("has_email") is not False
		and not forgotten(c) and not known_person(company, c)]
	return people_tools.rank(keep, titles)[: max(0, n)]


def forgotten(p: dict) -> bool:
	"""Someone who asked to be forgotten (Excom AI People Suppression): never found or added again."""
	from excom.excom_ai.doctype.excom_ai_people_suppression.excom_ai_people_suppression import suppressed

	ids = list((p.get("ids") or {}).values())
	return suppressed(p.get("email"), p.get("linkedin_url"), p.get("external_id"), *ids)


def enrich_one(c: dict, domain: str, any_address: bool = False) -> dict | None:
	"""The provider that found the person first (by its id), then the other by LinkedIn URL when there
	is still no verified address (`any_address`: no address at all, so a person costs one credit, as
	the Find people button promises). Each paid call is committed as it happens. Never raises."""
	best = None
	order = [k for k in PEOPLE_PROVIDERS if PROVIDER_LABEL[k] == c.get("provider")] + [
		k for k in PEOPLE_PROVIDERS if PROVIDER_LABEL[k] != c.get("provider")]
	for key in order:
		if best and (best.get("email_status") == "Verified" or (any_address and best.get("email"))):
			break
		if not tools.available(key):
			continue
		if PROVIDER_LABEL[key] == c.get("provider") and c.get("external_id"):
			kwargs = {"external_id": c["external_id"]}
		elif (best or c).get("linkedin_url"):
			kwargs = {"linkedin_url": (best or c)["linkedin_url"]}
		else:
			continue  # Apollo's search hides last names: nothing to ask the other provider with
		try:
			found = tools.use(key, "enrich", domain=domain, **kwargs)
		except tools.ToolRefused:
			continue
		except Exception:
			frappe.log_error(title=f"Excom AI: {PROVIDER_LABEL[key]} lookup failed", message=frappe.get_traceback())
			continue
		finally:
			frappe.db.commit()
		if found:
			best = _merge(best, found)
	return best


def _merge(best: dict | None, found: dict) -> dict:
	if not best:
		out = dict(found)
	else:
		out = dict(best)
		for k, v in found.items():
			if v and not out.get(k):
				out[k] = v
		if found.get("email") and found.get("email_status") == "Verified" and out.get("email_status") != "Verified":
			out.update({"email": found["email"], "email_status": "Verified", "provider": found["provider"]})
	ids = dict(best.get("ids") or {}) if best else {}
	if found.get("external_id"):
		ids[found["provider"]] = found["external_id"]
	out["ids"] = ids
	if found.get("email") and out.get("email") == found["email"]:
		out["email_provider"] = found["provider"]
	return out


def save_person(company, p: dict) -> tuple[str | None, bool]:
	"""Update the person we already have (found by id, LinkedIn or address) or add them. Returns
	(name, created). A person without an address is kept for LinkedIn and never written to."""
	if (not p.get("email") and not p.get("linkedin_url")) or forgotten(p):
		return None, False
	ids = p.get("ids") or {}
	values = {"apollo_id": ids.get("Apollo"), "prospeo_id": ids.get("Prospeo"), "linkedin_url": p.get("linkedin_url"),
		"title": p.get("title"), "seniority": p.get("seniority"), "first_name": p.get("first_name"),
		"last_name": p.get("last_name")}
	existing = None
	for field, value in (("apollo_id", ids.get("Apollo")), ("prospeo_id", ids.get("Prospeo")),
			("linkedin_url", p.get("linkedin_url")), ("email", p.get("email"))):
		existing = value and frappe.db.get_value("Excom AI Person", {field: value})  # never an empty filter
		if existing:
			break
	if existing:
		doc = frappe.get_doc("Excom AI Person", existing)
		changed = {k: v for k, v in values.items() if v and not doc.get(k)}
		if p.get("email") and not doc.email and not frappe.db.exists("Excom AI Person", {"email": p["email"]}):
			changed.update(_email_values(p))
		if changed:
			doc.db_set(changed, update_modified=False)
		return existing, False
	source = PROVIDER_LABEL.get((p.get("email_provider") or p.get("provider") or "").lower()) or p.get("provider")
	doc = frappe.get_doc({"doctype": "Excom AI Person", "company": company.name, "language": company.language,
		"status": "New", "source": source, "source_url": p.get("linkedin_url"), **{k: v for k, v in values.items() if v},
		**(_email_values(p) if p.get("email") else {"email_status": "Unknown"})})
	doc.insert(ignore_permissions=True)
	return doc.name, True


def _email_values(p: dict) -> dict:
	provider = p.get("email_provider") or p.get("provider")
	return {"email": p["email"], "email_source": provider, "email_status": "Unknown",
		"provider_email_status": p.get("email_status") or "Unknown"}


@registry.task_kind("find_people", priority=registry.PRIORITY["research"], effort="classify")
def find_people(task, agent) -> str:
	"""Named buyers at one good-fit company, once. Marked searched before anything is spent, so a
	failure is never searched (and paid for) again; each person is saved as soon as they are found."""
	company = frappe.get_doc("Excom AI Company", task.subject_name)
	if company.status in ("Excluded", "Promoted") or not company.domain or company.company_stop:
		return _("{0} is {1}").format(company.company_name, company.status)
	if company.people_checked_on:
		return _("{0}: people already searched").format(company.company_name)
	reason, customer = exclusion(company.domain, None, agent.name)
	if reason:
		exclude(company, reason, customer)
		return _("{0} excluded: {1}").format(company.company_name, reason)
	n = cint(frappe.db.get_single_value("Excom AI Settings", "people_per_company"))
	if n <= 0:
		return _("People finder is off (People Per Company is 0)")
	company.db_set("people_checked_on", now_datetime(), update_modified=False)
	frappe.db.commit()
	titles = people_titles()
	found, used = people_search(company.domain, titles, limit=10)
	made = linked = 0
	for c in pick(found, titles, n, company.name):
		p = enrich_one(c, company.domain)
		if not p and c.get("linkedin_url"):
			p = _merge(None, c)  # nothing more from the providers, but the LinkedIn link is worth keeping
		if not p:
			continue
		try:
			name, created = save_person(company, p)
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title="Excom AI: could not save a person found", message=frappe.get_traceback())
			continue
		frappe.db.commit()
		made += created
		linked += bool(name and not created)
	if made and company.status == "No contacts":
		company.db_set("status", "Contacts found", update_modified=False)
	_verify_new(agent, company.name)
	_observe(company.name, None, {"CONTACT_FOUND": 1 if made else None}, used or "none", task)
	return _("{0}: {1} people found by {2}, {3} already known").format(company.company_name, made,
		PROVIDER_LABEL.get(used or "", _("nobody")), linked)


# ── promotion: research becomes CRM ──────────────────────────────────────────


def promote(person_name: str, agent) -> dict:
	"""The person becomes a Lead and the company a Prospect, ready for step 1 of a sequence.
	Existing records are linked, not copied; an exclusion found now stops it."""
	from excom.excom.services import crm_gateway

	person = frappe.get_doc("Excom AI Person", person_name)
	company = frappe.get_doc("Excom AI Company", person.company)
	if person.lead:
		return {"lead": person.lead, "prospect": company.prospect, "created": False}
	if not person.email:
		frappe.throw(_("{0} has no email address yet: write to them on LinkedIn, or add an address first.").format(
			person.full_name or person.name))
	reason, customer = exclusion(company.domain, [person.email], agent.name)
	if reason or company.status == "Excluded" or company.company_stop:
		if reason:
			exclude(company, reason, customer)
		frappe.throw(_("{0} is not written to: {1}").format(company.company_name, reason or company.exclusion_reason
			or company.stop_reason or _("company stop")))
	if person.email_status in ("Invalid",) or person.consent_verdict == "Blocked":
		frappe.throw(_("{0} cannot be written to ({1}).").format(person.email, person.email_status))
	owner = agent.acting_owner or agent.owner_user
	lead = frappe.db.get_value("Lead", {"email_id": person.email})
	created = False
	if not lead:
		ref = crm_gateway.create_lead({
			"name": person.full_name if not person.is_inbox else company.company_name, "email": person.email,
			"phone": person.phone, "company_name": company.company_name, "company": agent.company,
			"country": company.country, "city": company.city, "owner": owner,
			"customer_type": (agent.customer_types or "").split(",")[0].strip() or None,
			"intake_source": None, "first_touch_channel": "Manual", "first_touch_by": agent.service_user,
			"source_reference": f"Excom AI Company {company.name}",
		}, ignore_permissions=True)
		lead, created = ref.name, True
	values = {"ai_agent": agent.name, "ai_person": person.name, "ai_email_status": person.email_status
		if person.email_status in ("Verified", "Risky", "Guess", "Invalid") else None, "ai_language": person.language}
	if frappe.get_meta("Lead").has_field("excom_team") and agent.team:
		values["excom_team"] = agent.team
	frappe.db.set_value("Lead", lead, {k: v for k, v in values.items() if frappe.get_meta("Lead").has_field(k)},
		update_modified=False)
	prospect = company.prospect or _prospect(company, agent, owner)
	if prospect and not frappe.db.exists("Prospect Lead", {"parent": prospect, "lead": lead}):
		p = frappe.get_doc("Prospect", prospect)
		p.append("leads", {"lead": lead})
		p.flags.ignore_permissions = True
		p.save(ignore_permissions=True)
	person.db_set({"lead": lead, "status": "Promoted"}, update_modified=False)
	company.db_set({"status": "Promoted", "prospect": prospect}, update_modified=False)
	return {"lead": lead, "prospect": prospect, "created": created}


def _prospect(company, agent, owner: str) -> str:
	existing = frappe.db.get_value("Prospect", {"company_name": company.company_name})
	if existing:
		return existing
	doc = frappe.get_doc({"doctype": "Prospect", "company_name": company.company_name, "website": company.website,
		"industry": None, "company": agent.company or frappe.defaults.get_global_default("company"), "prospect_owner": owner})
	for f, v in (("ai_agent", agent.name), ("ai_research", company.name), ("ai_fit_score", company.fit_score)):
		if frappe.get_meta("Prospect").has_field(f):
			doc.set(f, v)
	doc.insert(ignore_permissions=True)
	if company.country or company.city:
		frappe.get_doc({"doctype": "Address", "address_title": company.company_name, "address_type": "Office",
			"address_line1": company.location_text or company.city or company.country, "city": company.city or "-",
			"country": company.country or frappe.db.get_value("Company", agent.company, "country") or "India",
			"links": [{"link_doctype": "Prospect", "link_name": doc.name}]}).insert(ignore_permissions=True)
	return doc.name


# ── the Sales planner ────────────────────────────────────────────────────────


@registry.planner("sales")
def plan(agent) -> list[dict]:
	"""Research work for a working agent: a discovery run per source a day, then profiles,
	contacts and checks, a few each tick. Excluded and promoted companies need nothing."""
	specs = []
	today = frappe.utils.nowdate()
	for source in DISCOVERY:
		if tools.available(source):
			specs.append({"kind": "discover", "dedupe_key": f"discover:{source}:{today}",
				"params": {"source": source, "query": query_for(agent, source)}})
	for c in frappe.get_all("Excom AI Company", {"agent": agent.name, "status": "New"}, pluck="name",
			limit=PER_TICK["profile_company"]):
		specs.append({"kind": "profile_company", "subject": ("Excom AI Company", c)})
	for c in frappe.get_all("Excom AI Company", {"agent": agent.name, "status": "Profiled",
			"fit_score": [">=", FIT_FOR_CONTACTS], "contacts_checked_on": ["is", "not set"]}, pluck="name",
			limit=PER_TICK["find_contacts"]):
		specs.append({"kind": "find_contacts", "subject": ("Excom AI Company", c)})
	if cint(frappe.db.get_single_value("Excom AI Settings", "people_per_company")) > 0 and any(
			tools.available(k) for k in PEOPLE_PROVIDERS):
		# after the free addresses are in (contacts_checked_on), so the two steps never race
		for c in frappe.get_all("Excom AI Company", {"agent": agent.name, "status": ["in", ("Contacts found", "No contacts")],
				"fit_score": [">=", FIT_FOR_CONTACTS], "contacts_checked_on": ["is", "set"],
				"people_checked_on": ["is", "not set"], "domain": ["is", "set"]}, pluck="name", limit=PER_TICK["find_people"]):
			specs.append({"kind": "find_people", "subject": ("Excom AI Company", c)})
	since = add_days(now_datetime(), -30)
	for p in frappe.db.sql("""SELECT p.name FROM `tabExcom AI Person` p JOIN `tabExcom AI Company` c ON c.name = p.company
		WHERE c.agent = %s AND p.email_status IN ('Unknown', 'Guess') AND p.status = 'New' AND IFNULL(p.email, '') != ''
		AND (p.verified_on IS NULL OR p.verified_on < %s) LIMIT %s""", (agent.name, since, PER_TICK["verify_email"])):
		specs.append({"kind": "verify_email", "subject": ("Excom AI Person", p[0])})
	return specs


def summary(agent_name: str) -> dict:
	"""Counts for the agent page: what research has found so far."""
	by_status = dict(frappe.db.sql("SELECT status, COUNT(*) FROM `tabExcom AI Company` WHERE agent = %s GROUP BY status",
		agent_name))
	people = dict(frappe.db.sql("""SELECT p.email_status, COUNT(*) FROM `tabExcom AI Person` p JOIN `tabExcom AI Company` c
		ON c.name = p.company WHERE c.agent = %s AND IFNULL(p.email, '') != '' GROUP BY p.email_status""", agent_name))
	linkedin_only = frappe.db.sql("""SELECT COUNT(*) FROM `tabExcom AI Person` p JOIN `tabExcom AI Company` c
		ON c.name = p.company WHERE c.agent = %s AND IFNULL(p.email, '') = '' AND IFNULL(p.linkedin_url, '') != ''""",
		agent_name)[0][0]
	return {"companies": by_status, "people": people, "linkedin_only": cint(linkedin_only),
		"total": cint(sum(by_status.values()))}
