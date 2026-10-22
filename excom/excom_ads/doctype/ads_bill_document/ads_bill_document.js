// Ad bill review on the Desk: build / rebuild the draft Purchase Invoice and approve it.
// The server enforces every rule (rights, scope, maker-checker); these buttons only call it.
frappe.ui.form.on("Ads Bill Document", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.status === "Submitted") return;
		const call = (method, args, done) =>
			frappe.call({ method: `excom.excom_ads.api_bills.${method}`, args: { name: frm.doc.name, ...args }, freeze: true }).then((r) => {
				if (done) done(r.message || {});
				frm.reload_doc();
			});
		frm.add_custom_button(frm.doc.purchase_invoice ? __("Re-check") : __("Create Draft"), () => call("process_bill", {}), __("Bill"));
		if (frm.doc.purchase_invoice) {
			frm.add_custom_button(__("Rebuild Draft"), () => frappe.confirm(__("Delete the unedited draft and build it again from this bill?"), () => call("process_bill", { rebuild: 1 })), __("Bill"));
		}
		if (frm.doc.purchase_invoice && ["Draft Created", "Needs Review"].includes(frm.doc.status)) {
			frm.add_custom_button(__("Approve and Submit"), () => {
				const findings = (frm.doc.differences || "").trim();
				const go = () => call("approve_bill", { acknowledge_differences: findings ? 1 : 0 }, () => frappe.show_alert({ message: __("Purchase Invoice submitted"), indicator: "green" }));
				findings ? frappe.confirm(__("Approve with these findings?") + "<br><br>" + frappe.utils.escape_html(findings).replace(/\n/g, "<br>"), go) : go();
			}, __("Bill"));
		}
	},
});
