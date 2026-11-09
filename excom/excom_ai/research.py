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
PER_TICK = {"profile_company": 5, "find_contacts": 5, "verify_email": 10}
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

	for p in frappe.get_all("Excom AI Person", {"company": company.name, "email_status": ["in", ("Unknown", "Guess")]}, pluck="name"):
		tasks.add(agent.name, "verify_email", subject=("Excom AI Person", p))
	_observe(company.name, None, {"CONTACT_FOUND": 1 if made else None}, "email_finder", task)
	return _("{0}: {1} addresses").format(company.company_name, made)


@registry.task_kind("verify_email", priority=registry.PRIORITY["check"], effort="classify")
def verify_email(task, agent) -> str:
	person = frappe.get_doc("Excom AI Person", task.subject_name)
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
	values = {"email_status": status, "verified_on": now_datetime() if used else None,
		"verified_by": {"reoon": "Reoon", "zerobounce": "ZeroBounce", "hunter": "Hunter"}.get(used, "None"),
		"catch_all": 1 if result.get("catch_all") else 0}
	country = frappe.db.get_value("Excom AI Company", person.company, "country")
	if tools.available("precleared"):
		try:
			c = tools.use("precleared", "check", email=person.email, country=country)
			values.update({"consent_verdict": c.get("verdict") or "Unknown", "consent_checked_on": now_datetime()})
		except tools.ToolRefused:
			pass
	ready = status in ("Verified", "Risky") and values.get("consent_verdict", person.consent_verdict) != "Blocked"
	values["status"] = "Invalid" if status == "Invalid" else "Ready" if ready else person.status
	person.db_set(values, update_modified=False)
	_observe(person.company, person.name, {"EMAIL_STATUS": status}, used or "none", task)
	return _("{0}: {1}").format(person.email, status)


# ── promotion: research becomes CRM ──────────────────────────────────────────


def promote(person_name: str, agent) -> dict:
	"""The person becomes a Lead and the company a Prospect, ready for step 1 of a sequence.
	Existing records are linked, not copied; an exclusion found now stops it."""
	from excom.excom.services import crm_gateway

	person = frappe.get_doc("Excom AI Person", person_name)
	company = frappe.get_doc("Excom AI Company", person.company)
	if person.lead:
		return {"lead": person.lead, "prospect": company.prospect, "created": False}
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
	since = add_days(now_datetime(), -30)
	for p in frappe.db.sql("""SELECT p.name FROM `tabExcom AI Person` p JOIN `tabExcom AI Company` c ON c.name = p.company
		WHERE c.agent = %s AND p.email_status IN ('Unknown', 'Guess') AND p.status = 'New'
		AND (p.verified_on IS NULL OR p.verified_on < %s) LIMIT %s""", (agent.name, since, PER_TICK["verify_email"])):
		specs.append({"kind": "verify_email", "subject": ("Excom AI Person", p[0])})
	return specs


def summary(agent_name: str) -> dict:
	"""Counts for the agent page: what research has found so far."""
	by_status = dict(frappe.db.sql("SELECT status, COUNT(*) FROM `tabExcom AI Company` WHERE agent = %s GROUP BY status",
		agent_name))
	people = dict(frappe.db.sql("""SELECT p.email_status, COUNT(*) FROM `tabExcom AI Person` p JOIN `tabExcom AI Company` c
		ON c.name = p.company WHERE c.agent = %s GROUP BY p.email_status""", agent_name))
	return {"companies": by_status, "people": people, "total": cint(sum(by_status.values()))}
