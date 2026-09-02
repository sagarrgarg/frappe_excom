// Excom Voice Telephony Global Desk Integration
// Note: Desk popup toast alerts disabled per project specification (ringing ONLY inside /excom workspace)
frappe.provide("excom.voice");

$(document).ready(function() {
    if (!frappe.session || frappe.session.user === "Guest") return;
    console.log("[Excom Voice] Desk integration initialized (desktop alert suppressed for /excom workspace exclusive display).");
});
