"""A safe, read-only ERP query that turns a keyword into facts. Never free SQL."""

import json

import frappe
from frappe import _
from frappe.model.document import Document

SPEC_KEYS = {"doctype", "filters", "or_filters", "fields", "group_by", "order_by", "limit"}
NEVER = {"User", "Has Role", "DocShare", "Access Log", "Activity Log", "Version", "Communication", "Email Account",
	"Excom Channel Account", "Excom AI Tool", "AI Provider", "Integration Request", "OAuth Bearer Token"}


class ExcomAIRecipe(Document):
	def validate(self):
		if self.kind == "Built-in":
			if not self.handler:
				frappe.throw(_("A built-in recipe names its handler."))
			return
		spec = self.spec()
		unknown = set(spec) - SPEC_KEYS
		if unknown:
			frappe.throw(_("Unknown keys in the query spec: {0}").format(", ".join(sorted(unknown))))
		if not spec.get("doctype") or not frappe.db.exists("DocType", spec["doctype"]):
			frappe.throw(_("The query spec needs an existing doctype."))
		if spec["doctype"] in NEVER:
			frappe.throw(_("{0} cannot be read by a recipe.").format(spec["doctype"]))
		meta = frappe.get_meta(spec["doctype"])
		for field in spec.get("fields") or []:
			if not isinstance(field, str):
				continue
			name = field.split(" as ")[0].strip("` ")
			if name not in ("name", "creation", "modified") and not meta.has_field(name):
				frappe.throw(_("{0} has no field {1}").format(spec["doctype"], name))
			if meta.has_field(name) and meta.get_field(name).fieldtype == "Password":
				frappe.throw(_("Password fields cannot be read by a recipe."))

	def spec(self) -> dict:
		try:
			spec = json.loads(self.query_spec or "{}")
		except ValueError:
			frappe.throw(_("The query spec is not valid JSON."))
		if not isinstance(spec, dict):
			frappe.throw(_("The query spec is a JSON object."))
		return spec
