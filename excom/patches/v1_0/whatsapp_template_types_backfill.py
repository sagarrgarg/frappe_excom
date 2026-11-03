"""Templates synced before HLD-005 Phase 4 kept Meta's raw button types (COPY_CODE, CATALOG,
VOICE_CALL…) and no template type, so they sent without the components those buttons need and
could not be saved in Desk (not a Select option).

This maps the raw button types to excom's names and sets the template type where the stored rows
show it (Catalog, MPM, SPM buttons or a PRODUCT header). Offers and carousels were never stored by
the old sync, so they cannot be told apart here: the patch prints the templates that need a fresh
"Sync templates" (WhatsApp Templates list → Sync), which reads them in fully. It does not call Meta.

Safe to run again: rows already mapped are left alone.
"""

import frappe

# raw type stored by the old sync → excom button type (MPM and SPM are already the same string)
RAW_BUTTONS = {
	"COPY_CODE": "Copy Code",
	"CATALOG": "Catalog",
	"VOICE_CALL": "Voice Call",
	"MPM": "MPM",
	"SPM": "SPM",
	"URL": "Visit Website",
	"PHONE_NUMBER": "Call Phone",
	"QUICK_REPLY": "Quick Reply",
	"FLOW": "Flow",
}
TYPE_BY_BUTTON = (("Catalog", "Catalog"), ("MPM", "Multi-Product"), ("SPM", "Single-Product"))


def _ready() -> bool:
	for doctype, field in (("WhatsApp Templates", "template_type"), ("WhatsApp Button", "button_type"), ("WhatsApp Button", "example_code")):
		if not frappe.db.table_exists(doctype) or not frappe.db.has_column(doctype, field):
			return False
	return True


def execute():
	if not _ready():
		return

	for raw, kind in RAW_BUTTONS.items():
		if raw == kind:
			continue
		frappe.db.sql(
			"UPDATE `tabWhatsApp Button` SET button_type = %s WHERE parenttype = 'WhatsApp Templates' AND button_type = %s",
			(kind, raw),
		)
	if frappe.db.has_column("WhatsApp Button", "button_label"):
		# Meta's defaults for buttons whose text it fixes or makes optional
		frappe.db.sql("UPDATE `tabWhatsApp Button` SET button_label = 'Copy offer code' WHERE button_type = 'Copy Code' AND IFNULL(button_label, '') = ''")
		frappe.db.sql("UPDATE `tabWhatsApp Button` SET button_label = 'Call Now' WHERE button_type = 'Voice Call' AND IFNULL(button_label, '') = ''")

	rows = frappe.db.sql(
		"""SELECT name, IFNULL(template_type, '') AS template_type, IFNULL(header_type, '') AS header_type
		FROM `tabWhatsApp Templates` WHERE IFNULL(template_type, '') IN ('', 'Standard')""",
		as_dict=True,
	)
	kinds_by_parent: dict = {}
	for b in frappe.get_all("WhatsApp Button", filters={"parenttype": "WhatsApp Templates"}, fields=["parent", "button_type", "example_code"]):
		kinds_by_parent.setdefault(b.parent, []).append(b)

	resync = []
	for row in rows:
		buttons = kinds_by_parent.get(row.name, [])
		kinds = {b.button_type for b in buttons}
		new_type = next((t for kind, t in TYPE_BY_BUTTON if kind in kinds), None)
		if not new_type and row.header_type.upper() == "PRODUCT":
			new_type = "Single-Product"
		new_type = new_type or "Standard"
		if new_type != row.template_type:
			frappe.db.set_value("WhatsApp Templates", row.name, "template_type", new_type, update_modified=False)
		# a coupon without its example (never read by the old sync) cannot be saved until it is re-synced
		if any(b.button_type == "Copy Code" and not b.example_code for b in buttons):
			resync.append(row.name)

	if resync:
		print(
			"excom: WhatsApp templates need 'Sync templates' to read in their coupon example "
			f"(and any offer or carousel parts): {', '.join(sorted(resync)[:50])}"
		)
	if rows:
		print("excom: after this migrate, run Sync templates once so limited-time offers and carousels synced before are read in fully.")
