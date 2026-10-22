from frappe.model.document import Document


class AdsReportDelivery(Document):
	"""Delivery log written by report_schedule.py only (named from template × period × recipient ×
	channel, so a retry lands on the same row). Holds no figures."""

	pass
