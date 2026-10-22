frappe.ui.form.on("Ads Bill Source", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("Fetch Now"), () =>
			frappe.call({ method: "excom.excom_ads.api_bills.fetch_now", args: { source: frm.doc.name } }).then((r) =>
				frappe.show_alert({ message: r.message && r.message.queued ? __("Fetch queued") : __("A fetch is already running"), indicator: "blue" })
			)
		);
		frm.add_custom_button(__("Platform Suppliers"), () => excom_ads_propose_suppliers());
	},
});

function excom_ads_propose_suppliers() {
	frappe.call({ method: "excom.excom_ads.api_bills.propose_suppliers" }).then((r) => {
		const rows = r.message || [];
		const missing = rows.filter((p) => !p.exists);
		const fields = rows.map((p) => ({
			fieldtype: p.exists ? "HTML" : "Check",
			fieldname: `create_${p.key}`,
			label: `${p.supplier_name} (${p.gst_category}${p.state ? ", " + p.state : ""}${p.reverse_charge ? ", reverse charge" : ""})`,
			options: p.exists ? `<p class="text-muted">${frappe.utils.escape_html(p.supplier_name)}: exists as ${frappe.utils.escape_html(p.exists)}</p>` : undefined,
		}));
		missing.filter((p) => p.needs.includes("gstin")).forEach((p) => fields.push({ fieldtype: "Data", fieldname: `gstin_${p.key}`, label: __("GSTIN of {0} (from its invoice)", [p.supplier_name]) }));
		fields.push({ fieldtype: "Link", fieldname: "twc", options: "Tax Withholding Category", label: __("Tax Withholding Category (confirm with your tax adviser)") });
		const d = new frappe.ui.Dialog({
			title: __("Platform suppliers (one per legal entity)"),
			fields,
			primary_action_label: __("Create Selected"),
			primary_action(v) {
				const keys = missing.filter((p) => v[`create_${p.key}`]).map((p) => p.key);
				if (!keys.length) return d.hide();
				const gstins = {};
				missing.forEach((p) => v[`gstin_${p.key}`] && (gstins[p.key] = v[`gstin_${p.key}`]));
				frappe.call({ method: "excom.excom_ads.api_bills.create_suppliers", args: { keys, gstins, tax_withholding_category: v.twc, confirm: 1 }, freeze: true }).then((res) => {
					d.hide();
					frappe.show_alert({ message: __("{0} supplier(s) created", [(res.message || []).length]), indicator: "green" });
				});
			},
		});
		d.show();
	});
}
