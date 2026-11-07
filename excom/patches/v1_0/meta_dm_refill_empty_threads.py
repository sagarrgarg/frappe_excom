"""Instagram messages from 21 Oct 2026 on failed to store (message ids over 140 characters), leaving
conversations with no messages. The id limit is 500 now; fill those conversations once, in the
background, rather than waiting for the hourly refill."""

import frappe


def execute():
	frappe.enqueue(
		"excom.excom.channels.meta_dm.service.refill_empty_threads",
		queue="long",
		timeout=3600,
		days=60,
		limit=500,
		enqueue_after_commit=True,
	)
