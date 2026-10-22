// GA4 property: queue a sync now (the scheduler also runs them: hourly recent days, daily settle).
frappe.ui.form.on("Ads Analytics Property", {
	refresh(frm) {
		if (frm.is_new()) return;
		for (const [job, label] of [["Metrics", __("Sync Recent Days")], ["Settle", __("Re-pull Settle Window")], ["Backfill", __("Backfill History")]]) {
			frm.add_custom_button(label, () =>
				frappe.call({ method: "excom.excom_ads.api_analytics.sync_property", args: { property: frm.doc.name, job_type: job } }).then(() =>
					frappe.show_alert({ message: __("Queued. Refresh in a minute."), indicator: "green" })
				), __("Sync")
			);
		}
		if (frm.doc.last_error) {
			frm.dashboard.set_headline_alert(`<div class="text-danger">${frappe.utils.escape_html(frm.doc.last_error)}</div>`);
		}
	},
});
