"""People at a company, from paid data providers (HLD-009): Apollo and Prospeo.

Both are called only through their APIs; LinkedIn itself is never fetched, and a LinkedIn URL is kept
as a link. Each tool offers:

    search(domain, titles, limit) -> [candidate]      people at the company by job title, no emails
    enrich(external_id | first_name+last_name+domain | linkedin_url | email) -> person | None

candidate = {provider, external_id, first_name, last_name, full_name, title, seniority, linkedin_url, has_email}
person    = candidate + {email, email_status: Verified|Risky|Unknown, source_url}

What a call really costs is set on the tool as `charged` (Prospeo's no-match and its free re-enrichment
cost nothing), so the monthly budget counts only what the provider bills. No phone numbers are fetched:
Apollo sends them later to a webhook at 9× the credits, Prospeo charges 10×, and a found number is
never WhatsApp opt-in anyway.

Everything a provider sends back is untrusted text (§12.7): cleaned here, and fenced before any prompt.
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlsplit

import frappe
import requests
from frappe import _

from excom.excom_ai.tools import Tool, ToolRefused, tool
from excom.excom_ai.tools.web import normalize_domain, user_agent

TIMEOUT = (10, 30)
APOLLO_URL = "https://api.apollo.io/api/v1"
PROSPEO_URL = "https://api.prospeo.io"
APOLLO_CREDIT = 0.04  # USD, estimated (plans are not priced per credit)
PROSPEO_CREDIT = 0.025
SENIORITY = {"owner": "Owner", "founder": "Owner", "c_suite": "Executive", "partner": "Executive", "vp": "Executive",
	"head": "Director", "director": "Director", "manager": "Manager", "senior": "Manager", "entry": "Staff",
	"intern": "Staff"}
SENIORITY_RANK = {"Owner": 0, "Executive": 1, "Director": 2, "Manager": 3, "Staff": 4, None: 5}


# ── cleaning ─────────────────────────────────────────────────────────────────


def clean(value, size: int = 140) -> str | None:
	"""Provider text made safe to store: no control characters, one line, bounded."""
	if value is None:
		return None
	text = "".join(" " if unicodedata.category(c).startswith("C") else c for c in str(value))
	text = re.sub(r"\s+", " ", text).strip()
	return text[:size] or None


def linkedin(url) -> str | None:
	"""A person's LinkedIn profile URL in one form (https://www.linkedin.com/in/<slug>), or None when it
	is not one. Company pages, other hosts and anything odd are dropped."""
	raw = str(url or "").strip()
	if not raw:
		return None
	if "://" not in raw:
		raw = "https://" + raw
	try:
		parts = urlsplit(raw)
	except ValueError:
		return None
	host = (parts.hostname or "").lower()
	if parts.scheme not in ("http", "https") or not (host == "linkedin.com" or host.endswith(".linkedin.com")):
		return None
	# a slug may be in any script (/in/josé-pérez) and may carry a profile-language suffix (/in/ana/es)
	m = re.match(r"^/in/([\w\-%.]{2,100})(?:/[a-z]{2}(?:-[a-z]{2})?)?/?$", parts.path or "", re.UNICODE | re.IGNORECASE)
	return f"https://www.linkedin.com/in/{m.group(1).lower()}" if m else None


def email(value) -> str | None:
	"""An address Frappe will store (its own check), or None; Apollo's locked placeholder is not one."""
	e = str(value or "").strip().lower()
	if not e or len(e) > 254 or e.startswith("email_not_unlocked") or not frappe.utils.validate_email_address(e):
		return None
	return e


def seniority_from(level, title) -> str | None:
	if level and str(level).lower() in SENIORITY:
		return SENIORITY[str(level).lower()]
	t = (title or "").lower()
	for words, label in ((("owner", "founder", "proprietor", "dueño", "propietario"), "Owner"),
			(("ceo", "chief", "president", "managing director", "partner", "vp", "vice president"), "Executive"),
			(("director", "head", "gerente general"), "Director"), (("manager", "gerente", "lead", "jefe"), "Manager")):
		if any(w in t for w in words):
			return label
	return None


def _candidate(provider, external_id, first, last, full, title, level=None, url=None, has_email=None) -> dict:
	first, last, title = clean(first, 60), clean(last, 60), clean(title)
	full = clean(full, 120) or " ".join(x for x in (first, last) if x) or None
	return {"provider": provider, "external_id": clean(external_id, 64), "first_name": first, "last_name": last,
		"full_name": full, "title": title, "seniority": seniority_from(level, title), "linkedin_url": linkedin(url),
		"has_email": has_email}


def rank(candidates: list[dict], titles: list[str]) -> list[dict]:
	"""Best match first: the earliest wanted title word found in the job title, then seniority."""
	words = [w.strip().lower() for w in titles or [] if w and w.strip()]

	def key(c):
		t = (c.get("title") or "").lower()
		hit = next((i for i, w in enumerate(words) if w in t), len(words))
		return (hit, SENIORITY_RANK.get(c.get("seniority"), 5), 0 if c.get("has_email") is not False else 1)

	return sorted(candidates, key=key)


# ── HTTP ─────────────────────────────────────────────────────────────────────


def _post(url: str, label: str, headers: dict, body: dict, secret: str) -> tuple[int, dict]:
	"""One POST. A failure raises with the status, never the key or the body."""
	try:
		r = requests.post(url, json=body, timeout=TIMEOUT, headers={"User-Agent": user_agent(),
			"Content-Type": "application/json", "Accept": "application/json", **headers})
	except requests.RequestException as e:
		raise RuntimeError(f"{label}: no answer ({type(e).__name__})") from None
	if r.status_code == 429:
		raise ToolRefused(_("{0}: too many calls just now, try later.").format(label))
	try:
		data = r.json()
	except ValueError:
		data = None
	if not isinstance(data, dict):
		if r.status_code in (401, 402, 403):
			return r.status_code, {}  # an account answer in plain text (Apollo's 401): the caller refuses
		raise RuntimeError(f"{label}: HTTP {r.status_code}, an answer that is not JSON")
	if secret:
		import json

		data = json.loads(json.dumps(data).replace(secret, "[hidden]"))
	return r.status_code, data


def _refuse(record, message: str):
	"""An account problem (bad key, no credits, an endpoint the key may not use): the call is refused,
	so research falls through to the other provider, and the reason is kept on the tool for the admin."""
	if record:
		frappe.db.set_value("Excom AI Tool", record.name, "last_error", message[:500], update_modified=False)
	raise ToolRefused(message)


# ── Apollo ───────────────────────────────────────────────────────────────────

APOLLO_EMAIL = {"verified": "Verified", "likely to engage": "Risky", "likely_to_engage": "Risky",
	"extrapolated": "Unknown", "guessed": "Unknown"}


def _apollo(path: str, body: dict, secret: str, record=None) -> dict:
	status, data = _post(f"{APOLLO_URL}{path}", "Apollo", {"x-api-key": secret, "Cache-Control": "no-cache"}, body, secret)
	if status != 200:
		code = ((data.get("error_details") or {}).get("code") or data.get("error_code") or "")
		if status in (401, 402, 403):
			_refuse(record, _("Apollo: HTTP {0} {1}. People search needs a master API key; check the key and the plan's credits.")
				.format(status, clean(code, 60) or ""))
		raise RuntimeError(f"Apollo: HTTP {status}{' ' + clean(code, 60) if code else ''}")
	return data


def map_apollo_search(data: dict) -> list[dict]:
	out = []
	for p in data.get("people") or []:
		if not isinstance(p, dict) or not p.get("id"):
			continue
		# search hides the last name (last_name_obfuscated): only what is really given is kept
		out.append(_candidate("Apollo", p.get("id"), p.get("first_name"), p.get("last_name"), None,
			p.get("title"), p.get("seniority"), p.get("linkedin_url"),
			None if p.get("has_email") is None else bool(p.get("has_email"))))
	return out


def map_apollo_person(data: dict) -> dict | None:
	p = data.get("person") or {}
	if not isinstance(p, dict) or not p or str(p.get("match_confidence") or data.get("match_confidence") or "") == "none":
		return None
	out = _candidate("Apollo", p.get("id"), p.get("first_name"), p.get("last_name"), p.get("name"), p.get("title"),
		p.get("seniority"), p.get("linkedin_url"))
	address = email(p.get("email"))
	status = APOLLO_EMAIL.get(str(p.get("email_status") or "").lower(), "Unknown") if address else None
	out.update({"email": address, "email_status": status, "has_email": bool(address), "source_url": out["linkedin_url"]})
	return out


@tool("apollo", tier="paid", cost=APOLLO_CREDIT, needs_key=True)
class Apollo(Tool):
	"""search(domain, titles, limit) is free (no emails, last names hidden); enrich() is a credit when
	Apollo returns someone, nothing when it does not."""

	charged: float | None = None

	def search(self, domain: str, titles: list | None = None, limit: int = 10) -> list[dict]:
		domain = normalize_domain(domain)
		if not domain:
			raise ToolRefused(_("No domain to look up."))
		self.charged = 0.0
		body = {"q_organization_domains_list": [domain], "person_titles": [t for t in titles or [] if t][:20],
			"include_similar_titles": True, "page": 1, "per_page": max(1, min(int(limit or 10), 25))}
		return map_apollo_search(_apollo("/mixed_people/api_search", body, self.secret(), self.record))

	def enrich(self, external_id: str | None = None, first_name: str | None = None, last_name: str | None = None,
			domain: str | None = None, linkedin_url: str | None = None, email: str | None = None) -> dict | None:
		body = _enrich_body(external_id, first_name, last_name, domain, linkedin_url, email, id_key="id")
		body.update({"reveal_personal_emails": False, "reveal_phone_number": False})
		out = map_apollo_person(_apollo("/people/match", body, self.secret(), self.record))
		self.charged = APOLLO_CREDIT if out else 0.0
		return out

	def fake_search(self, domain: str, titles: list | None = None, limit: int = 10) -> list[dict]:
		self.charged = 0.0
		return map_apollo_search({"people": [
			{"id": "ap-1", "first_name": "Carlos", "last_name_obfuscated": "Ra***z", "title": "Gerente de Compras",
				"seniority": "manager", "has_email": True},
			{"id": "ap-2", "first_name": "Lucía", "title": "Directora de Importaciones", "seniority": "director",
				"has_email": True},
			{"id": "ap-3", "first_name": "Pedro", "title": "Marketing Intern", "seniority": "intern", "has_email": False},
		]})[: max(1, int(limit or 10))]

	def fake_enrich(self, external_id=None, first_name=None, last_name=None, domain=None, linkedin_url=None, email=None):
		d = normalize_domain(domain or "") or "acme.qa-people.example"
		people = {"ap-1": {"id": "ap-1", "first_name": "Carlos", "last_name": "Ramírez", "title": "Gerente de Compras",
				"seniority": "manager", "linkedin_url": "https://linkedin.com/in/carlos-ramirez-qa", "email": f"carlos.ramirez@{d}",
				"email_status": "verified"},
			"ap-2": {"id": "ap-2", "first_name": "Lucía", "last_name": "Núñez", "title": "Directora de Importaciones",
				"seniority": "director", "linkedin_url": "https://www.linkedin.com/in/lucia-nunez-qa/", "email": None,
				"email_status": "unavailable"}}
		out = map_apollo_person({"person": people.get(external_id or "") or {}})
		self.charged = APOLLO_CREDIT if out else 0.0
		return out


# ── Prospeo ──────────────────────────────────────────────────────────────────


PROSPEO_ACCOUNT = ("INVALID_API_KEY", "INSUFFICIENT_CREDITS", "PLAN_REQUIRED")


def _prospeo(path: str, body: dict, secret: str, empty: tuple = (), record=None) -> dict | None:
	"""None for the provider's 'nothing found' codes (never charged); raises on everything else."""
	status, data = _post(f"{PROSPEO_URL}{path}", "Prospeo", {"X-KEY": secret}, body, secret)
	if data.get("error"):
		code = str(data.get("error_code") or "")
		if code in empty:
			return None
		if code in PROSPEO_ACCOUNT or status in (401, 402, 403):
			_refuse(record, _("Prospeo: {0}. Check the key and the plan's credits.").format(clean(code, 60) or status))
		raise RuntimeError(f"Prospeo: {clean(code, 60) or 'HTTP ' + str(status)}")
	if status in (401, 402, 403):  # an account answer in plain text
		_refuse(record, _("Prospeo: HTTP {0}. Check the key and the plan's credits.").format(status))
	if status != 200:
		raise RuntimeError(f"Prospeo: HTTP {status}")
	return data


def map_prospeo_person(p: dict, provider: str = "Prospeo") -> dict:
	return _candidate(provider, p.get("person_id"), p.get("first_name"), p.get("last_name"), p.get("full_name"),
		p.get("current_job_title") or p.get("job_title"), p.get("seniority"), p.get("linkedin_url"))


def map_prospeo_search(data: dict | None) -> list[dict]:
	out = []
	for r in (data or {}).get("results") or []:
		p = (r or {}).get("person") if isinstance(r, dict) else None
		if isinstance(p, dict) and p.get("person_id"):
			out.append(map_prospeo_person(p) | {"has_email": None})
	return out


def map_prospeo_enrich(data: dict | None) -> dict | None:
	p = (data or {}).get("person")
	if not isinstance(p, dict) or not p:
		return None
	out = map_prospeo_person(p)
	e = p.get("email") if isinstance(p.get("email"), dict) else {"email": p.get("email")}
	address = email(e.get("email")) if e.get("revealed", True) else None
	status = "Verified" if address and str(e.get("status") or "").upper() == "VERIFIED" else "Unknown" if address else None
	out.update({"email": address, "email_status": status, "has_email": bool(address), "source_url": out["linkedin_url"]})
	return out


@tool("prospeo", tier="paid", cost=PROSPEO_CREDIT, needs_key=True)
class Prospeo(Tool):
	"""search() is a credit per page with results (free again within 30 days); enrich() asks for verified
	emails only and is a credit per email found (no match, and a repeat within 90 days, cost nothing)."""

	charged: float | None = None

	def search(self, domain: str, titles: list | None = None, limit: int = 10) -> list[dict]:
		domain = normalize_domain(domain)
		if not domain:
			raise ToolRefused(_("No domain to look up."))
		filters = {"company": {"websites": {"include": [domain]}}}
		titles = [t for t in titles or [] if t][:20]
		if titles:
			filters["person_job_title"] = {"include": titles, "match_mode": "CONTAINS"}
		data = _prospeo("/search-person", {"page": 1, "filters": filters}, self.secret(), empty=("NO_RESULTS", "NO_MATCH"),
			record=self.record)
		out = map_prospeo_search(data)
		self.charged = PROSPEO_CREDIT if out and not (data or {}).get("free") else 0.0
		return out[: max(1, int(limit or 10))]

	def enrich(self, external_id: str | None = None, first_name: str | None = None, last_name: str | None = None,
			domain: str | None = None, linkedin_url: str | None = None, email: str | None = None) -> dict | None:
		data = _enrich_body(external_id, first_name, last_name, domain, linkedin_url, email, id_key="person_id",
			domain_key="company_website")
		answer = _prospeo("/enrich-person", {"only_verified_email": True, "data": data}, self.secret(), empty=("NO_MATCH",),
			record=self.record)
		out = map_prospeo_enrich(answer)
		self.charged = PROSPEO_CREDIT if out and out.get("email") and not (answer or {}).get("free_enrichment") else 0.0
		return out

	def fake_search(self, domain: str, titles: list | None = None, limit: int = 10) -> list[dict]:
		self.charged = PROSPEO_CREDIT
		return map_prospeo_search({"results": [
			{"person": {"person_id": "pr-1", "first_name": "Marta", "last_name": "Gil", "full_name": "Marta Gil",
				"current_job_title": "Purchasing Director", "linkedin_url": "https://www.linkedin.com/in/marta-gil-qa"}},
		]})[: max(1, int(limit or 10))]

	def fake_enrich(self, external_id=None, first_name=None, last_name=None, domain=None, linkedin_url=None, email=None):
		d = normalize_domain(domain or "") or "acme.qa-people.example"
		known = {"pr-1": ("pr-1", "Marta", "Gil", "Purchasing Director", "https://www.linkedin.com/in/marta-gil-qa", "marta.gil"),
			"https://www.linkedin.com/in/lucia-nunez-qa": ("pr-2", "Lucía", "Núñez", "Directora de Importaciones",
				"https://www.linkedin.com/in/lucia-nunez-qa", "lucia.nunez")}
		hit = known.get(external_id or "") or known.get(linkedin(linkedin_url) or "")
		if not hit:
			self.charged = 0.0
			return None
		pid, first, last, title, url, local = hit
		address = f"{local}@{d}"
		self.charged = PROSPEO_CREDIT
		return map_prospeo_enrich({"person": {"person_id": pid, "first_name": first, "last_name": last,
			"current_job_title": title, "linkedin_url": url, "email": {"status": "VERIFIED", "revealed": True, "email": address}}})


def _enrich_body(external_id, first_name, last_name, domain, linkedin_url, email_address, id_key: str,
		domain_key: str = "domain") -> dict:
	"""The strongest identity we have, in the provider's words; refused when there is not enough."""
	if external_id:
		return {id_key: external_id}
	url = linkedin(linkedin_url)
	if url:
		return {"linkedin_url": url}
	address = email(email_address)
	if address:
		return {"email": address}
	domain = normalize_domain(domain or "")
	if first_name and last_name and domain:
		return {"first_name": clean(first_name, 60), "last_name": clean(last_name, 60), domain_key: domain}
	raise ToolRefused(_("Not enough to look this person up: an id, a LinkedIn URL, an email, or a full name and the company's website."))
