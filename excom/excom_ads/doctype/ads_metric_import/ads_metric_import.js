// Manual ad network figures: validate the file row by row, then import (replaces the file's dates).
frappe.ui.form.on("Ads Metric Import", {
	setup(frm) {
		frm.set_query("account", () => ({ filters: { provider: "Manual" } }));
	},
	refresh(frm) {
		frm.add_custom_button(__("Download Template"), () => {
			window.open("/api/method/excom.excom_ads.imports.download_template");
		});
		if (frm.is_new() || !frm.doc.import_file) return;
		const call = (method, label) =>
			frappe.call({ method, args: { name: frm.doc.name }, freeze: true, freeze_message: label }).then(() => frm.reload_doc());
		frm.add_custom_button(__("Validate"), () => call("excom.excom_ads.imports.validate_import", __("Checking rows…")));
		if (frm.doc.status === "Validated" || frm.doc.status === "Imported") {
			frm.add_custom_button(__("Import"), () => call("excom.excom_ads.imports.run_import", __("Importing…"))).addClass("btn-primary");
		}
	},
});
