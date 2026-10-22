frappe.ui.form.on("Ads Connection", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.provider !== "Google") return;
		const api = "excom.excom_ads.api_google.";
		frm.add_custom_button(__("Test Connection"), () =>
			frappe.call({ method: api + "test_connection", args: { connection: frm.doc.name }, freeze: true }).then(({ message }) => {
				frappe.show_alert({ message: message.ok ? __("Connection works") : frappe.utils.escape_html(message.error || ""), indicator: message.ok ? "green" : "red" }, 8);
				frm.reload_doc();
			})
		);
		frm.add_custom_button(__("Google Ads Accounts"), () =>
			frappe.call({ method: api + "list_customers", args: { connection: frm.doc.name }, freeze: true }).then(({ message }) => show_customers(frm, message || []))
		);
		if (/auth\/analytics/.test(frm.doc.scopes || "")) {
			frm.add_custom_button(__("GA4 Properties"), () =>
				frappe.call({ method: "excom.excom_ads.api_analytics.list_properties", args: { connection: frm.doc.name }, freeze: true }).then(({ message }) => show_properties(frm, message || []))
			);
		}
		if (frm.doc.last_error) {
			frm.dashboard.set_headline_alert(`<div class="text-danger">${frappe.utils.escape_html(frm.doc.last_error)}</div>`);
		}
	},
});

function show_customers(frm, rows) {
	const esc = frappe.utils.escape_html;
	const body = rows.length
		? `<table class="table table-sm"><thead><tr><th>${__("Account")}</th><th>${__("ID")}</th><th>${__("Currency")}</th><th>${__("Status")}</th><th></th></tr></thead><tbody>${rows
				.map(
					(r) => `<tr><td>${"&nbsp;".repeat(2 * (r.level || 0))}${esc(r.name || "")}${r.manager ? ` <span class="text-muted">(${__("manager")})</span>` : ""}${r.test_account ? ` <span class="text-muted">(${__("test")})</span>` : ""}</td>
					<td>${esc(r.customer_id_display || r.customer_id)}</td><td>${esc(r.currency || "")}</td><td>${esc(r.status || "")}</td>
					<td>${r.manager ? "" : r.enabled_as ? `<a href="/app/ads-account/${encodeURIComponent(r.enabled_as)}">${__("Enabled")}</a>` : `<button class="btn btn-xs btn-primary" data-id="${esc(r.customer_id)}">${__("Enable")}</button>`}</td></tr>`
				)
				.join("")}</tbody></table>`
		: `<p class="text-muted">${__("No Google Ads accounts are reachable with this connection.")}</p>`;
	const d = new frappe.ui.Dialog({ title: __("Google Ads Accounts"), size: "large", fields: [{ fieldtype: "HTML", fieldname: "list", options: body }] });
	d.$wrapper.on("click", "button[data-id]", function () {
		const btn = $(this);
		btn.prop("disabled", true);
		frappe
			.call("excom.excom_ads.api_google.enable_customer", { connection: frm.doc.name, customer_id: btn.attr("data-id") })
			.then(({ message }) => {
				btn.replaceWith(`<a href="/app/ads-account/${encodeURIComponent(message.account)}">${__("Enabled")}</a>`);
				frappe.show_alert({ message: __("Ads Account {0} enabled. Its first sync is queued.", [message.account]), indicator: "green" });
			})
			.catch(() => btn.prop("disabled", false));
	});
	d.show();
}

function show_properties(frm, rows) {
	const esc = frappe.utils.escape_html;
	const body = rows.length
		? `<table class="table table-sm"><thead><tr><th>${__("Analytics account")}</th><th>${__("Property")}</th><th>${__("ID")}</th><th></th></tr></thead><tbody>${rows
				.map(
					(r) => `<tr><td>${esc(r.account_name || r.account || "")}</td><td>${esc(r.display_name || "")}</td><td>${esc(r.property_id)}</td>
					<td>${r.enabled_as ? `<a href="/app/ads-analytics-property/${encodeURIComponent(r.enabled_as)}">${__("Enabled")}</a>` : `<button class="btn btn-xs btn-primary" data-id="${esc(r.property_id)}">${__("Enable")}</button>`}</td></tr>`
				)
				.join("")}</tbody></table>`
		: `<p class="text-muted">${__("No GA4 properties are readable with this connection. Add its service account (or Google user) as a Viewer on the property.")}</p>`;
	const d = new frappe.ui.Dialog({ title: __("GA4 Properties"), size: "large", fields: [{ fieldtype: "HTML", fieldname: "list", options: body }] });
	d.$wrapper.on("click", "button[data-id]", function () {
		const btn = $(this);
		btn.prop("disabled", true);
		frappe
			.call("excom.excom_ads.api_analytics.enable_property", { connection: frm.doc.name, property_id: btn.attr("data-id") })
			.then(({ message }) => {
				btn.replaceWith(`<a href="/app/ads-analytics-property/${encodeURIComponent(message.property)}">${__("Enabled")}</a>`);
				frappe.show_alert({ message: __("GA4 property {0} enabled. Its first backfill is queued.", [message.property]), indicator: "green" });
			})
			.catch(() => btn.prop("disabled", false));
	});
	d.show();
}
