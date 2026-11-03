"""
WhatsApp Analytics Service — fetches messaging, conversation, template,
and pricing analytics from Meta's Graph API.

All functions accept an Excom Channel Account (or its name) and date ranges,
call Meta's `/<WABA_ID>?fields=...` endpoints, and return structured dicts.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Optional

import frappe
import requests as http_requests
from frappe import _
from frappe.utils.password import get_decrypted_password

from excom.excom.utils.errors import ExcomProviderError
from excom.excom.utils.graph import graph_version


def _resolve_waba_credentials(account_name: str) -> tuple[str, str, str, str]:
    """
    Extract WABA ID, token, base_url, and version from an Excom Channel Account.

    Returns:
        (waba_id, token, base_url, version)
    """
    account = frappe.get_doc("Excom Channel Account", account_name)

    waba_id = getattr(account, "wa_business_id", None)
    if not waba_id:
        frappe.throw(
            _("No WhatsApp Business Account ID configured on {0}").format(account_name),
            title=_("Missing WABA ID"),
        )

    token = None
    try:
        token = account.get_password("wa_token")
    except Exception:
        pass
    if not token:
        token = get_decrypted_password(
            account.doctype, account.name, "wa_token", raise_exception=False
        )
    if not token:
        frappe.throw(
            _("No access token for account {0}").format(account_name),
            title=_("Auth Error"),
        )

    base_url = getattr(account, "wa_url", None) or "https://graph.facebook.com"
    version = graph_version(getattr(account, "wa_version", None))

    return waba_id, token, base_url, version


def _call_analytics_api(
    waba_id: str,
    token: str,
    base_url: str,
    version: str,
    fields_param: str,
) -> dict:
    """Make a GET request to Meta's analytics endpoint."""
    url = f"{base_url}/{version}/{waba_id}"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"fields": fields_param}

    try:
        resp = http_requests.get(url, headers=headers, params=params, timeout=(10, 60))
    except http_requests.exceptions.RequestException as e:
        raise ExcomProviderError(
            f"Analytics API request failed: {e}",
            provider="whatsapp",
        ) from e

    if not resp.ok:
        try:
            err = resp.json().get("error", {}).get("message", resp.text[:300])
        except Exception:
            err = resp.text[:300]
        raise ExcomProviderError(
            f"Analytics API error ({resp.status_code}): {err}",
            provider="whatsapp",
        )

    return resp.json()


def _to_unix(dt: datetime) -> int:
    return int(dt.timestamp())


def _default_range(days: int = 30) -> tuple[int, int]:
    end = datetime.now()
    start = end - timedelta(days=days)
    return _to_unix(start), _to_unix(end)


def get_messaging_analytics(
    account_name: str,
    start: Optional[int] = None,
    end: Optional[int] = None,
    granularity: str = "DAY",
) -> dict:
    """
    Fetch messaging analytics (messages sent/delivered) from Meta.

    Args:
        account_name: Excom Channel Account name
        start: UNIX timestamp for range start (default: 30 days ago)
        end: UNIX timestamp for range end (default: now)
        granularity: DAY | HALF_HOUR | MONTH

    Returns:
        dict with phone_numbers, country_codes, granularity, data_points
    """
    waba_id, token, base_url, version = _resolve_waba_credentials(account_name)

    if not start or not end:
        start, end = _default_range(30)

    granularity = granularity.upper()
    if granularity not in ("DAY", "HALF_HOUR", "MONTH"):
        granularity = "DAY"

    fields = f"analytics.start({start}).end({end}).granularity({granularity})"
    data = _call_analytics_api(waba_id, token, base_url, version, fields)
    return data.get("analytics", {})


def get_conversation_analytics(
    account_name: str,
    start: Optional[int] = None,
    end: Optional[int] = None,
    granularity: str = "DAILY",
    dimensions: Optional[list[str]] = None,
    conversation_categories: Optional[list[str]] = None,
) -> dict:
    """
    Conversation-shaped analytics, now built from Meta's pricing_analytics.

    Meta removed `conversation_analytics` on 2026-11-01 (per-message pricing replaced
    conversations on 2025-07-01). This keeps the old response shape — data[0].data_points with
    `conversation`, `cost` and `conversation_category` — so existing screens keep working,
    with `volume` mapped to `conversation` and `pricing_category` to `conversation_category`.

    Args:
        account_name: Excom Channel Account name
        start/end: UNIX timestamps
        granularity: DAILY | HALF_HOUR | MONTHLY
        dimensions: CONVERSATION_CATEGORY / CONVERSATION_TYPE / COUNTRY / PHONE (mapped to pricing dimensions)
        conversation_categories: filter on AUTHENTICATION, MARKETING, SERVICE, UTILITY

    Returns:
        dict with conversation-shaped data points
    """
    mapping = {"CONVERSATION_CATEGORY": "PRICING_CATEGORY", "CONVERSATION_TYPE": "PRICING_TYPE"}
    pricing_dims = [mapping.get(d, d) for d in (dimensions or [])]
    pricing = get_pricing_analytics(account_name, start, end, granularity, dimensions=pricing_dims or None)
    wanted = {c.upper() for c in (conversation_categories or [])}
    points = []
    for dp in (pricing.get("data") or [{}])[0].get("data_points", []) if pricing.get("data") else []:
        category = (dp.get("pricing_category") or "").upper()
        if wanted and category not in wanted:
            continue
        points.append({
            **dp,
            "conversation": dp.get("volume", 0),
            "conversation_category": category or "UNKNOWN",
            "conversation_type": dp.get("pricing_type", ""),
        })
    return {"data": [{"data_points": points}], "source": "pricing_analytics"}


def get_template_analytics(
    account_name: str,
    template_ids: list[str],
    start: Optional[int] = None,
    end: Optional[int] = None,
    metric_types: Optional[list[str]] = None,
) -> dict:
    """
    Fetch template analytics (sent/delivered/read/clicked) from Meta.

    Args:
        account_name: Excom Channel Account name
        template_ids: list of Meta template IDs (max 10)
        start/end: UNIX timestamps
        metric_types: SENT, DELIVERED, READ, CLICKED, COST

    Returns:
        dict with template data points
    """
    waba_id, token, base_url, version = _resolve_waba_credentials(account_name)

    if not start or not end:
        start, end = _default_range(30)

    if not template_ids:
        return {"data": []}

    template_ids = template_ids[:10]

    url = f"{base_url}/{version}/{waba_id}/template_analytics"
    headers = {"Authorization": f"Bearer {token}"}
    params: dict = {
        "start": start,
        "end": end,
        "granularity": "daily",
        "template_ids": "[" + ",".join(str(t) for t in template_ids) + "]",
    }
    if metric_types:
        params["metric_types"] = ",".join(metric_types)

    try:
        resp = http_requests.get(url, headers=headers, params=params, timeout=(10, 60))
    except http_requests.exceptions.RequestException as e:
        raise ExcomProviderError(
            f"Template analytics request failed: {e}", provider="whatsapp"
        ) from e

    if not resp.ok:
        try:
            err = resp.json().get("error", {}).get("message", resp.text[:300])
        except Exception:
            err = resp.text[:300]
        raise ExcomProviderError(
            f"Template analytics error ({resp.status_code}): {err}", provider="whatsapp"
        )

    return resp.json()


def get_pricing_analytics(
    account_name: str,
    start: Optional[int] = None,
    end: Optional[int] = None,
    granularity: str = "DAILY",
    dimensions: Optional[list[str]] = None,
) -> dict:
    """
    Fetch pricing analytics (cost/volume breakdowns) from Meta.

    Args:
        account_name: Excom Channel Account name
        start/end: UNIX timestamps
        granularity: DAILY | HALF_HOUR | MONTHLY
        dimensions: COUNTRY, PHONE, PRICING_CATEGORY, PRICING_TYPE, TIER

    Returns:
        dict with pricing data points
    """
    waba_id, token, base_url, version = _resolve_waba_credentials(account_name)

    if not start or not end:
        start, end = _default_range(30)

    granularity = granularity.upper()
    if granularity not in ("DAILY", "HALF_HOUR", "MONTHLY"):
        granularity = "DAILY"

    fields = f"pricing_analytics.start({start}).end({end}).granularity({granularity})"

    if dimensions:
        dim_str = ",".join(f'"{d}"' for d in dimensions)
        fields += f".dimensions([{dim_str}])"

    data = _call_analytics_api(waba_id, token, base_url, version, fields)
    return data.get("pricing_analytics", {})


def get_account_overview(account_name: str, days: int = 30) -> dict:
    """
    Aggregate overview combining messaging + conversation metrics.

    Fetches both in sequence and returns a unified summary suitable
    for a dashboard overview card.
    """
    start, end = _default_range(days)

    messaging = {}
    conversation = {}
    pricing = {}

    try:
        messaging = get_messaging_analytics(account_name, start, end, "DAY")
    except Exception:
        frappe.log_error(title="Excom: messaging analytics fetch error")

    try:
        pricing = get_pricing_analytics(
            account_name, start, end, "DAILY",
            dimensions=["PRICING_CATEGORY"],
        )
    except Exception:
        frappe.log_error(title="Excom: pricing analytics fetch error")

    # conversation_analytics is gone (2026-11-01); the conversation card is derived from pricing.
    conversation = {"data": [{"data_points": [
        {**dp, "conversation": dp.get("volume", 0), "conversation_category": (dp.get("pricing_category") or "UNKNOWN").upper()}
        for dp in ((pricing.get("data") or [{}])[0].get("data_points", []) if pricing.get("data") else [])
    ]}]}

    total_sent = sum(dp.get("sent", 0) for dp in messaging.get("data_points", []))
    total_delivered = sum(dp.get("delivered", 0) for dp in messaging.get("data_points", []))

    conv_data = conversation.get("data", [{}])
    conv_points = conv_data[0].get("data_points", []) if conv_data else []
    total_conversations = sum(dp.get("conversation", 0) for dp in conv_points)
    total_cost = sum(dp.get("cost", 0) for dp in conv_points)

    category_breakdown: dict[str, int] = {}
    for dp in conv_points:
        cat = dp.get("conversation_category", "UNKNOWN")
        category_breakdown[cat] = category_breakdown.get(cat, 0) + dp.get("conversation", 0)

    pricing_data = pricing.get("data", [{}])
    pricing_points = pricing_data[0].get("data_points", []) if pricing_data else []
    total_volume = sum(dp.get("volume", 0) for dp in pricing_points)
    total_pricing_cost = sum(dp.get("cost", 0) for dp in pricing_points)

    return {
        "period_days": days,
        "start": start,
        "end": end,
        "messaging": {
            "total_sent": total_sent,
            "total_delivered": total_delivered,
            "delivery_rate": round((total_delivered / total_sent * 100), 1) if total_sent else 0,
            "data_points": messaging.get("data_points", []),
        },
        "conversations": {
            "total": total_conversations,
            "total_cost": round(total_cost, 2),
            "by_category": category_breakdown,
            "data_points": conv_points,
        },
        "pricing": {
            "total_volume": total_volume,
            "total_cost": round(total_pricing_cost, 2),
            "data_points": pricing_points,
        },
    }
