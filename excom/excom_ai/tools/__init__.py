"""Outside services an agent may use (HLD-007 §9): one interface, one set of limits.

Each tool is a class registered with @tool. It has a tier (read, draft, communicate, paid), an
estimated cost per call, and a fake used in tests so nothing touches the network:

    @tool("serper", tier="paid", cost=0.001)
    class Serper(Tool):
        def search(self, query: dict) -> list[dict]: ...
        def fake_search(self, query: dict) -> list[dict]: ...

Every call goes through `use(key, method, **kwargs)`, which:
- refuses a tool that is switched off (Excom AI Tool.enabled) or has no key when it needs one;
- refuses a paid call that would pass the tool's monthly budget, and counts the spend;
- keeps to the tool's rate limit (calls a minute);
- records the last error on the tool;
- runs the fake instead when `frappe.flags.excom_ai_fake_tools` is set (tests) or the tool
  record says so.

What a tool returns is untrusted text (§12.7): it is data for prompts and records, never
instructions.

Interfaces by kind (each adapter implements the ones it offers):
- discovery: `search(query) -> [company dict]`, query = {keywords, countries, languages,
  category, limit}; a company dict has name, domain, website, country, city, phone, source,
  source_url, external_id, industry, buyer_type, signals {code: value}.
- website profiler: `profile(url) -> {title, description, language, emails, phones,
  has_online_store, pages, text, links}`.
- email finder: `find(domain, published, people) -> {mx_type, pattern, addresses: [...]}`.
- verifier: `verify(email) -> {status: Verified|Risky|Invalid|Unknown, catch_all, raw}`.
- consent: `check(email, country) -> {verdict: OK to email|Consent needed|Blocked|Unknown}`.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt, get_first_day, nowdate

TOOLS: dict[str, type] = {}
MODULES = [
	"excom.excom_ai.tools.discovery",
	"excom.excom_ai.tools.web",
	"excom.excom_ai.tools.email",
]
_loaded = False


class ToolRefused(frappe.ValidationError):
	pass


class Tool:
	key: str = ""
	tier: str = "read"
	cost: float = 0.0  # estimated cost of one call, in USD
	needs_key: bool = False

	def __init__(self, record=None):
		self.record = record

	def secret(self, field: str = "api_key") -> str:
		if not self.record:
			return ""
		return self.record.get_password(field, raise_exception=False) or ""


def tool(key: str, tier: str = "read", cost: float = 0.0, needs_key: bool = False):
	def wrap(cls):
		cls.key, cls.tier, cls.cost, cls.needs_key = key, tier, cost, needs_key
		TOOLS[key] = cls
		return cls

	return wrap


def load():
	global _loaded
	if _loaded:
		return
	for m in MODULES:
		try:
			frappe.get_module(m)
		except ImportError:
			pass
	_loaded = True


def available(key: str) -> bool:
	load()
	if key not in TOOLS:
		return False
	if frappe.flags.excom_ai_fake_tools:
		return True
	return bool(frappe.db.get_value("Excom AI Tool", key, "enabled"))


def use(key: str, method: str, cost: float | None = None, **kwargs):
	"""Call one tool method under its limits. Raises ToolRefused when it may not run now."""
	load()
	cls = TOOLS.get(key)
	if not cls:
		raise ToolRefused(_("No tool called {0}.").format(key))
	fake = bool(frappe.flags.excom_ai_fake_tools)
	record = None if fake else (frappe.get_doc("Excom AI Tool", key) if frappe.db.exists("Excom AI Tool", key) else None)
	if not fake:
		if not record or not record.enabled:
			raise ToolRefused(_("The tool {0} is switched off.").format(key))
		if cls.needs_key and not record.get_password("api_key", raise_exception=False):
			raise ToolRefused(_("The tool {0} has no key.").format(key))
	instance = cls(record)
	estimate = flt(cost if cost is not None else cls.cost)
	if not fake and record and cls.tier == "paid":
		_check_budget(record, estimate)
	if not fake and record:
		_check_rate(record)
	fn = getattr(instance, f"fake_{method}" if fake else method, None)
	if not fn:
		raise ToolRefused(_("{0} cannot {1}.").format(key, method))
	try:
		out = fn(**kwargs)
	except ToolRefused:
		raise
	except Exception as e:
		if record:
			frappe.db.set_value("Excom AI Tool", key, "last_error", f"{type(e).__name__}: {e!s}"[:500], update_modified=False)
		raise
	if not fake and record and cls.tier == "paid":
		_spend(record, estimate)
	return out


def _check_budget(record, estimate: float) -> None:
	_roll_month(record)
	budget = flt(record.monthly_budget)
	if budget > 0 and flt(record.used_this_month) + estimate > budget:
		raise ToolRefused(_("{0}: the monthly budget of {1} is spent.").format(record.label or record.name, budget))


def _roll_month(record) -> None:
	"""used_this_month starts again on the 1st (the date is kept in the tool's cache)."""
	key = f"excom_ai_tool_month:{record.name}"
	month = str(get_first_day(nowdate()))
	if frappe.cache.get_value(key) != month:
		if frappe.cache.get_value(key) is not None:
			record.db_set("used_this_month", 0, update_modified=False)
		frappe.cache.set_value(key, month)


def _spend(record, amount: float) -> None:
	frappe.db.sql("UPDATE `tabExcom AI Tool` SET used_this_month = IFNULL(used_this_month, 0) + %s WHERE name = %s",
		(amount, record.name))


def _check_rate(record) -> None:
	limit = int(record.rate_limit or 0)
	if limit <= 0:
		return
	key = frappe.cache.make_key(f"excom_ai_tool_rate:{record.name}:{frappe.utils.now_datetime().strftime('%Y%m%d%H%M')}")
	n = frappe.cache.incrby(key, 1)
	frappe.cache.expire(key, 120)
	if n > limit:
		raise ToolRefused(_("{0}: at most {1} calls a minute.").format(record.label or record.name, limit))
