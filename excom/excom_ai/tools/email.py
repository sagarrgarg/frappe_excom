# Parts of this file are ported from Harvey (github.com/ethanplusai/harvey): the email finder, its patterns
# and verifiers (harvey/integrations/email_finder.py), the Precleared check (harvey/integrations/precleared.py)
# and the TrulyInbox reader (harvey/integrations/trulyinbox.py).
#
# MIT License
#
# Copyright (c) 2026 Ethan Rogers
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
"""Addresses and what is known about them (HLD-007 §9): the email finder, three verifiers, the
Precleared consent check, TrulyInbox warm-up status and mailbox health.

The email finder never guesses blind. It reports the domain's mail host (from MX, by DNS over
HTTPS), the addresses the company publishes, and a Pattern address for a named person only when the
domain's format is known from at least one published address that belongs to a named person (or
reads as one: maria.lopez@). It never guesses an address another named person could own (jsmith@
when John and Jane Smith both work there), never one that is already someone else's or a role
inbox, and never one for a generic name ("Sales Team", a first name alone). A guess stays a guess
until a verifier says otherwise.

Catch-all is left unknown: finding it out means SMTP probing on port 25, which Harvey did and which
is not ported (blocked on most hosts, and it harms the sender's reputation). The verifiers report it.

Everything a provider sends back is untrusted text (§12.7).
"""

from __future__ import annotations

import json
import math
import re
import unicodedata

import frappe
import requests
from frappe import _
from frappe.utils import flt, now_datetime

from excom.excom_ai.tools import Tool, ToolRefused, tool
from excom.excom_ai.tools.web import normalize_domain, user_agent

TIMEOUT = (10, 30)

# ── role inboxes ─────────────────────────────────────────────────────────────

# Mailboxes that are a function, not a person (Harvey's list, plus the Spanish, Portuguese, French,
# German and Italian ones an exporter meets). They are inboxes to write to, never a naming pattern.
ROLE_LOCALS = frozenset("""
info office contact contactus hello hi team admin administrator support help sales service services
estimates quotes quote inquiries inquiry enquiries enquiry billing accounts accounting ap ar invoices payments
orders order careers jobs hr recruiting marketing media press pr news newsletter webmaster web website
postmaster hostmaster abuse noreply no-reply donotreply reception frontdesk appointments dispatch
customerservice customercare care clients client mail email general main manager management operations ops
legal privacy compliance security it tech feedback warranty claims parts shop store import imports importing
export exports purchasing purchase procurement buyer buying trade wholesale retail logistics shipping
ventas venta compras compra contacto contactenos informacion info-mx atencion atencionalcliente atencionclientes
servicioalcliente pedidos cotizaciones cotizacion facturacion facturas cobranza pagos administracion gerencia
direccion recepcion rrhh logistica almacen tienda importaciones importacion exportaciones mayoreo mayorista
comercial clientes proveedores hola soporte ayuda prensa
vendas compras contato atendimento financeiro faturamento comercial suporte sac pedidos orcamento orcamentos loja
ventes achats commercial commande commandes boutique accueil
verkauf einkauf vertrieb bestellung bestellungen kontakt buchhaltung zentrale
vendite acquisti ordini commerciale amministrazione negozio
""".split())
GENERIC_NAMES = ROLE_LOCALS | frozenset("""
staff equipo equipe user usuario test prueba cliente customer company empresa dept department departamento
oficina na none unknown desconocido sr sra srta mr mrs ms dr ing lic owner dueno manager gerente director
""".split())


def _local(email: str) -> str:
	return (email or "").strip().lower().partition("@")[0].split("+", 1)[0]


def is_role(email: str) -> bool:
	"""True for a function mailbox: info@, ventas@, ventas.mx@, compras2@, import-dept@ …"""
	local = _local(email) if "@" in (email or "") else (email or "").strip().lower()
	if local in ROLE_LOCALS:
		return True
	tokens = [t for t in re.split(r"[._\-]+", re.sub(r"\d+", "", local)) if t]
	return bool(tokens) and tokens[0] in ROLE_LOCALS


def _clean_name(value: str) -> str:
	"""A name part as it appears in an address: accents folded, letters only, the first word only
	(María José → maria; García López → garcia, the surname Spanish addresses use)."""
	folded = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode().lower()
	words = re.findall(r"[a-z]+", folded)
	return words[0] if words else ""


def is_generic_person(first: str, last: str) -> bool:
	f, l = _clean_name(first), _clean_name(last)
	return len(f) < 2 or len(l) < 2 or f in GENERIC_NAMES or l in GENERIC_NAMES


# ── patterns ─────────────────────────────────────────────────────────────────

PATTERNS = {
	"first.last": lambda f, l: f"{f}.{l}",
	"firstlast": lambda f, l: f"{f}{l}",
	"flast": lambda f, l: f"{f[0]}{l}",
	"first": lambda f, l: f,
	"f.last": lambda f, l: f"{f[0]}.{l}",
	"first_last": lambda f, l: f"{f}_{l}",
	"last": lambda f, l: l,
	"last.first": lambda f, l: f"{l}.{f}",
	"first-last": lambda f, l: f"{f}-{l}",
	"lastf": lambda f, l: f"{l}{f[0]}",
	"first.l": lambda f, l: f"{f}.{l[0]}",
	"firstl": lambda f, l: f"{f}{l[0]}",
}
# Harvey's and Hunter's spellings of the same patterns.
ALIASES = {"{f}.{l}": "first.last", "{f}{l}": "firstlast", "{fi}{l}": "flast", "{f}": "first", "{fi}.{l}": "f.last",
	"{f}_{l}": "first_last", "{l}": "last", "{l}.{f}": "last.first", "{f}-{l}": "first-last", "{l}{fi}": "lastf",
	"{first}.{last}": "first.last", "{first}{last}": "firstlast", "{f}{last}": "flast", "{first}": "first",
	"{f}.{last}": "f.last", "{first}_{last}": "first_last", "{last}": "last", "{last}.{first}": "last.first",
	"{first}-{last}": "first-last", "{last}{f}": "lastf", "{first}.{l}": "first.l", "{first}{l}": "firstl"}


def pattern_name(value: str | None) -> str | None:
	value = (value or "").strip().lower()
	return value if value in PATTERNS else ALIASES.get(value)


def build(pattern: str, first: str, last: str, domain: str) -> str:
	f, l, builder = _clean_name(first), _clean_name(last), PATTERNS.get(pattern_name(pattern) or "")
	if not (f and l and builder and domain):
		return ""
	return f"{builder(f, l)}@{domain}"


def infer_pattern(email: str, first: str, last: str) -> str | None:
	"""Which pattern turns this person's name into this address, if any."""
	local, f, l = _local(email), _clean_name(first), _clean_name(last)
	if not (local and f and l):
		return None
	return next((p for p, b in PATTERNS.items() if b(f, l) == local), None)


def _pattern_from_local(local: str) -> tuple[str, float] | None:
	"""The format an address shows when no named person owns it: maria.lopez@ → first.last, j.smith@ →
	f.last. A dotless one (jsmith@, maria@) is ambiguous alone and teaches nothing."""
	if not local or is_role(local) or any(ch.isdigit() for ch in local):
		return None
	m = re.fullmatch(r"([a-z]+)([._-])([a-z]+)", local)
	if not m:
		return None
	first, sep, last = m.groups()
	if sep == "." and len(first) == 1 and len(last) >= 2:
		return "f.last", 0.6
	if len(first) >= 2 and len(last) >= 2 and first not in GENERIC_NAMES and last not in GENERIC_NAMES:
		return {".": "first.last", "_": "first_last", "-": "first-last"}[sep], 0.6
	return None


def evidence(addresses: list[str], people: list[dict], domain: str) -> tuple[dict, str | None, float]:
	"""What the published addresses say (Harvey's site_email_evidence).

	Returns (owner by address, pattern, confidence). An address one named person's name explains is
	theirs; one two people's names explain (jsmith@ for John and Jane Smith) is nobody's but still
	shows the format; one nobody's name explains shows a format only if it reads as first.last or
	f.last. Tied addresses win over loose ones."""
	owners: dict[str, dict] = {}
	tied: dict[str, int] = {}
	loose: dict[str, int] = {}
	for addr in addresses:
		if addr.rpartition("@")[2] != domain or is_role(addr):
			continue
		matches = [(p, infer_pattern(addr, p.get("first_name"), p.get("last_name"))) for p in people]
		matches = [(p, pat) for p, pat in matches if pat and not is_generic_person(p.get("first_name"), p.get("last_name"))]
		if len(matches) == 1:
			owners[addr] = matches[0][0]
			tied[matches[0][1]] = tied.get(matches[0][1], 0) + 1
		elif matches and len({pat for _p, pat in matches}) == 1:
			loose[matches[0][1]] = loose.get(matches[0][1], 0) + 2
		elif not matches:
			guess = _pattern_from_local(_local(addr))
			if guess:
				loose[guess[0]] = loose.get(guess[0], 0) + 1
	if tied:
		best = max(tied, key=tied.get)
		return owners, best, 0.95 if tied[best] >= 2 else 0.9
	if loose:
		best = max(loose, key=loose.get)
		return owners, best, min(0.55 + 0.05 * loose[best], 0.7)
	return owners, None, 0.0


# ── MX ───────────────────────────────────────────────────────────────────────

GATEWAYS = ("pphosted", "mimecast", "barracuda", "proofpoint", "messagelabs", "iphmx", "fortimail", "trendmicro",
	"sophos", "spamexperts", "mailcontrol", "forcepoint", "cisco", "hornetsecurity", "antispamcloud")


def classify_mx(host: str | None) -> str:
	h = (host or "").lower().rstrip(".")
	if not h:
		return "None"
	if "google" in h or "googlemail" in h:
		return "Google"
	if "outlook" in h or "microsoft" in h:
		return "Microsoft"
	if any(g in h for g in GATEWAYS):
		return "Gateway"
	return "Other"


def mx_hosts(domain: str) -> list[str] | None:
	"""The domain's MX hosts, best first; [] when it has none (or a null MX); None when DNS failed."""
	from excom.excom_ai import mail

	try:
		answers = mail.dns_txt(domain, "MX")
	except Exception:
		return None
	rows = []
	for a in answers:
		m = re.match(r"\s*(\d+)\s+(\S+)", a or "")
		if m and m.group(2).rstrip("."):
			rows.append((int(m.group(1)), m.group(2).rstrip(".").lower()))
	return [h for _p, h in sorted(rows)]


# ── the finder ───────────────────────────────────────────────────────────────


def _address(email, source, person=None, inbox=False, hint="Published") -> dict:
	person = person or {}
	return {"email": email, "source": source, "first_name": person.get("first_name") or None,
		"last_name": person.get("last_name") or None, "title": person.get("title") or None, "is_inbox": inbox,
		"status_hint": hint}


def find(domain: str, published: list[str] | None = None, people: list[dict] | None = None,
		known: list[dict] | None = None, use_stored: bool = True, hosts: list[str] | None = None) -> dict:
	"""See EmailFinder.find. `hosts` skips the MX lookup (the fake)."""
	domain = normalize_domain(domain)
	if not domain:
		raise ToolRefused(_("No domain to look up."))
	hosts = mx_hosts(domain) if hosts is None else hosts
	out = {"domain": domain, "mx_type": None if hosts is None else classify_mx(hosts[0] if hosts else None),
		"mx_hosts": hosts or [], "catch_all": None, "pattern": None, "pattern_source": None,
		"pattern_confidence": 0.0, "addresses": []}
	people = [p for p in (people or []) if p.get("first_name") or p.get("last_name")]
	known = [k for k in (known or []) if (k.get("email") or "").strip()]
	published = list(dict.fromkeys((e or "").strip().lower() for e in (published or []) if "@" in (e or "")))

	# Named evidence: published addresses, and named ones from elsewhere (Hunter, an import).
	named = list({(_clean_name(p.get("first_name")), _clean_name(p.get("last_name"))): p
		for p in people + [k for k in known if k.get("first_name") or k.get("last_name")]}.values())
	owners, pattern, confidence = evidence(published, named, domain)
	source = "Published" if pattern else None
	for k in known:
		email = k["email"].strip().lower()
		pat = infer_pattern(email, k.get("first_name"), k.get("last_name"))
		if email.rpartition("@")[2] == domain and pat and not is_generic_person(k.get("first_name"), k.get("last_name")):
			owners.setdefault(email, k)
			if not pattern:
				pattern, confidence, source = pat, 0.85, k.get("source") or "Hunter"
	if not pattern and use_stored:
		stored = frappe.db.get_value("Excom AI Email Domain", domain, ["pattern", "pattern_source", "pattern_confidence"],
			as_dict=True)
		if stored and pattern_name(stored.pattern) and stored.pattern_source in ("Published", "Hunter") \
				and flt(stored.pattern_confidence) >= 0.6:
			pattern, source, confidence = pattern_name(stored.pattern), stored.pattern_source, flt(stored.pattern_confidence)
	out.update({"pattern": pattern, "pattern_source": source, "pattern_confidence": round(confidence, 2)})

	addresses, taken = [], set()
	for email in published:
		on_domain = email.rpartition("@")[2] in (domain, f"www.{domain}") or email.endswith("." + domain)
		if is_role(email):
			addresses.append(_address(email, "Published", inbox=True))
		elif on_domain:
			addresses.append(_address(email, "Published", owners.get(email)))
		else:
			continue  # a personal address on another domain is not the company's
		taken.add(email)
	for k in known:
		email = k["email"].strip().lower()
		if email not in taken:
			addresses.append(_address(email, k.get("source") or "Hunter", k, inbox=is_role(email)))
			taken.add(email)

	if pattern:
		has_own = {(_clean_name(p.get("first_name")), _clean_name(p.get("last_name"))) for p in owners.values()}
		for person in people:
			me = (_clean_name(person.get("first_name")), _clean_name(person.get("last_name")))
			if me in has_own or is_generic_person(*me):
				continue
			candidate = build(pattern, *me, domain)
			if not candidate or candidate in taken or is_role(candidate):
				continue  # already someone's, or a function mailbox
			others = [o for o in named if (_clean_name(o.get("first_name")), _clean_name(o.get("last_name"))) != me]
			if any(build(pattern, o.get("first_name"), o.get("last_name"), domain) == candidate for o in others):
				continue  # another named person could own it
			addresses.append(_address(candidate, "Pattern", person, hint="Guess"))
			taken.add(candidate)
	out["addresses"] = addresses
	return out


def save_domain(found: dict) -> str:
	"""Upsert the Excom AI Email Domain record from a find() result. A weaker pattern never replaces
	a stronger one, an unknown MX or catch-all never clears a known one."""
	domain = found["domain"]
	doc = frappe.get_doc("Excom AI Email Domain", domain) if frappe.db.exists("Excom AI Email Domain", domain) \
		else frappe.new_doc("Excom AI Email Domain")
	doc.domain = domain
	if found.get("mx_type"):
		doc.mx_type = found["mx_type"]
	new = pattern_name(found.get("pattern"))
	stronger = flt(found.get("pattern_confidence")) >= flt(doc.pattern_confidence)
	if new and (not doc.pattern or new == doc.pattern or stronger):
		doc.pattern, doc.pattern_source = new, found.get("pattern_source")
		doc.pattern_confidence = flt(found.get("pattern_confidence"))
	if found.get("catch_all") is not None:
		doc.catch_all = 1 if found["catch_all"] else 0
	if found.get("hunter_searched"):
		doc.hunter_searched_on = now_datetime()
	doc.checked_on = now_datetime()
	doc.save(ignore_permissions=True) if not doc.is_new() else doc.insert(ignore_permissions=True)
	return doc.name


@tool("email_finder", tier="read", cost=0.0)
class EmailFinder(Tool):
	"""find(domain, published=[emails], people=[{first_name, last_name, title}], known=[{email, first_name,
	last_name, title, source}]) -> {domain, mx_type: Google|Microsoft|Gateway|Other|"None" (no MX)|None (DNS
	failed), mx_hosts, catch_all: None, pattern, pattern_source, pattern_confidence, addresses: [{email,
	source: Published|Pattern|Hunter…, first_name, last_name, title, is_inbox, status_hint: Published|Guess}]}.

	`known` is for named addresses found elsewhere (Hunter's domain search): they count as evidence of
	the pattern and come back in `addresses` with their own source."""

	def find(self, domain: str, published: list | None = None, people: list | None = None, known: list | None = None):
		return find(domain, published, people, known)

	def fake_find(self, domain: str, published: list | None = None, people: list | None = None,
			known: list | None = None):
		return find(domain, published, people, known, use_stored=False, hosts=["aspmx.l.google.com",
			"alt1.aspmx.l.google.com"])


# ── verifiers ────────────────────────────────────────────────────────────────


def _get(url: str, params: dict, label: str, secret: str) -> dict:
	"""One GET to a verifier. A failure raises with the status, never the key or the body."""
	try:
		r = requests.get(url, params=params, timeout=TIMEOUT, headers={"User-Agent": user_agent()})
	except requests.RequestException as e:
		raise RuntimeError(f"{label}: no answer ({type(e).__name__})") from None
	if r.status_code != 200:
		raise RuntimeError(f"{label}: HTTP {r.status_code}")
	try:
		data = r.json()
	except ValueError:
		raise RuntimeError(f"{label}: an answer that is not JSON") from None
	if not isinstance(data, dict):
		raise RuntimeError(f"{label}: an unexpected answer")
	return _redact(data, secret)


def _redact(data, secret: str):
	if not secret:
		return data
	return json.loads(json.dumps(data).replace(secret, "[hidden]"))


def _verdict(status: str, catch_all: bool | None, raw: dict) -> dict:
	return {"status": status, "catch_all": catch_all, "raw": raw}


REOON = {"safe": "Verified", "valid": "Verified", "invalid": "Invalid", "disabled": "Invalid", "disposable": "Invalid",
	"spamtrap": "Invalid", "catch_all": "Risky", "inbox_full": "Risky", "unknown": "Unknown"}


def map_reoon(data: dict) -> dict:
	"""Reoon power mode: safe, valid, invalid, disabled, disposable, inbox_full, catch_all, role_account,
	spamtrap, unknown. A role account (info@) counts as verified only when Reoon also says deliverable."""
	status = str(data.get("status") or "").lower()
	if status == "error":
		raise RuntimeError(f"Reoon: {str(data.get('reason') or 'error')[:120]}")
	catch_all = bool(data.get("is_catch_all") or status == "catch_all")
	if status == "role_account":
		mapped = "Verified" if data.get("is_deliverable") is True else "Unknown"
	else:
		mapped = REOON.get(status, "Unknown")
	if catch_all and mapped == "Verified":
		mapped = "Risky"
	return _verdict(mapped, catch_all, data)


@tool("reoon", tier="paid", cost=0.001, needs_key=True)
class Reoon(Tool):
	"""verify(email) -> {status: Verified|Risky|Invalid|Unknown, catch_all, raw}"""

	def verify(self, email: str) -> dict:
		key = self.secret()
		return map_reoon(_get("https://emailverifier.reoon.com/api/v1/verify",
			{"email": email, "key": key, "mode": "power"}, "Reoon", key))

	def fake_verify(self, email: str) -> dict:
		return map_reoon(_fake_provider(email, {"safe": {"status": "safe", "is_catch_all": False, "is_deliverable": True},
			"catch_all": {"status": "catch_all", "is_catch_all": True}, "invalid": {"status": "invalid"},
			"unknown": {"status": "unknown"}}) | {"email": email})


ZEROBOUNCE = {"valid": "Verified", "invalid": "Invalid", "catch-all": "Risky", "unknown": "Unknown",
	"spamtrap": "Invalid", "abuse": "Risky", "do_not_mail": "Invalid"}


def map_zerobounce(data: dict) -> dict:
	"""ZeroBounce: valid, invalid, catch-all, unknown, spamtrap, abuse, do_not_mail (with a sub_status).
	do_not_mail for a role address (role_based) is Risky, not Invalid: role inboxes are what we write to."""
	if data.get("error"):
		raise RuntimeError(f"ZeroBounce: {str(data['error'])[:120]}")
	status, sub = str(data.get("status") or "").lower(), str(data.get("sub_status") or "").lower()
	catch_all = status == "catch-all" or sub in ("catch-all", "role_based_catch_all")
	mapped = ZEROBOUNCE.get(status, "Unknown")
	if status == "do_not_mail" and sub in ("role_based", "role_based_catch_all"):
		mapped = "Risky"
	return _verdict(mapped, catch_all, data)


@tool("zerobounce", tier="paid", cost=0.008, needs_key=True)
class ZeroBounce(Tool):
	"""verify(email) -> {status: Verified|Risky|Invalid|Unknown, catch_all, raw}"""

	def verify(self, email: str) -> dict:
		key = self.secret()
		return map_zerobounce(_get("https://api.zerobounce.net/v2/validate", {"api_key": key, "email": email},
			"ZeroBounce", key))

	def fake_verify(self, email: str) -> dict:
		return map_zerobounce(_fake_provider(email, {"safe": {"status": "valid", "sub_status": ""},
			"catch_all": {"status": "catch-all", "sub_status": ""},
			"invalid": {"status": "invalid", "sub_status": "mailbox_not_found"},
			"unknown": {"status": "unknown", "sub_status": "timeout_exceeded"}}) | {"address": email})


HUNTER = {"valid": "Verified", "invalid": "Invalid", "accept_all": "Risky", "disposable": "Invalid",
	"unknown": "Unknown"}
HUNTER_SEARCH_COST = 0.025  # a domain search is one credit; pass it as use(..., cost=) when budgets matter


def map_hunter(data: dict) -> dict:
	"""Hunter's email-verifier `status`: valid, invalid, accept_all, webmail, disposable, unknown. A
	webmail address (gmail) is graded by Hunter's `result` instead."""
	d = data.get("data") or {}
	status = str(d.get("status") or "").lower()
	if status == "webmail":
		mapped = {"deliverable": "Verified", "undeliverable": "Invalid", "risky": "Risky"}.get(str(d.get("result")),
			"Unknown")
	else:
		mapped = HUNTER.get(status, "Unknown")
	catch_all = bool(d.get("accept_all")) or status == "accept_all"
	if catch_all and mapped == "Verified":
		mapped = "Risky"
	return _verdict(mapped, catch_all, data)


def map_hunter_search(data: dict, domain: str) -> list[dict]:
	d = data.get("data") or {}
	pattern = pattern_name(d.get("pattern"))
	out = []
	for e in d.get("emails") or []:
		email = str(e.get("value") or "").strip().lower()
		if not email or "@" not in email:
			continue
		out.append({"email": email, "source": "Hunter", "first_name": e.get("first_name") or None,
			"last_name": e.get("last_name") or None, "title": e.get("position") or None,
			"is_inbox": e.get("type") == "generic" or is_role(email),
			"status_hint": "Published" if e.get("sources") else "Guess", "confidence": e.get("confidence"),
			"department": e.get("department"), "seniority": e.get("seniority"), "linkedin_url": e.get("linkedin"),
			"verification": (e.get("verification") or {}).get("status"), "domain_pattern": pattern, "domain": domain})
	return out


@tool("hunter", tier="paid", cost=0.012, needs_key=True)
class Hunter(Tool):
	"""verify(email) -> {status, catch_all, raw}; domain_search(domain, limit=10) -> [address dicts with
	source "Hunter", first_name, last_name, title, is_inbox, status_hint, confidence, domain_pattern]."""

	def verify(self, email: str) -> dict:
		key = self.secret()
		return map_hunter(_get("https://api.hunter.io/v2/email-verifier", {"email": email, "api_key": key}, "Hunter", key))

	def fake_verify(self, email: str) -> dict:
		return map_hunter({"data": _fake_provider(email, {"safe": {"status": "valid", "result": "deliverable"},
			"catch_all": {"status": "accept_all", "accept_all": True, "result": "risky"},
			"invalid": {"status": "invalid", "result": "undeliverable"}, "unknown": {"status": "unknown"}}) | {"email": email}})

	def domain_search(self, domain: str, limit: int = 10) -> list[dict]:
		key, domain = self.secret(), normalize_domain(domain)
		return map_hunter_search(_get("https://api.hunter.io/v2/domain-search",
			{"domain": domain, "api_key": key, "limit": max(1, min(int(limit or 10), 100))}, "Hunter", key), domain)

	def fake_domain_search(self, domain: str, limit: int = 10) -> list[dict]:
		domain = normalize_domain(domain)
		return map_hunter_search({"data": {"domain": domain, "pattern": "{first}.{last}", "emails": [
			{"value": f"carlos.ramirez@{domain}", "type": "personal", "confidence": 94, "first_name": "Carlos",
				"last_name": "Ramírez", "position": "Gerente de Compras", "seniority": "senior", "department": "management",
				"sources": [{"uri": f"https://{domain}/nosotros"}], "verification": {"status": "valid"}},
			{"value": f"compras@{domain}", "type": "generic", "confidence": 91, "sources": [{"uri": f"https://{domain}/"}]},
		]}}, domain)[: max(1, int(limit or 10))]


def _fake_provider(email: str, answers: dict) -> dict:
	"""Canned answers by the address's shape, so tests can pick an outcome: invalid…@, catchall…@,
	unknown…@, anything else is deliverable."""
	local = _local(email)
	kind = "invalid" if local.startswith(("invalid", "bounce")) else "catch_all" if local.startswith("catchall") \
		else "unknown" if local.startswith("unknown") else "safe"
	return dict(answers[kind])


# ── consent: Precleared ──────────────────────────────────────────────────────

PRECLEARED_URL = "https://precleared.dev/api/v1/check"
PRECLEARED = {"ok_to_email": "OK to email", "consent_needed": "Consent needed", "blocked": "Blocked",
	"unknown": "Unknown"}


def country_code(country: str | None) -> str:
	"""A Frappe country name (or a code) as an upper-case ISO 3166 alpha-2 code, or ""."""
	if not country:
		return ""
	code = frappe.db.get_value("Country", country, "code") if frappe.db.exists("Country", country) else ""
	code = (code or (country if len(country.strip()) == 2 else "")).strip().upper()
	return code if re.fullmatch(r"[A-Z]{2}", code) else ""


def map_precleared(data: dict) -> dict:
	verdict = data.get("verdict")
	if verdict not in PRECLEARED:
		raise RuntimeError("Precleared: an answer without a known verdict")
	reasons = data.get("reasons")
	first = reasons[0] if isinstance(reasons, list) and reasons and isinstance(reasons[0], dict) else {}
	reason = " ".join(str(x).strip() for x in (first.get("text") or first.get("note"), data.get("next_step")) if x)
	receipt = data.get("receipt") if isinstance(data.get("receipt"), dict) else {}
	facts = data.get("facts") if isinstance(data.get("facts"), dict) else {}
	return {"verdict": PRECLEARED[verdict], "reason": reason[:240] or None, "receipt_id": receipt.get("id"),
		"country": facts.get("recipient_country"), "raw": data}


@tool("precleared", tier="paid", cost=0.01, needs_key=True)
class Precleared(Tool):
	"""check(email, country, sender_country=None, company=None, name=None, address_source="published",
	pitch=None) -> {verdict: OK to email|Consent needed|Blocked|Unknown, reason, receipt_id, country, raw}.

	`country` is the recipient's Frappe country (sent as recipient_country when known). The sender's
	country defaults to System Settings' country. A definite verdict should be cached 30 days by the
	caller (Excom AI Person.consent_checked_on)."""

	def check(self, email: str, country: str | None = None, sender_country: str | None = None, company: str | None = None,
			name: str | None = None, address_source: str = "published", pitch: str | None = None) -> dict:
		key = self.secret()
		payload = {"recipient_email": (email or "").strip().lower(), "existing_customer": False,
			"sender_country": country_code(sender_country or frappe.db.get_single_value("System Settings", "country")),
			"address_source": address_source if address_source in ("published", "bought", "scraped", "given", "unknown")
				else "unknown"}
		extra = {"recipient_country": country_code(country), "company_name": " ".join((company or "").split())[:160],
			"recipient_name": " ".join((name or "").split())[:120], "pitch": (pitch or "").strip()[:200]}
		payload.update({k: v for k, v in extra.items() if v})
		try:
			r = requests.post(PRECLEARED_URL, json=payload, timeout=(10, 40),
				headers={"Authorization": f"Bearer {key}", "User-Agent": user_agent()})
		except requests.RequestException as e:
			raise RuntimeError(f"Precleared: no answer ({type(e).__name__})") from None
		problems = {401: "the key was rejected", 402: "no checks left on the key",
			429: "the rate limit or daily allowance is used"}
		if r.status_code != 200:
			raise RuntimeError(f"Precleared: {problems.get(r.status_code, f'HTTP {r.status_code}')}")
		try:
			data = r.json()
		except ValueError:
			raise RuntimeError("Precleared: an answer that is not JSON") from None
		if not isinstance(data, dict):
			raise RuntimeError("Precleared: an unexpected answer")
		return map_precleared(_redact(data, key))

	def fake_check(self, email: str, country: str | None = None, sender_country: str | None = None,
			company: str | None = None, name: str | None = None, address_source: str = "published",
			pitch: str | None = None) -> dict:
		code = country_code(country)
		if _local(email).startswith("blocked"):
			data = {"verdict": "blocked", "reasons": [{"text": "The recipient objected to marketing."}]}
		elif code in ("DE", "FR") and not is_role(email):
			data = {"verdict": "consent_needed", "reasons": [{"text": "Prior consent is required for a named person.",
				"source_title": "UWG §7"}], "next_step": "Write to the company's published inbox instead."}
		else:
			data = {"verdict": "ok_to_email", "reasons": [{"text": "Business address, relevant offer."}]}
		return map_precleared(data | {"receipt": {"id": "rcpt_fake_0001"}, "facts": {"recipient_country": code or None}})


# ── warm-up: TrulyInbox ──────────────────────────────────────────────────────

TRULYINBOX_API = "https://lynx-api.trulyinbox.com"
_WARM = {"active", "warming", "enabled", "running"}
_LOGIN_FAILED = ("auth_error", "auth_expired", "auth_failed", "suspended_auth", "blocked", "disconnect")


def _num(x) -> float | None:
	return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) else None


@tool("trulyinbox", tier="read", cost=0.0, needs_key=True)
class TrulyInbox(Tool):
	"""status(mailbox_email) -> {found, warming, login_ok, score, started_on, sent, spam, raw}. Read only:
	every call is a GET. The workspace id is the tool record's Account ID, else the key's first workspace."""

	def _get(self, path: str, params: dict | None = None):
		key = self.secret()
		try:
			r = requests.get(TRULYINBOX_API + path, params=params, timeout=(10, 20),
				headers={"Authorization": f"Bearer {key}", "User-Agent": user_agent()})
		except requests.RequestException as e:
			raise RuntimeError(f"TrulyInbox: no answer ({type(e).__name__})") from None
		try:
			body = r.json() if r.content else {}
		except ValueError:
			body = None
		envelope = body if isinstance(body, dict) else {}
		if r.ok and body is not None and envelope.get("success") is not False and not envelope.get("errorCode"):
			return envelope.get("data")
		problems = {401: "the key was rejected", 403: "the key lacks a scope or the plan has no API access",
			429: "the API limit is reached"}
		raise RuntimeError(f"TrulyInbox: {problems.get(r.status_code, f'HTTP {r.status_code}')}")

	def status(self, mailbox_email: str) -> dict:
		address = (mailbox_email or "").strip()
		workspace = str((self.record and self.record.get("account_id")) or "").strip()
		if not workspace.isdigit():
			spaces = [w for w in self._get("/v2/workspaces") or [] if isinstance(w, dict) and str(w.get("id", "")).isdigit()]
			if not spaces:
				raise RuntimeError("TrulyInbox: no workspace found for the key")
			workspace = str(spaces[0]["id"])
		base = f"/v2/workspaces/{workspace}"
		accounts = self._get(f"{base}/accounts", {"q": address, "limit": 25}) or []
		hit = next((a for a in accounts if isinstance(a, dict) and str(a.get("emailAddress") or "").strip().lower()
			== address.lower() and str(a.get("id", "")).isdigit()), None)
		if not hit:
			return {"found": False, "warming": False, "login_ok": None, "score": None, "started_on": None, "sent": 0,
				"spam": 0, "raw": {"accounts": len(accounts)}}
		warmup = self._get(f"{base}/accounts/{hit['id']}/warmup") or {}
		stats = self._get(f"{base}/accounts/{hit['id']}/analytics/stats", {"days": 14}) or {}
		return map_trulyinbox(address, warmup, stats)

	def fake_status(self, mailbox_email: str) -> dict:
		return map_trulyinbox(mailbox_email, {"warmupStatus": "active", "status": "ok",
			"warmupStartDate": "2026-10-01T00:00:00Z"},
			{"deliverabilityRate": 97.5, "dailyBreakdown": [{"sent": 20, "spamDetected": 0}, {"sent": 22, "spamDetected": 1}]})


def map_trulyinbox(address: str, warmup: dict, stats: dict) -> dict:
	warmup = warmup if isinstance(warmup, dict) else {}
	stats = stats if isinstance(stats, dict) else {}
	tokens = [str(warmup.get(k) or "").lower() for k in ("status", "accountState", "statusBucket", "warmupPausedReason")]
	login_ok = not any(w in t for t in tokens for w in _LOGIN_FAILED) and warmup.get("sendCheckFailed") is not True \
		and warmup.get("receiveCheckFailed") is not True
	days = [d for d in stats.get("dailyBreakdown") or [] if isinstance(d, dict)]
	return {"found": True, "address": address,
		"warming": str(warmup.get("warmupStatus") or "").lower() in _WARM and login_ok, "login_ok": login_ok,
		"score": _num(stats.get("deliverabilityRate")), "started_on": (str(warmup.get("warmupStartDate") or "")[:10] or None),
		"sent": int(sum(_num(d.get("sent")) or 0 for d in days)),
			"spam": int(sum(_num(d.get("spamDetected")) or 0 for d in days)),
		"raw": {"warmup": warmup, "stats": stats}}


# ── mailbox health (a thin adapter over excom_ai.mail) ───────────────────────


@tool("mailbox_health", tier="read", cost=0.0)
class MailboxHealth(Tool):
	"""check(domain, selector=None) -> mail.check_domain's {spf, dkim, dmarc, mx, blocklisted, ok, …}."""

	def check(self, domain: str, selector: str | None = None) -> dict:
		from excom.excom_ai import mail

		return mail.check_domain(normalize_domain(domain), selector)

	def fake_check(self, domain: str, selector: str | None = None) -> dict:
		domain = normalize_domain(domain)
		return {"domain": domain, "checked_on": str(now_datetime()), "spf": "v=spf1 include:_spf.google.com ~all",
			"dmarc": "v=DMARC1; p=quarantine", "mx": ["1 smtp.google.com."], "dkim": "v=DKIM1; k=rsa; p=MIIB…",
			"dkim_selector": selector or "google", "blocklisted": False, "ok": True}
