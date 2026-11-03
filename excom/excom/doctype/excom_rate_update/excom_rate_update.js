frappe.ui.form.on("Excom Rate Update", {
	refresh(frm) {
		if (["Draft", "Failed"].includes(frm.doc.status) && (frm.doc.items || []).length) {
			frm.add_custom_button(frm.doc.status === "Failed" ? __("Retry publish") : __("Publish now"), () => {
				frappe.confirm(__("Sync the catalogue and send this rate update on WhatsApp now?"), () =>
					frm.call("publish_now").then((r) => {
						const m = r.message || {};
						if (m.warning) frappe.msgprint({ message: m.warning, indicator: "orange" });
						frm.reload_doc();
					})
				);
			});
		}
		if (frm.doc.status === "Draft") {
			frm.add_custom_button(__("Cancel update"), () => frm.call("cancel_update").then(() => frm.reload_doc()));
		}
	},
});
