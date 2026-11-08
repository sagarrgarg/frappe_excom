frappe.ui.form.on("Excom Channel Account", {
	refresh(frm) {
		if (frm.doc.channel === "whatsapp" && !frm.is_new()) {
			frm.add_custom_button(__("Test Connection"), function () {
				frappe.call({
					method: "frappe.client.get_list",
					args: {
						doctype: "Excom Channel Account",
						filters: { name: frm.doc.name },
						fields: ["wa_url", "wa_version", "wa_phone_id"],
						limit_page_length: 1,
					},
					callback(r) {
						if (r.message && r.message.length) {
							frappe.msgprint(
								__("Account configured: {0}/{1}/{2}", [
									r.message[0].wa_url,
									r.message[0].wa_version,
									r.message[0].wa_phone_id,
								])
							);
						}
					},
				});
			});
		}

		if (frm.doc.channel === "email" && !frm.is_new() && frm.doc.email_provider === "JMAP") {
			// A JMAP server (Stalwart): sign in with the login and password, find the folders.
			if (!frm.doc.email_authorized) {
				frm.dashboard.set_headline_alert(
					__("Not connected yet. Save the server address, login and password, then click <b>Connect</b>."),
					"orange"
				);
			}
			frm.add_custom_button(frm.doc.email_authorized ? __("Reconnect") : __("Connect"), function () {
				frappe.call({
					method: "excom.excom.api.email.connect_mail_server",
					args: { account_name: frm.doc.name },
					freeze: true,
					freeze_message: __("Signing in to the mail server..."),
					callback(r) {
						const m = r.message || {};
						if (m.ok) {
							frappe.msgprint({
								title: __("Connected"),
								message: __("Signed in as {0}.", [m.email || frm.doc.jmap_username]) + (m.push ? "<br>" + __("Live updates: {0}", [m.push]) : ""),
								indicator: "green",
							});
							frm.reload_doc();
						} else {
							frappe.msgprint({ title: __("Could not connect"), message: m.error || __("Unknown error"), indicator: "red" });
						}
					},
				});
			}, __("Email"));
			if (frm.doc.email_authorized) {
				frm.add_custom_button(__("Sync Now"), function () {
					frappe.call({
						method: "excom.excom.api.email.manual_sync",
						args: { account_name: frm.doc.name },
						freeze: true,
						freeze_message: __("Syncing with the mail server..."),
						callback(r) {
							if (r.message && r.message.success) {
								frappe.show_alert({ message: __("Email sync completed"), indicator: "green" });
								frm.reload_doc();
							} else {
								frappe.msgprint({ title: __("Sync Failed"), message: r.message ? r.message.error : __("Unknown error"), indicator: "red" });
							}
						},
					});
				}, __("Email"));
			}
		} else if (frm.doc.channel === "email" && !frm.is_new()) {
			frm.call("check_email_authorization").then((r) => {
				if (r && r.message) {
					const wasAuthorized = frm.doc.email_authorized;
					if (r.message.error) {
						frappe.msgprint({
							title: __("Authorization Problem"),
							message: r.message.error,
							indicator: "red",
						});
					}
					if (r.message.authorized && !wasAuthorized) {
						frm.reload_doc();
					} else if (!r.message.authorized && !r.message.error) {
						frm.dashboard.set_headline_alert(
							__("Gmail not authorized. Click the <b>Authorize Gmail</b> button below to connect."),
							"orange"
						);
					}
				}
			});

			if (frm.doc.email_authorized) {
				frm.add_custom_button(__("Check Gmail Connection"), function () {
					frappe.call({
						method: "excom.excom.api.email.check_gmail_connection",
						args: { account_name: frm.doc.name },
						callback(r) {
							if (r.message && r.message.success) {
								frappe.msgprint(
									__("Gmail connection active. Email: {0}, Messages: {1}", [
										r.message.email,
										r.message.messagesTotal,
									])
								);
							} else {
								frappe.msgprint({
									title: __("Connection Failed"),
									message: r.message ? r.message.error : __("Unknown error"),
									indicator: "red",
								});
							}
						},
					});
				}, __("Email"));

				frm.add_custom_button(__("Sync Now"), function () {
					frappe.call({
						method: "excom.excom.api.email.manual_sync",
						args: { account_name: frm.doc.name },
						freeze: true,
						freeze_message: __("Syncing emails from Gmail..."),
						callback(r) {
							if (r.message && r.message.success) {
								frappe.show_alert({
									message: __("Email sync completed"),
									indicator: "green",
								});
								frm.reload_doc();
							} else {
								frappe.msgprint({
									title: __("Sync Failed"),
									message: r.message ? r.message.error : __("Unknown error"),
									indicator: "red",
								});
							}
						},
					});
				}, __("Email"));
			}
		}
	},

	email_authorize_button(frm) {
		if (!frm.doc.email_connected_app) {
			frappe.msgprint(__("Please set the Connected App field first."));
			return;
		}

		// Use the backend helper which:
		// 1. Sets email_connected_user = frappe.session.user so the OAuth
		//    callback state check always passes (no "Invalid token state" error).
		// 2. Adds prompt=consent to the Connected App query parameters so
		//    Google always returns a fresh refresh_token.
		frappe.call({
			doc: frm.doc,
			method: "authorize_email_account",
			args: { success_uri: window.location.pathname },
			callback(r) {
				if (r.message) {
					window.open(r.message, "_self");
				}
			},
		});
	},
});
