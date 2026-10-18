"""Ad platform suppliers: one ERPNext Supplier per legal entity that issues the bill, never one per
platform. Facebook India and Meta Ireland are two suppliers with different GST treatment (domestic
forward charge vs import of service under reverse charge), even though both are "Meta".

Nothing here runs on install. `propose()` lists what is missing; `create()` makes it, only when an
admin confirms (api_bills.create_suppliers).

GSTINs are taken from what the user gave us; a GSTIN that is not known ("from the invoice") must be
typed in before that supplier can be created, because India Compliance does not accept a Registered
Regular supplier without one.
"""

import re

import frappe
from frappe import _

# key -> legal entity. `aliases` are matched against the billing entity a platform reports (Meta's
# invoice `entity`) after normalising to lower-case letters and digits. An account map can also name
# the exact entity code it expects (Ads Bill Account Map.billing_entity).
LEGAL_ENTITIES = {
	"meta_in": {
		"provider": "Meta",
		"supplier_name": "Facebook India Online Services Pvt Ltd",
		"gst_category": "Registered Regular",
		"country": "India",
		"state": "Haryana",
		"gstin": "06AABCF5150G1ZZ",
		"pan": "AABCF5150G",
		"reverse_charge": 0,
		"aliases": ("facebook india", "facebook india online services", "fbin", "fb india", "meta india"),
	},
	"meta_ie": {
		"provider": "Meta",
		"supplier_name": "Meta Platforms Ireland Ltd",
		"gst_category": "Overseas",
		"country": "Ireland",
		"state": "",
		"gstin": "",
		"pan": "",
		"reverse_charge": 1,
		"aliases": ("meta platforms ireland", "facebook ireland", "fbie", "fb ireland", "meta ireland"),
	},
	"google_in": {
		"provider": "Google",
		"supplier_name": "Google India Pvt Ltd",
		"gst_category": "Registered Regular",
		"country": "India",
		"state": "Haryana",
		"gstin": "",  # printed on the invoice; type it in when creating
		"pan": "AACCG0527D",
		"reverse_charge": 0,
		"aliases": ("google india",),
	},
	"google_apac": {
		"provider": "Google",
		"supplier_name": "Google Asia Pacific Pte Ltd",
		"gst_category": "Overseas",
		"country": "Singapore",
		"state": "",
		"gstin": "",
		"pan": "",
		"reverse_charge": 1,
		"aliases": ("google asia pacific",),
	},
	"linkedin_in": {
		"provider": "LinkedIn",
		"supplier_name": "LinkedIn India Sales Pvt Ltd",
		"gst_category": "Registered Regular",
		"country": "India",
		"state": "Karnataka",
		"gstin": "",
		"pan": "",
		"reverse_charge": 0,
		"aliases": ("linkedin india",),
	},
	"linkedin_sg": {
		"provider": "LinkedIn",
		"supplier_name": "LinkedIn Singapore Pte Ltd",
		"gst_category": "Overseas",
		"country": "Singapore",
		"state": "",
		"gstin": "",
		"pan": "",
		"reverse_charge": 1,
		"aliases": ("linkedin singapore",),
	},
}

ADDRESS_PLACEHOLDER = "(complete from the platform's invoice)"


def norm(text) -> str:
	return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def match_entity(provider: str | None, entity_text: str | None) -> str | None:
	"""The legal entity key a reported billing entity names, or None when it is not recognised."""
	n = norm(entity_text)
	if not n:
		return None
	for key, e in LEGAL_ENTITIES.items():
		if provider and e["provider"] != provider:
			continue
		if n == norm(e["supplier_name"]) or any(norm(a) and (n == norm(a) or norm(a) in n) for a in e["aliases"]):
			return key
	return None


def find_supplier(key: str) -> str | None:
	e = LEGAL_ENTITIES[key]
	if e["gstin"]:
		name = frappe.db.get_value("Supplier", {"gstin": e["gstin"], "disabled": 0}, "name") if frappe.get_meta("Supplier").has_field("gstin") else None
		if name:
			return name
	return frappe.db.get_value("Supplier", {"supplier_name": e["supplier_name"]}, "name")


def key_for_supplier(supplier: str) -> str | None:
	"""Which catalogue entity an existing Supplier is: by GSTIN first, then by exact legal name."""
	if not supplier:
		return None
	meta = frappe.get_meta("Supplier")
	fields = ["supplier_name"] + [f for f in ("gstin", "gst_category") if meta.has_field(f)]
	row = frappe.db.get_value("Supplier", supplier, fields, as_dict=True)
	if not row:
		return None
	for key, e in LEGAL_ENTITIES.items():
		if e["gstin"] and row.get("gstin") == e["gstin"]:
			return key
	for key, e in LEGAL_ENTITIES.items():
		if norm(row.supplier_name) == norm(e["supplier_name"]):
			return key
	return None


def propose() -> list[dict]:
	"""What exists and what would be created. Read-only."""
	out = []
	for key, e in LEGAL_ENTITIES.items():
		existing = find_supplier(key)
		needs = []
		if e["gst_category"] != "Overseas" and not e["gstin"]:
			needs.append("gstin")
		out.append(
			{
				"key": key,
				"provider": e["provider"],
				"supplier_name": e["supplier_name"],
				"gst_category": e["gst_category"],
				"country": e["country"],
				"state": e["state"],
				"gstin": e["gstin"],
				"pan": e["pan"],
				"reverse_charge": e["reverse_charge"],
				"exists": existing,
				"needs": [] if existing else needs,
				"action": "exists" if existing else ("needs input" if needs else "create"),
			}
		)
	return out


def _ic() -> bool:
	return "india_compliance" in frappe.get_installed_apps()


def check_gstin(gstin: str, label: str = "GSTIN") -> str:
	"""Upper-cased, validated GSTIN. India Compliance's check (length + check digit) when installed."""
	gstin = (gstin or "").strip().upper()
	if not gstin:
		return ""
	if _ic():
		from india_compliance.gst_india.utils import validate_gstin

		return validate_gstin(gstin, label=label)
	if not re.fullmatch(r"\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]", gstin):
		frappe.throw(_("{0} {1} is not a valid GSTIN").format(label, gstin))
	return gstin


def state_code(state: str) -> str:
	if not state or not _ic():
		return ""
	from india_compliance.gst_india.constants import STATE_NUMBERS

	return STATE_NUMBERS.get(state, "")


def create(keys: list[str], gstins: dict | None = None, tax_withholding_category: str | None = None) -> list[dict]:
	"""Create the confirmed missing suppliers (and an address carrying the GSTIN for Indian ones)."""
	gstins = {k: v for k, v in (gstins or {}).items() if v}
	created = []
	for key in keys:
		if key not in LEGAL_ENTITIES:
			frappe.throw(_("Unknown platform legal entity {0}").format(key))
		e = LEGAL_ENTITIES[key]
		if find_supplier(key):
			continue
		gstin = check_gstin(gstins.get(key) or e["gstin"], _("GSTIN of {0}").format(e["supplier_name"]))
		if e["gst_category"] != "Overseas":
			if not gstin:
				frappe.throw(_("Enter the GSTIN printed on {0}'s invoice before creating it").format(e["supplier_name"]))
			code = state_code(e["state"])
			if code and gstin[:2] != code:
				frappe.throw(_("GSTIN {0} is not a {1} GSTIN (state code {2})").format(gstin, e["state"], code))
			if e["pan"] and gstin[2:12] != e["pan"]:
				frappe.throw(_("GSTIN {0} does not carry {1}'s PAN {2}").format(gstin, e["supplier_name"], e["pan"]))
		created.append(_make_supplier(key, e, gstin, tax_withholding_category))
	return created


def _make_supplier(key, e, gstin, twc) -> dict:
	meta = frappe.get_meta("Supplier")
	sup = frappe.new_doc("Supplier")
	sup.supplier_name = e["supplier_name"]
	sup.supplier_type = "Company"
	sup.country = e["country"]
	if meta.has_field("gst_category"):
		sup.gst_category = e["gst_category"]
	if gstin and meta.has_field("gstin"):
		sup.gstin = gstin
	pan = e["pan"] or (gstin[2:12] if gstin else "")
	if pan and meta.has_field("pan"):
		sup.pan = pan
	if meta.has_field("is_reverse_charge_applicable"):
		sup.is_reverse_charge_applicable = e["reverse_charge"]
	if twc:
		sup.tax_withholding_category = twc
	if not sup.get("supplier_group"):
		sup.supplier_group = frappe.db.get_single_value("Buying Settings", "supplier_group") or frappe.db.get_value("Supplier Group", {"is_group": 0}, "name") or frappe.db.get_value("Supplier Group", {}, "name")
	sup.insert()
	address = None
	if e["country"] == "India" and gstin:
		addr = frappe.new_doc("Address")
		addr.address_title = e["supplier_name"]
		addr.address_type = "Billing"
		addr.address_line1 = ADDRESS_PLACEHOLDER
		addr.city = e["state"]
		addr.state = e["state"]
		addr.country = "India"
		addr.is_primary_address = 1
		if frappe.get_meta("Address").has_field("gstin"):
			addr.gstin = gstin
			addr.gst_category = e["gst_category"]
		addr.append("links", {"link_doctype": "Supplier", "link_name": sup.name})
		addr.insert()
		address = addr.name
	return {"key": key, "supplier": sup.name, "address": address, "gstin": gstin}
