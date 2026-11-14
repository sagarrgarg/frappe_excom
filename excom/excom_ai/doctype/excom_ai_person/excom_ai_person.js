// A data-removal request (HLD-009): delete what research found and never find or add this person again.
frappe.ui.form.on("Excom AI Person", {
	refresh(frm) {
		if (frm.is_new() || !frappe.user.has_role(["Excom Admin", "System Manager"])) return;
		frm.add_custom_button(__("Forget this person"), () => {
			frappe.prompt(
				{ fieldname: "reason", fieldtype: "Small Text", label: __("Reason (for example: removal request of 14 Nov)") },
				({ reason }) => {
					frappe.call({
						method: "excom.excom_ai.people_finder.forget_person",
						args: { person: frm.doc.name, reason },
						freeze: true,
						callback: () => {
							frappe.show_alert({ message: __("Forgotten. They will not be found or added again."), indicator: "green" });
							frappe.set_route("List", "Excom AI Person");
						},
					});
				},
				__("Forget this person"),
				__("Forget")
			);
		});
	},
});
