import frappe
from frappe.model.document import Document


class ExcomTeamMember(Document):
    pass


def on_doctype_update():
    # Read on every permission check now that team membership decides lead and thread visibility.
    frappe.db.add_index("Excom Team Member", ["user"])
