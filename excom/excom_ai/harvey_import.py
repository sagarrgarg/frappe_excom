"""Bringing Harvey's work into Excom AI (HLD-007 Phase 1I, HLD-006 §11, Appendix A).

Reads Harvey's SQLite database and its local config, read-only, and writes Excom records. Safe to
run again: every row carries Harvey's id (external ids and source references), so nothing is
copied twice.

    companies        -> Excom AI Company (one per domain), research notes, company stops
    prospects        -> Excom AI Person; the ones Harvey contacted (contacted, replied, lost) also
                        become Leads, owned by the agent's owner, on its team, with the ai_* fields
    campaigns        -> Excom AI Sequence (one per campaign, its steps and waits)
    outbox           -> Excom AI Proposal per email: sent ones as Sent, approved as Approved
                        (they wait for the agent to be Live), pending as Pending, rejected and
                        cancelled with their reason; plus an Enrollment per campaign and prospect
    conversations    -> the matching Excom conversation gets the agent, the intent and the stage
    email_patterns   -> Excom AI Email Domain
    compliance       -> do-not-contact domains into Excom AI Settings, addresses onto the person
    persona, skills  -> Harish, a Sales agent in Shadow, with a Draft playbook built from his
                        product_knowledge and competitive_intel files and the writer's rules

Harvey keeps running until a person stops it; `pause_harvey()` sets its own kill switch
(sending_paused) after copying its database, and is only run when asked.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3

import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime

from excom.excom_ai.doctype.excom_ai_company.excom_ai_company import normalise_domain

HARVEY = os.path.expanduser("~/harvey")
SOURCE = {"discover:serper": "Serper", "volza": "Volza", "web_search": "Web search", "osm": "OSM", "discover:osm": "OSM",
	"dataforseo": "DataForSEO", "scavio": "Scavio", "tinyfish": "TinyFish"}
EMAIL_STATUS = {"verified": "Verified", "risky": "Risky", "guess": "Guess", "invalid": "Invalid"}
PROMOTED = ("contacted", "replied", "lost")
OUTBOX_STATUS = {"sent": "Sent", "approved": "Approved", "pending_review": "Pending", "rejected": "Rejected",
	"cancelled": "Cancelled"}
STAGE = {"initial_outreach": "Initial outreach", "engaged": "Engaged", "qualifying": "Qualifying", "presenting": "Presenting",
	"negotiating": "Negotiating", "closing": "Closing", "closed_lost": "Closed lost", "closed_won": "Closed won"}


def _db(path: str | None = None):
	path = path or os.path.join(HARVEY, "data", "harvey.db")
	conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
	conn.row_factory = sqlite3.Row
	return conn


def _config() -> dict:
	import yaml

	out = {}
	for name in ("harvey.yaml", "harvey.local.yaml"):
		p = os.path.join(HARVEY, name)
		if os.path.exists(p):
			with open(p) as f:
				_merge(out, yaml.safe_load(f) or {})
	return out


def _merge(a: dict, b: dict) -> None:
	for k, v in b.items():
		if isinstance(v, dict) and isinstance(a.get(k), dict):
			_merge(a[k], v)
		else:
			a[k] = v


def _t(value, n: int = 140):
	"""Harvey's text as an Excom Data field takes it (140 characters)."""
	return (str(value)[:n] or None) if value else None


def _country(location: str | None) -> str | None:
	"""The country from Harvey's free-text location ("Brisbane, QLD, Australia", "Austin, TX")."""
	if not location:
		return None
	parts = [p.strip() for p in location.split(",") if p.strip()]
	for p in reversed(parts):
		if frappe.db.exists("Country", p):
			return p
		if len(p) == 2 and p.isupper() and len(parts) > 1:
			return "United States" if p not in ("UK",) else "United Kingdom"
	return None


def run(owner: str, backup: str | None = None, team: str | None = None, mailbox: str | None = None,
		db_path: str | None = None, agent_name: str = "Harish") -> dict:
	"""Import everything; returns counts on both sides so a person can see they match."""
	conn = _db(db_path)
	cfg = _config()
	report = {"harvey": {}, "excom": {}}
	for t in ("companies", "prospects", "campaigns", "outbox", "conversations", "email_patterns"):
		report["harvey"][t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
	report["harvey"]["outbox_by_status"] = dict(conn.execute("SELECT status, COUNT(*) FROM outbox GROUP BY status").fetchall())

	agent = _agent(cfg, owner, backup, team, mailbox, agent_name)
	stops = {r["key"].split(":", 1)[1] for r in conn.execute("SELECT key FROM settings WHERE key LIKE 'company_stop:%'")}
	companies = _companies(conn, agent, stops)
	people, leads = _people(conn, agent, companies)
	sequences = _sequences(conn, agent)
	enrollments, props = _outbox(conn, agent, people, leads, sequences, companies)
	threads = _conversations(conn, agent, people)
	domains = _patterns(conn)
	dnc = _compliance(cfg)
	frappe.db.commit()
	report["excom"] = {
		"agent": agent.name, "companies": len(companies), "people": len(people), "leads": len(leads),
		"sequences": len(sequences), "enrollments": len(enrollments), "proposals": len(props),
		"proposals_by_status": dict(frappe.db.sql("""SELECT status, COUNT(*) FROM `tabExcom AI Proposal`
			WHERE name IN %(n)s GROUP BY status""", {"n": tuple(props) or ("-",)})),
		"conversations_matched": threads, "email_domains": domains, "dnc_domains": dnc,
	}
	agent.add_comment("Comment", _("Imported from Harvey: {0}").format(frappe.as_json(report["excom"], indent=None)))
	return report


def _agent(cfg: dict, owner: str, backup: str | None, team: str | None, mailbox: str | None, name: str):
	existing = frappe.db.get_value("Excom AI Agent", {"agent_name": name, "status": ["!=", "Retired"]})
	if existing:
		return frappe.get_doc("Excom AI Agent", existing)
	persona, product = cfg.get("persona") or {}, cfg.get("product") or {}
	mailbox = mailbox or frappe.db.get_value("Excom Channel Account", {"email_address": persona.get("email"), "channel": "email"})
	company = frappe.db.get_value("Company", {"company_name": persona.get("company")}) or frappe.defaults.get_global_default("company")
	doc = frappe.get_doc({
		"doctype": "Excom AI Agent", "agent_name": name, "title": f"{persona.get('role') or 'Sales'}, {persona.get('company') or ''}".strip(", "),
		"agent_type": "Sales", "status": "Draft", "company": company, "owner_user": owner, "backup_user": backup, "team": team,
		"what_it_is": product.get("description"), "tone": persona.get("tone"),
		"mission": _("Carry on Harvey's export outreach from ERPNext: reply to every buyer within 2 working hours."),
		"customer_types": "Export Importer" if frappe.get_meta("Lead").has_field("customer_type") else None,
		"brief_sources": json.dumps({"what_it_is": "harvey.local.yaml product", "tone": "harvey.local.yaml persona"}),
		"channels": [{"channel_account": mailbox, "purpose": "Outreach and replies"}] if mailbox else [],
	})
	doc.insert(ignore_permissions=True)
	pb = _playbook(cfg, name)
	doc.reload()
	doc.append("playbooks", {"playbook": pb})
	doc.save(ignore_permissions=True)
	return frappe.get_doc("Excom AI Agent", doc.name)


def _playbook(cfg: dict, name: str) -> str:
	"""Harish's first playbook: his two generated skill files and the writer's company rules."""
	def read(f):
		p = os.path.join(HARVEY, "skills", f)
		return open(p).read() if os.path.exists(p) else ""

	sections = [
		{"kind": "Facts", "title": "product_knowledge.md", "content": read("product_knowledge.md"), "source": "Import",
			"source_ref": "harvey/skills/product_knowledge.md"},
		{"kind": "Process rules", "title": "competitive_intel.md", "content": read("competitive_intel.md"), "source": "Import",
			"source_ref": "harvey/skills/competitive_intel.md"},
		{"kind": "Certifications", "title": "", "content": "Tested by Intertek to ASTM F963 and CPSIA; ISO 9001 certified. "
			"Certifications are for the water guns only.", "source": "Import", "source_ref": "harvey/prompts/writer.md rule 19"},
		{"kind": "Banned claims", "title": "", "content": "Never claim EN71.\nNever claim CE.", "source": "Import",
			"source_ref": "harvey/prompts/writer.md rule 19"},
		{"kind": "Compliance", "title": "", "content": (cfg.get("compliance") or {}).get("opt_out_line") or "", "source": "Import",
			"source_ref": "harvey.local.yaml compliance.opt_out_line"},
	]
	doc = frappe.get_doc({"doctype": "Excom AI Playbook", "title": _("{0} playbook (from Harvey)").format(name), "agent_type": "Sales",
		"status": "Draft", "sections": [s for s in sections if s["content"]], "change_note": _("Imported from Harvey")})
	doc.insert(ignore_permissions=True)
	return doc.name


def _companies(conn, agent, stops: set) -> dict:
	out = {}
	for r in conn.execute("SELECT * FROM companies"):
		domain = normalise_domain(r["domain"] or r["website"])
		ext = r["external_id"] or f"harvey:{r['id']}"
		name = (frappe.db.get_value("Excom AI Company", {"external_id": _t(ext)})
			or (frappe.db.get_value("Excom AI Company", {"domain": domain}) if domain else None))
		if not name:
			try:
				doc = frappe.get_doc({
					"doctype": "Excom AI Company", "company_name": (r["name"] or domain or "?")[:140], "domain": domain,
					"external_id": _t(ext), "website": _t(r["website"]), "description": (r["description"] or "")[:500] or None,
					"industry": _t(r["industry"]), "location_text": _t(r["location"]), "country": _country(r["location"]),
					"phone": _t(r["phone"]), "source": SOURCE.get(r["source"] or "", "Import"), "source_url": _t(r["source_url"]),
					"agent": agent.name, "status": "New", "research_notes": r["notes"],
					"website_facts": r["tech_stack_json"], "signals": r["signals_json"],
					"profile_checked_on": r["profile_checked_at"], "contacts_checked_on": r["prospects_checked_at"],
					"company_stop": 1 if r["id"] in stops else 0, "stop_reason": _("Harvey: someone there replied") if r["id"] in stops else None,
				})
				doc.insert(ignore_permissions=True)
				name = doc.name
			except frappe.UniqueValidationError:
				frappe.clear_last_message()
				name = frappe.db.get_value("Excom AI Company", {"domain": domain})
		out[r["id"]] = name
	return out


def _people(conn, agent, companies: dict) -> tuple[dict, dict]:
	from excom.excom.services import crm_gateway

	people, leads = {}, {}
	owner = agent.owner_user
	for r in conn.execute("SELECT * FROM prospects"):
		email = (r["email"] or "").strip().lower()
		company = companies.get(r["company_id"])
		if not email or not company:
			continue
		name = frappe.db.get_value("Excom AI Person", {"email": email})
		status = {"new": "New", "queued": "Ready"}.get(r["status"], "Promoted" if r["status"] in PROMOTED else "New")
		if not name:
			doc = frappe.get_doc({"doctype": "Excom AI Person", "company": company, "email": email, "first_name": _t(r["first_name"]),
				"last_name": _t(r["last_name"]), "title": _t(r["title"]), "seniority": r["seniority"] if r["seniority"] in (
					"Owner", "Executive", "Director", "Manager", "Staff", "Inbox") else None, "department": _t(r["department"]),
				"is_inbox": 1 if (r["source"] or "") == "company_inbox" else 0,
				"email_status": EMAIL_STATUS.get(r["email_status"] or "", "Unknown"), "phone": _t(r["phone"]),
				"linkedin_url": _t(r["linkedin_url"]), "timezone": _t(r["timezone"]), "source": None, "source_url": _t(r["source_url"]),
				"score": r["score"], "status": status})
			doc.insert(ignore_permissions=True)
			name = doc.name
		people[r["id"]] = name
		if r["personalization_notes"]:
			notes = frappe.db.get_value("Excom AI Company", company, "research_notes") or ""
			if r["personalization_notes"] not in notes:
				frappe.db.set_value("Excom AI Company", company, "research_notes", (notes + "\n" + r["personalization_notes"]).strip(),
					update_modified=False)
		if r["status"] in PROMOTED:
			lead = frappe.db.get_value("Lead", {"email_id": email})
			if not lead:
				lead = crm_gateway.create_lead({"name": " ".join(filter(None, (r["first_name"], r["last_name"]))) or r["company"],
					"email": email, "company_name": r["company"], "owner": owner, "first_touch_channel": "Manual",
					"source_reference": f"harvey:prospect:{r['id']}",
					"customer_type": agent.customer_types or None}, ignore_permissions=True).name
			values = {"ai_agent": agent.name, "ai_person": name, "ai_email_status": EMAIL_STATUS.get(r["email_status"] or "")}
			if frappe.get_meta("Lead").has_field("excom_team") and agent.team:
				values["excom_team"] = agent.team
			frappe.db.set_value("Lead", lead, {k: v for k, v in values.items() if v and frappe.get_meta("Lead").has_field(k)},
				update_modified=False)
			frappe.db.set_value("Excom AI Person", name, {"lead": lead, "status": "Promoted"}, update_modified=False)
			frappe.db.set_value("Excom AI Company", company, "status", "Promoted", update_modified=False)
			leads[r["id"]] = lead
		elif status == "Ready":
			frappe.db.set_value("Excom AI Company", company, "status", "Contacts found", update_modified=False)
	return people, leads


def _sequences(conn, agent) -> dict:
	out = {}
	for r in conn.execute("SELECT * FROM campaigns"):
		title = f"Harvey: {r['name']}"[:140]
		name = frappe.db.get_value("Excom AI Sequence", {"agent": agent.name, "title": title})
		if not name:
			steps = json.loads(r["sequence_json"] or "[]")
			doc = frappe.get_doc({"doctype": "Excom AI Sequence", "title": title, "agent": agent.name, "enabled": 0,
				"first_email_style": "Direct offer", "steps": [{"step": s.get("step"), "wait_days": s.get("delay_days") or 0,
					"channel": "Email", "purpose": s.get("subject") or "", "word_limit": (90, 80, 45)[min(2, (s.get("step") or 1) - 1)]}
					for s in steps]})
			doc.insert(ignore_permissions=True)
			name = doc.name
		out[r["id"]] = name
	return out


def _outbox(conn, agent, people: dict, leads: dict, sequences: dict, companies: dict) -> tuple[dict, list]:
	enrollments, props = {}, []
	prospects = {r["id"]: r for r in conn.execute("SELECT id, status, company_id FROM prospects")}
	for r in conn.execute("SELECT * FROM outbox ORDER BY campaign_id, prospect_id, step"):
		pid, cid = r["prospect_id"], r["campaign_id"]
		if pid not in people:
			continue
		ref = f"harvey:outbox:{r['id']}"
		existing = frappe.db.get_value("Excom AI Proposal", {"agent": agent.name, "source_ref": ref})
		if existing:
			props.append(existing)
			continue
		enrollment = None
		if r["kind"] == "sequence" and cid in sequences:
			key = (cid, pid)
			if key not in enrollments:
				p = prospects.get(pid)
				status = {"replied": "Replied", "lost": "Stopped"}.get(p["status"] if p else "", "Active")
				enr = frappe.db.get_value("Excom AI Enrollment", {"agent": agent.name, "sequence": sequences[cid], "person": people[pid]})
				if not enr:
					enr = frappe.get_doc({"doctype": "Excom AI Enrollment", "agent": agent.name, "sequence": sequences[cid],
						"person": people[pid], "lead": leads.get(pid), "company": companies.get(p["company_id"]) if p else None,
						"status": status, "stop_reason": _("Harvey: {0}").format(p["status"]) if status != "Active" else None}
						).insert(ignore_permissions=True).name
				enrollments[key] = enr
			enrollment = enrollments[key]
		status = OUTBOX_STATUS.get(r["status"], "Cancelled")
		values = {
			"doctype": "Excom AI Proposal", "agent": agent.name, "action": "reply" if r["kind"] == "reply" else "send_email",
			"policy_action": "reply" if r["kind"] == "reply" else ("first_email" if (r["step"] or 1) == 1 else "follow_up_email"),
			"status": status, "kind": "Reply" if r["kind"] == "reply" else "Sequence", "enrollment": enrollment,
			"step": r["step"] if enrollment else None, "to_email": _t(r["to_email"]),
			"payload": {"subject": r["subject"], "body": r["body"], "harvey_message_id": r["message_id"]},
			"preview_html": "".join(f"<p>{frappe.utils.escape_html(x)}</p>" for x in (r["body"] or "").split("\n\n")),
			"send_at": get_datetime(r["send_at"]) if r["send_at"] else None,
			"sent_on": get_datetime(r["sent_at"]) if r["sent_at"] else None,
			"decided_by": "Autopilot" if (r["approved_by"] or "") == "autopilot" else ("Earlier" if status != "Pending" else None),
			"error": (r["error"] or "")[:500] or None, "source_ref": ref,
			"subject_doctype": "Lead" if leads.get(pid) else "Excom AI Person", "subject_name": leads.get(pid) or people[pid],
		}
		if status == "Rejected":
			values.update({"reject_reason": "Other", "reject_note": r["error"] or _("Rejected in Harvey")})
		if r["message_id"]:
			msg = frappe.db.get_value("Excom Message", {"internet_message_id": r["message_id"]})
			if msg:
				values["message"] = msg
		doc = frappe.get_doc(values)
		try:
			doc.insert(ignore_permissions=True)
		except frappe.ValidationError:
			# Harvey had two prospects with the same address in one campaign; here they are one person
			# and one enrollment, so the second email is kept, outside the sequence, and says why.
			frappe.clear_last_message()
			values.update({"enrollment": None, "step": None,
				"hold_reason": _("Harvey had this address twice in one campaign: kept outside the sequence")})
			doc = frappe.get_doc(values)
			doc.insert(ignore_permissions=True)
		props.append(doc.name)
		if status == "Sent" and enrollment:
			cur = frappe.db.get_value("Excom AI Enrollment", enrollment, ["current_step", "last_sent_on"], as_dict=True)
			if (r["step"] or 0) >= (cur.current_step or 0):
				frappe.db.set_value("Excom AI Enrollment", enrollment, {"current_step": r["step"], "last_sent_on": values["sent_on"]},
					update_modified=False)
	return enrollments, props


def _conversations(conn, agent, people: dict) -> int:
	matched = 0
	for r in conn.execute("SELECT * FROM conversations"):
		email = conn.execute("SELECT email FROM prospects WHERE id = ?", (r["prospect_id"],)).fetchone()
		identity = frappe.db.get_value("Omni Identity", {"primary_email": (email[0] or "").lower()}) if email else None
		thread = frappe.db.get_value("Excom Thread", {"omni_identity": identity, "channel": "email"}) if identity else None
		if not thread:
			continue
		frappe.db.set_value("Excom Thread", thread, {"ai_agent": agent.name, "ai_intent": r["intent"],
			"ai_stage": STAGE.get(r["stage"] or "", None), "ai_mode": "Needs person" if r["status"] == "needs_human" else "Handle"},
			update_modified=False)
		for enr in frappe.get_all("Excom AI Enrollment", {"agent": agent.name, "person": people.get(r["prospect_id"])}, pluck="name"):
			frappe.db.set_value("Excom AI Enrollment", enr, "thread", thread, update_modified=False)
		matched += 1
	return matched


def _patterns(conn) -> int:
	n = 0
	mx = {"google": "Google", "microsoft": "Microsoft", "gateway": "Gateway", "none": "None"}
	for r in conn.execute("SELECT * FROM email_patterns"):
		if frappe.db.exists("Excom AI Email Domain", {"domain": r["domain"]}):
			continue
		frappe.get_doc({"doctype": "Excom AI Email Domain", "domain": r["domain"], "pattern": r["pattern"],
			"pattern_source": r["source"], "pattern_confidence": r["confidence"], "mx_type": mx.get((r["mx_type"] or "").lower(), "Other"),
			"catch_all": 1 if r["is_catch_all"] else 0, "checked_on": get_datetime(r["updated_at"]) if r["updated_at"] else None}
			).insert(ignore_permissions=True)
		n += 1
	return n


def _compliance(cfg: dict) -> int:
	from excom.excom_ai.doctype.excom_ai_company.excom_ai_company import normalise_domain as nd

	entries = (cfg.get("compliance") or {}).get("do_not_contact") or []
	settings = frappe.get_single("Excom AI Settings")
	have = {r.domain for r in settings.dnc_domains or []}
	added = 0
	for e in entries:
		e = str(e).strip().lower()
		if "@" in e:
			identity = frappe.db.get_value("Omni Identity", {"primary_email": e})
			if identity:
				frappe.db.set_value("Omni Identity", identity, {"do_not_contact": 1, "opt_out_source": "harvey_do_not_contact"},
					update_modified=False)
			continue
		d = nd(e)
		if d and d not in have:
			settings.append("dnc_domains", {"domain": d, "reason": "Asked not to be contacted"})
			have.add(d)
			added += 1
	if added:
		settings.save(ignore_permissions=True)
	return added


def pause_harvey(db_path: str | None = None) -> str:
	"""Cut-over: set Harvey's own kill switch so it sends nothing more, after copying its database
	beside it. Only run when a person asks for it."""
	path = db_path or os.path.join(HARVEY, "data", "harvey.db")
	backup = f"{path}.before-excom-{now_datetime().strftime('%Y%m%d%H%M%S')}"
	shutil.copy2(path, backup)
	conn = sqlite3.connect(path)
	conn.execute("INSERT INTO settings (key, value, updated_at) VALUES ('sending_paused', '1', datetime('now')) "
		"ON CONFLICT(key) DO UPDATE SET value = '1', updated_at = datetime('now')")
	conn.commit()
	conn.close()
	return backup


def undo(agent_name: str) -> dict:
	"""Remove what an import made for this agent (to redo it, or after a trial on a test site)."""
	agent = frappe.get_doc("Excom AI Agent", agent_name)
	companies = frappe.get_all("Excom AI Company", {"agent": agent.name}, pluck="name")
	people = frappe.get_all("Excom AI Person", {"company": ["in", companies or ["-"]]}, pluck="name")
	leads = frappe.get_all("Lead", {"ai_agent": agent.name}, pluck="name") if frappe.get_meta("Lead").has_field("ai_agent") else []
	counts = {"companies": len(companies), "people": len(people), "leads": len(leads)}
	for dt in ("Excom AI Proposal", "Excom AI Enrollment", "Excom AI Task", "Excom AI Sequence", "Excom AI Knowledge"):
		counts[dt] = frappe.db.count(dt, {"agent": agent.name})
		frappe.db.delete(dt, {"agent": agent.name})
	frappe.db.delete("Excom AI Observation", {"company": ["in", companies or ["-"]]})
	frappe.db.delete("Excom AI Person", {"name": ["in", people or ["-"]]})
	frappe.db.delete("Excom AI Company", {"name": ["in", companies or ["-"]]})
	for lead in leads:
		# A Lead brings a Contact and an Omni Identity with it (Excom's hooks): remove them together.
		contacts = frappe.get_all("Dynamic Link", {"link_doctype": "Lead", "link_name": lead, "parenttype": "Contact"}, pluck="parent")
		ids = set(frappe.get_all("Omni Identity Link", {"linked_name": ["in", [lead, *contacts]]}, pluck="parent"))
		for oi in ids:
			if frappe.db.exists("Excom Thread", {"omni_identity": oi}):
				frappe.db.delete("Omni Identity Link", {"parent": oi, "linked_name": ["in", [lead, *contacts]]})
				continue
			for child in ("Omni Identity Link", "Omni Identity Channel", "Omni Identity Alias"):
				frappe.db.delete(child, {"parent": oi})
			frappe.delete_doc("Omni Identity", oi, force=True, ignore_permissions=True)
		for c in contacts:
			frappe.delete_doc("Contact", c, force=True, ignore_permissions=True)
		frappe.delete_doc("Lead", lead, force=True, ignore_permissions=True)
	pbs = [r.playbook for r in agent.playbooks]
	user = agent.service_user
	frappe.delete_doc("Excom AI Agent", agent.name, force=True, ignore_permissions=True)
	for pb in pbs:
		frappe.delete_doc("Excom AI Playbook", pb, force=True, ignore_permissions=True)
	if user and frappe.db.exists("User", user):
		frappe.db.delete("Excom Team Member", {"user": user})
		frappe.delete_doc("User", user, force=True, ignore_permissions=True)
	frappe.db.commit()
	return counts
