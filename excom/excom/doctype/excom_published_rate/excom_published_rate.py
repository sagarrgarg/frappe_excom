import frappe
from frappe.model.document import Document


class ExcomPublishedRate(Document):
	pass


def on_doctype_update():
	frappe.db.add_unique("Excom Published Rate", ["item_code", "price_list", "uom"], constraint_name="unique_item_price_list_uom")
