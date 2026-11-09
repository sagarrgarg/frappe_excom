"""A safe, read-only ERP query that turns a keyword into facts. Never free SQL.

A query spec may only read the business records listed in ALLOWED (customers, items, orders,
invoices and the like), never users, mail, credentials, logs, payroll or contacts. Every field it
names - in fields, filters, order and grouping - must be a real field of that doctype, and never a
password. The engine runs it through frappe.get_list as the agent's own user, so permissions apply.
"""

import json
import re

import frappe
from frappe import _
from frappe.model.document import Document

SPEC_KEYS = {"doctype", "filters", "or_filters", "fields", "group_by", "order_by", "limit"}
ALLOWED = frozenset({
	# selling
	"Customer", "Customer Group", "Territory", "Lead", "Prospect", "Opportunity", "Quotation",
	"Sales Order", "Delivery Note", "Sales Invoice", "Sales Partner", "Campaign", "Lead Source",
	"Market Segment", "Industry Type",
	# buying
	"Supplier", "Supplier Group", "Purchase Order", "Purchase Receipt", "Purchase Invoice",
	"Request for Quotation", "Supplier Quotation",
	# stock and products
	"Item", "Item Group", "Brand", "UOM", "Item Price", "Price List", "Customs Tariff Number", "Warehouse",
	"Bin",
	# terms, places, money
	"Company", "Country", "Currency", "Incoterm", "Payment Terms Template", "Shipping Rule", "Language",
	"Payment Entry",
	# Excom AI research
	"Excom AI Company", "Excom AI Person", "Excom AI Observation",
})
STANDARD = {"name", "creation", "modified", "docstatus", "idx"}
AGGREGATE = re.compile(r"^(count|sum|avg|min|max)\(\s*(\*|[a-z_][a-z0-9_]*)\s*\)$", re.I)
ALIAS = re.compile(r"^(.+?)\s+as\s+([a-z_][a-z0-9_]*)$", re.I)
ORDER = re.compile(r"^([a-z_][a-z0-9_]*)(\s+(asc|desc))?$", re.I)


class ExcomAIRecipe(Document):
	def validate(self):
		if self.kind == "Built-in":
			if not self.handler:
				frappe.throw(_("A built-in recipe names its handler."))
			return
		check_spec(self.spec())

	def spec(self) -> dict:
		try:
			spec = json.loads(self.query_spec or "{}")
		except ValueError:
			frappe.throw(_("The query spec is not valid JSON."))
		if not isinstance(spec, dict):
			frappe.throw(_("The query spec is a JSON object."))
		return spec


def check_spec(spec: dict) -> None:
	"""Refuse anything but a plain read of an allowed doctype's own fields. Also called before running."""
	unknown = set(spec) - SPEC_KEYS
	if unknown:
		frappe.throw(_("Unknown keys in the query spec: {0}").format(", ".join(sorted(unknown))))
	doctype = spec.get("doctype")
	if not doctype or not frappe.db.exists("DocType", doctype):
		frappe.throw(_("The query spec needs an existing doctype."))
	if doctype not in ALLOWED:
		frappe.throw(_("{0} cannot be read by a recipe.").format(doctype), frappe.PermissionError)
	meta = frappe.get_meta(doctype)

	def field(name, where):
		name = str(name).strip().strip("`")
		if name in STANDARD:
			return
		df = meta.get_field(name)
		if not df:
			frappe.throw(_("{0}: {1} has no field {2}").format(where, doctype, name))
		if df.fieldtype == "Password":
			frappe.throw(_("Password fields cannot be read by a recipe."), frappe.PermissionError)

	for f in spec.get("fields") or []:
		if isinstance(f, dict):
			# v16 form: {"COUNT": "*", "as": "n"} / {"SUM": "grand_total", "as": "total"}
			fn = [k for k in f if k != "as"]
			if len(fn) != 1 or fn[0].lower() not in ("count", "sum", "avg", "min", "max") or \
					(f.get("as") and not re.match(r"^[a-z_][a-z0-9_]*$", f["as"], re.I)):
				frappe.throw(_("A computed field is {\"COUNT\": \"*\", \"as\": \"n\"} or SUM, AVG, MIN, MAX of a field."))
			if f[fn[0]] != "*":
				field(f[fn[0]], _("fields"))
			continue
		if not isinstance(f, str):
			frappe.throw(_("Fields are plain names, for example \"customer_name\" or \"count(name) as orders\"."))
		f = f.strip()
		alias = ALIAS.match(f)
		expr = alias.group(1).strip() if alias else f
		agg = AGGREGATE.match(expr)
		if agg:
			if agg.group(2) != "*":
				field(agg.group(2), _("fields"))
		else:
			field(expr, _("fields"))

	for key in ("filters", "or_filters"):
		filters = spec.get(key) or []
		if isinstance(filters, dict):
			names = list(filters)
		elif isinstance(filters, list):
			names = []
			for row in filters:
				if not isinstance(row, list | tuple) or len(row) not in (3, 4):
					frappe.throw(_("{0}: each filter is [field, operator, value].").format(key))
				if len(row) == 4 and row[0] != doctype:
					frappe.throw(_("{0}: filters on other doctypes are not allowed.").format(key))
				names.append(row[-3])
		else:
			frappe.throw(_("{0} must be a list or an object.").format(key))
		for name in names:
			field(name, key)

	for part in (spec.get("order_by") or "").split(","):
		if part.strip():
			m = ORDER.match(part.strip())
			if not m:
				frappe.throw(_("order_by is field names with asc or desc."))
			field(m.group(1), "order_by")
	for part in (spec.get("group_by") or "").split(","):
		if part.strip():
			field(part.strip(), "group_by")
	if spec.get("limit") is not None and not (isinstance(spec["limit"], int) and 0 < spec["limit"] <= 500):
		frappe.throw(_("limit is a whole number from 1 to 500."))


def run_spec(spec: dict, limit: int = 500) -> list:
	"""Run a checked query spec through frappe.get_list as the current user (permissions apply).
	"count(name) as n" strings become v16's {"COUNT": "name", "as": "n"}."""
	check_spec(spec)
	fields = []
	for f in spec.get("fields") or ["name"]:
		if isinstance(f, str):
			alias = ALIAS.match(f.strip())
			expr = alias.group(1).strip() if alias else f.strip()
			agg = AGGREGATE.match(expr)
			if agg:
				fields.append({agg.group(1).upper(): agg.group(2), "as": alias.group(2) if alias else agg.group(1).lower()})
				continue
		fields.append(f)
	return frappe.get_list(spec["doctype"], filters=spec.get("filters"), or_filters=spec.get("or_filters"), fields=fields,
		group_by=spec.get("group_by"), order_by=spec.get("order_by"), limit_page_length=min(int(spec.get("limit") or limit), 500))
