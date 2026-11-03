# Copyright (c) 2025, Shridhar Patil and contributors
# For license information, please see license.txt

import json
import hashlib
import hmac
import frappe
from frappe import _
from frappe.rate_limiter import rate_limit


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=120, seconds=60)
def handle_flow_request():
    """
    Handle WhatsApp Flow data exchange requests.

    This endpoint receives requests from WhatsApp when a flow needs data
    or when a user completes a flow.

    Endpoint URL to configure in Meta:
    https://your-site.com/api/method/excom.excom.api.flow_endpoint.handle_flow_request
    """
    # Meta signs flow data-exchange requests with the app secret; unsigned callers are refused once any
    # secret is configured (same rule as the webhook). Without this, anyone could POST flow completions.
    from excom.excom.utils.webhook import _verify_hmac_signature
    if frappe.request and frappe.request.method == "POST":
        # Signed requests only — even on a site without an app secret (the webhook may accept
        # unsigned payloads there; this endpoint never does).
        if not _verify_hmac_signature() or not frappe.flags.excom_webhook_signed:
            frappe.throw(_("Invalid signature"), frappe.AuthenticationError)
    try:
        # Get request data
        if frappe.request.method == "GET":
            # Health check - WhatsApp pings this to verify endpoint
            return {"status": "ok"}

        # POST request - actual flow data
        data = frappe.request.get_json()

        if not data:
            frappe.throw(_("No data received"))
        if data.get("encrypted_flow_data"):
            # Meta encrypts endpoint (data_exchange) Flows with RSA/AES; excom does not decrypt them
            # yet (HLD-005 D11 uses static Flows). Refuse clearly instead of treating ciphertext as data.
            frappe.throw(_("Endpoint (data_exchange) Flows are not supported yet"), frappe.ValidationError)

        # Not logged: a Flow request carries what the person typed (names, numbers, addresses).

        # Get action type
        action = data.get("action")

        if action == "ping":
            # Health check
            return {
                "data": {
                    "status": "active"
                }
            }

        if action == "INIT":
            # Initial data request when flow opens
            flow_token = data.get("flow_token")
            screen_id = data.get("screen")

            return handle_init(flow_token, screen_id, data)

        if action == "data_exchange":
            # Data exchange during flow navigation
            return handle_data_exchange(data)

        if action == "BACK":
            # User pressed back button
            return handle_back(data)

        # Default response
        return {
            "data": {}
        }

    except Exception as e:
        frappe.log_error("Excom: WhatsApp Flow endpoint error", str(e))
        return {
            "data": {
                "error": str(e)
            }
        }


def handle_init(flow_token, screen_id, data):
    """Handle initial flow request."""
    # Return initial data for the first screen
    # This can be customized based on flow_token or other parameters

    return {
        "screen": screen_id or "INIT",
        "data": {}
    }


def handle_data_exchange(data):
    """Handle data exchange requests during flow."""
    flow_token = data.get("flow_token")
    screen = data.get("screen")
    form_data = data.get("data", {})

    # Process the form data
    # You can save it, validate it, or fetch additional data

    # Log flow data for processing
    if flow_token:
        save_flow_data(flow_token, screen, form_data)

    # Return next screen data or completion
    return {
        "data": {}
    }


def handle_back(data):
    """Handle back button press."""
    return {
        "data": {}
    }


FLOW_SESSION_TTL = 24 * 3600


def save_flow_data(flow_token, screen, form_data):
    """Keep what the person entered so far, per flow_token, for one day.

    The completed answer arrives later as an nfm_reply message (with the same flow_token), which
    is what features act on; this only bridges screens of an endpoint (data_exchange) Flow.
    """
    try:
        if not flow_token or len(str(flow_token)) > 512 or len(json.dumps(form_data or {})) > 20000:
            return  # bounded: a token and a form, not a storage service
        key = f"excom:flow_session:{flow_token}"
        current = frappe.cache.get_value(key) or {}
        current.update(form_data or {})
        current["_last_screen"] = screen
        frappe.cache.set_value(key, current, expires_in_sec=FLOW_SESSION_TTL)
    except Exception:
        frappe.log_error(title="Excom: WhatsApp Flow session save failed", message=frappe.get_traceback())


def get_flow_session(flow_token: str) -> dict:
    """What an endpoint Flow collected so far for this flow_token ({} when nothing / expired)."""
    return frappe.cache.get_value(f"excom:flow_session:{flow_token}") or {}


def verify_signature(payload, signature, app_secret):
    """Verify the request signature from WhatsApp."""
    expected_signature = hmac.new(
        app_secret.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(signature, expected_signature)
