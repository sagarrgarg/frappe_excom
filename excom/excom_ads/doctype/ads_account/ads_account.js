frappe.ui.form.on("Ads Account", {
	refresh(frm) {
		if (frm.is_new()) return;
		const run = (job_type) =>
			frappe
				.call("excom.excom_ads.api.sync_now", { account: frm.doc.name, job_type })
				.then(({ message }) =>
					frappe.show_alert({
						message: message.already_running ? __("{0} sync is already queued", [__(job_type)]) : __("{0} sync queued", [__(job_type)]),
						indicator: message.already_running ? "orange" : "green",
					})
				);
		for (const t of ["Structure", "Metrics", "Settle", "Backfill"]) {
			frm.add_custom_button(__(t), () => run(t), __("Sync Now"));
		}
		frm.add_custom_button(__("Sync Jobs"), () => frappe.set_route("List", "Ads Sync Job", { account: frm.doc.name }), __("View"));
		frm.add_custom_button(__("Daily Metrics"), () => frappe.set_route("List", "Ads Metric Snapshot", { account: frm.doc.name, level: "Account" }), __("View"));
		if (frm.doc.last_error) {
			frm.dashboard.set_headline_alert(`<div class="text-danger">${frappe.utils.escape_html(frm.doc.last_error)}</div>`);
		}
	},
});
