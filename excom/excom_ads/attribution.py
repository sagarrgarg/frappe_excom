"""Leads and attribution: every observed ad touch becomes one Ads Attribution Touch row, and the CRM
record it produced (Lead, later Opportunity) carries a copy of its FIRST ad touch.

Observations, each written once (the row is named from its natural key, so a replayed webhook, a
re-run reconciliation or a re-processed log lands on the same row):

    Lead Form   Meta lead ads and Google Ads lead forms, from excom intake (`on_intake`)
    CTWA        an inbound WhatsApp message that carries `referral` (`on_whatsapp_message`)
    Website     a website enquiry that carries UTM values or click IDs (`on_intake`)
    Order       a submitted Sales Order with storefront UTM / click IDs (`on_sales_order_submit`)

Platform IDs are stored as they arrive. The links to the Excom Ads mirrors (Ads Account / Campaign /
Ad Group / Advertisement, named `{account}-{external_id}`) are resolved at once when the mirror
exists, and later by `relink_pending` (after every structure sync and daily) when the lead came in
before the mirror did.

Lead/Opportunity names are spelled only through crm_gateway (`gw.LEAD`), attribution only through
crm_compat. Everything here is best effort for the caller: intake and inbound messaging never fail
because attribution did (callers wrap these in a savepoint and log).
"""

from urllib.parse import parse_qs, urlparse

import frappe
from frappe.utils import add_days, cint, now_datetime

from excom.excom.services import crm_gateway as gw
from excom.excom_ads.naming import touch_name

TOUCH = "Ads Attribution Touch"
CLICK_IDS = ("gclid", "gbraid", "wbraid", "fbclid", "fbc", "fbp", "ctwa_clid", "li_fat_id")
UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "utm_id")
ID_FIELDS = ("account_external_id", "campaign_external_id", "campaign_name", "ad_group_external_id", "ad_group_name", "ad_external_id", "ad_name", "creative_id", "form_id", "page_id")
LINK_FIELDS = ("account", "campaign", "ad_group", "advertisement")
SCOPE_FIELDS = ("company", "workspace", "brand")
REF_FIELDS = ("reference_doctype", "reference_name", "omni_identity", "source_log", "thread", "message")
TEXT_FIELDS = ("landing_url", "referrer_url", "referral_source_type", "headline", "ad_body", "media_type", "attribution_note", "channel")
TOUCH_FIELDS = ("touch_source", "observation_id", "touch_at", "provider", "link_status", "is_test", "is_organic") + ID_FIELDS + LINK_FIELDS + SCOPE_FIELDS + REF_FIELDS + TEXT_FIELDS + CLICK_IDS + UTM_KEYS
RELINK_STATUSES = ("Pending", "Partial")
RELINK_DAYS = 90

# Lead / Opportunity custom field ← touch field (attribution_schema.py creates them).
RECORD_MAP = {
	"ads_touch_source": "touch_source", "ads_provider": "provider", "ads_first_touch_at": "touch_at", "ads_link_status": "link_status",
	"ads_is_test": "is_test", "ads_is_organic": "is_organic",
	"ads_account": "account", "ads_campaign": "campaign", "ads_ad_group": "ad_group", "ads_advertisement": "advertisement",
	"ads_campaign_id": "campaign_external_id", "ads_campaign_name": "campaign_name", "ads_ad_group_id": "ad_group_external_id",
	"ads_ad_group_name": "ad_group_name", "ads_ad_id": "ad_external_id", "ads_ad_name": "ad_name", "ads_form_id": "form_id", "ads_page_id": "page_id",
	**{f"ads_{c}": c for c in CLICK_IDS},
}

MEDIUM = {"Meta": "paid_social", "Google": "cpc"}


def available() -> bool:
	return bool(frappe.db.exists("DocType", TOUCH))


# ─── small helpers ────────────────────────────────────────────────────────────

def _s(value, limit: int = 140):
	"""Platform value → trimmed string, or None. Google sends int64 IDs as numbers and 0 for 'none'."""
	if value is None or value is False:
		return None
	if isinstance(value, (int, float)) and not isinstance(value, bool):
		if value == 0:
			return None
		value = str(int(value)) if float(value).is_integer() else str(value)
	value = str(value).strip()
	if not value or value == "0":
		return None
	return value[:limit]


def _truthy(value) -> bool:
	return value is True or str(value).strip().lower() in ("1", "true", "yes")


def _when(value):
	"""Epoch seconds (Meta webhook) or ISO-8601 (Graph, Google) → naive site datetime; now when unknown."""
	from excom.excom_ads.sync import _dt

	if value in (None, ""):
		return now_datetime()
	if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
		from frappe.utils import get_datetime

		from excom.excom.utils.site_time import epoch_to_site_time

		return get_datetime(epoch_to_site_time(int(value)))
	return _dt(value) or now_datetime()


def _query(url: str | None) -> dict:
	if not url or "?" not in str(url):
		return {}
	try:
		return {k: v[0] for k, v in parse_qs(urlparse(str(url)).query).items() if v}
	except ValueError:
		return {}


def build_fbc(fbclid: str | None, at=None) -> str | None:
	"""Meta's click cookie format, fb.{subdomain index}.{creation ms}.{fbclid}, for a click with no _fbc cookie."""
	if not fbclid:
		return None
	at = at or now_datetime()
	return f"fb.1.{int(at.timestamp() * 1000)}.{fbclid}"


def _provider_from(values: dict) -> str | None:
	if values.get("gclid") or values.get("gbraid") or values.get("wbraid"):
		return "Google"
	if values.get("fbclid") or values.get("fbc") or values.get("ctwa_clid"):
		return "Meta"
	if values.get("li_fat_id"):
		return "LinkedIn"
	src = (values.get("utm_source") or "").lower()
	if src in ("google", "googleads", "adwords", "youtube"):
		return "Google"
	if src in ("facebook", "fb", "instagram", "ig", "meta", "whatsapp"):
		return "Meta"
	if src in ("linkedin", "li"):
		return "LinkedIn"
	return None


# ─── observations → touch values (pure: no writes) ──────────────────────────

def _meta_is_test(raw: dict) -> bool:
	"""Meta's Lead Ads Testing Tool fills every answer with '<test lead: dummy data for …>'."""
	if _truthy(raw.get("is_test")):
		return True
	for fd in raw.get("field_data") or []:
		for v in fd.get("values") or []:
			if isinstance(v, str) and v.strip().lower().startswith("<test lead"):
				return True
	return False


def meta_lead_context(raw: dict) -> dict:
	"""Graph lead (`fetch_lead` / reconcile fields) merged with the leadgen webhook value.

	In the webhook `adgroup_id` is Meta's legacy name for the AD (not the ad set): it is only a
	fallback for the ad id. The ad set comes from the Graph field `adset_id`."""
	hook = raw.get("_webhook") or {}
	return {
		"touch_source": "Lead Form", "provider": "Meta", "channel": "Meta Lead Ads",
		"ad_external_id": _s(raw.get("ad_id") or hook.get("ad_id") or hook.get("adgroup_id")),
		"ad_name": _s(raw.get("ad_name"), 255),
		"ad_group_external_id": _s(raw.get("adset_id")),
		"ad_group_name": _s(raw.get("adset_name"), 255),
		"campaign_external_id": _s(raw.get("campaign_id")),
		"campaign_name": _s(raw.get("campaign_name"), 255),
		"form_id": _s(raw.get("form_id") or hook.get("form_id")),
		"page_id": _s(raw.get("page_id") or hook.get("page_id")),
		"is_organic": 1 if _truthy(raw.get("is_organic")) else 0,
		"is_test": 1 if _meta_is_test(raw) else 0,
		"touch_at": _when(raw.get("created_time") or hook.get("created_time")),
	}


def google_lead_context(raw: dict) -> dict:
	"""Google Ads lead form webhook (WebhookLead): campaign_id, adgroup_id, creative_id, asset_group_id,
	form_id, gcl_id, is_test, lead_submit_time. Performance Max leads carry an asset group, not an ad group."""
	return {
		"touch_source": "Lead Form", "provider": "Google", "channel": "Google Lead Form",
		"campaign_external_id": _s(raw.get("campaign_id")),
		"ad_group_external_id": _s(raw.get("adgroup_id")) or _s(raw.get("asset_group_id")),
		"ad_external_id": _s(raw.get("creative_id")),
		"creative_id": _s(raw.get("creative_id")),
		"form_id": _s(raw.get("form_id")),
		"gclid": _s(raw.get("gcl_id") or raw.get("gclid"), 500),
		"is_test": 1 if _truthy(raw.get("is_test")) else 0,
		"touch_at": _when(raw.get("lead_submit_time")),
	}


def web_context(raw: dict) -> dict | None:
	"""Website enquiry: `utm` (utm_source or source keys), `extra`, top level, and the page URL's query.
	None when the enquiry carries no marketing parameter at all (nothing to attribute)."""
	utm = raw.get("utm") if isinstance(raw.get("utm"), dict) else {}
	extra = raw.get("extra") if isinstance(raw.get("extra"), dict) else {}
	page_q = _query(raw.get("page_url"))
	values: dict = {}
	for key in UTM_KEYS:
		short = key[4:]
		values[key] = _s(utm.get(key) or utm.get(short) or raw.get(key) or extra.get(key) or page_q.get(key), 255)
	for key in CLICK_IDS:
		values[key] = _s(utm.get(key) or raw.get(key) or extra.get(key) or page_q.get(key) or (page_q.get("_" + key) if key in ("fbc", "fbp") else None), 500)
	if not any(values.values()):
		return None
	values["fbc"] = values.get("fbc") or build_fbc(values.get("fbclid"))
	return {
		"touch_source": "Website", "channel": "Website", "provider": _provider_from(values),
		"campaign_external_id": values.get("utm_id"),
		"landing_url": _s(raw.get("page_url"), 2000), "touch_at": now_datetime(), **values,
	}


def ctwa_context(referral: dict, at=None) -> dict:
	"""WhatsApp Cloud API `messages[].referral` (click-to-WhatsApp ad or boosted post).

	`source_id` is the ad ID for source_type 'ad'. Meta omits `ctwa_clid` for ads shown in WhatsApp
	Status, so those are kept as 'ad (no click ID)'."""
	source_type = (_s(referral.get("source_type")) or "").lower() or None
	clid = _s(referral.get("ctwa_clid"), 500)
	note = None
	if source_type == "ad" and not clid:
		note = "ad (no click ID)"
	return {
		"touch_source": "CTWA", "provider": "Meta", "channel": "WhatsApp",
		"ad_external_id": _s(referral.get("source_id")) if source_type == "ad" else None,
		"referral_source_type": source_type, "ctwa_clid": clid, "attribution_note": note,
		"landing_url": _s(referral.get("source_url"), 2000), "headline": _s(referral.get("headline"), 255),
		"ad_body": _s(referral.get("body"), 2000), "media_type": _s(referral.get("media_type")),
		"touch_at": at or now_datetime(),
	}


# Sales Order field (webshop custom field first, then the v16 native field) per touch field.
ORDER_FIELDS = {**{k: (f"storefront_{k}", k) for k in UTM_KEYS}, **{k: (f"storefront_{k}",) for k in CLICK_IDS}, "landing_url": ("storefront_landing_page",)}


def order_context(doc) -> dict | None:
	values = {}
	for key, candidates in ORDER_FIELDS.items():
		values[key] = next((_s(doc.get(f), 2000 if key == "landing_url" else 500) for f in candidates if doc.get(f)), None)
	if not any(v for k, v in values.items() if k != "landing_url"):
		return None
	return {"touch_source": "Order", "channel": "Webshop", "provider": _provider_from(values), "campaign_external_id": values.get("utm_id"), "touch_at": now_datetime(), **values}


# ─── resolution: platform IDs → mirror links ─────────────────────────────────

def _find(doctype: str, provider: str | None, ids: list, fields: list[str]):
	ids = [i for i in ids if i]
	if not ids:
		return None
	filters = {"external_id": ["in", ids]}
	if provider:
		filters["provider"] = provider
	rows = frappe.get_all(doctype, filters=filters, fields=fields, order_by="creation asc", limit=1)
	return rows[0] if rows else None


def resolve(values: dict) -> dict:
	"""Links + IDs derived from the mirrors + scope + link_status. Reads only.

	Meta IDs are globally unique, so an ad ID alone finds its ad, ad set, campaign and account; the
	other ID levels are tried when the ad mirror is missing (ad-level sync can be off)."""
	provider = values.get("provider")
	ad_id, group_id, camp_id = values.get("ad_external_id"), values.get("ad_group_external_id"), values.get("campaign_external_id")
	out: dict = {}
	ad = group = camp = None
	if ad_id and provider in ("Meta", "Google", "LinkedIn"):
		# Google ad mirrors may be keyed "{ad_group_id}~{ad_id}" (an ad_group_ad resource).
		ad = _find("Ads Advertisement", provider, [ad_id, f"{group_id}~{ad_id}" if group_id else None], ["name", "account", "campaign", "ad_group", "ad_name"])
	if ad:
		out["advertisement"] = ad.name
		out["ad_name"] = values.get("ad_name") or ad.ad_name
		if ad.ad_group:
			group = frappe.db.get_value("Ads Ad Group", ad.ad_group, ["name", "account", "campaign", "external_id", "ad_group_name"], as_dict=True)
		if ad.campaign:
			camp = frappe.db.get_value("Ads Campaign", ad.campaign, ["name", "account", "external_id", "campaign_name"], as_dict=True)
	if not group and group_id and provider:
		group = _find("Ads Ad Group", provider, [group_id], ["name", "account", "campaign", "external_id", "ad_group_name"])
	if group:
		out["ad_group"] = group.name
		out["ad_group_external_id"] = group_id or group.external_id
		out["ad_group_name"] = values.get("ad_group_name") or group.ad_group_name
		if not camp and group.campaign:
			camp = frappe.db.get_value("Ads Campaign", group.campaign, ["name", "account", "external_id", "campaign_name"], as_dict=True)
	if not camp and camp_id and provider:
		camp = _find("Ads Campaign", provider, [camp_id], ["name", "account", "external_id", "campaign_name"])
	if camp:
		out["campaign"] = camp.name
		out["campaign_external_id"] = camp_id or camp.external_id
		out["campaign_name"] = values.get("campaign_name") or camp.campaign_name
	account = (ad or group or camp or {}).get("account") or values.get("account")
	if account:
		acc = frappe.db.get_value("Ads Account", account, ["name", "external_id", "company", "workspace", "brand"], as_dict=True)
		if acc:
			out.update(account=acc.name, account_external_id=acc.external_id, workspace=acc.workspace, company=acc.company or values.get("company"))
			out["brand"] = values.get("brand") or acc.brand
	has_ids = bool(ad_id or group_id or camp_id)
	if not has_ids:
		out["link_status"] = "Organic" if values.get("is_organic") else "No Ad IDs"
	else:
		complete = bool(camp and out.get("account") and (ad or not ad_id) and (group or not group_id))
		out["link_status"] = "Linked" if complete else ("Partial" if (ad or group or camp) else "Pending")
	return out


# ─── writes ───────────────────────────────────────────────────────────────────

def record_touch(ctx: dict, observation_id: str, **refs) -> frappe._dict | None:
	"""Write (or refresh) the one row for this observation. Returns the row's values with `name`.

	A replay never blanks a stored value: a webhook stub without Graph fields does not undo the
	fields a richer copy of the same lead already wrote."""
	if not ctx or not observation_id or not available():
		return None
	values = {k: v for k, v in {**ctx, **refs}.items() if k in TOUCH_FIELDS and v not in (None, "")}
	values["observation_id"] = str(observation_id)[:255]
	values.setdefault("touch_at", now_datetime())
	for k in ("is_test", "is_organic"):
		values[k] = cint(values.get(k))
	values.update({k: v for k, v in resolve(values).items() if v not in (None, "")})
	name = touch_name(values["touch_source"], values["observation_id"])
	if not frappe.db.exists(TOUCH, name):
		try:
			doc = frappe.get_doc({"doctype": TOUCH, **values})
			# Every link was just read from the database (or is the record being saved right now, e.g. a
			# Sales Order inside its own on_submit): skip the per-link re-validation queries.
			doc.flags.ignore_links = True
			doc.insert(ignore_permissions=True)
			return frappe._dict(values, name=name)
		except (frappe.DuplicateEntryError, frappe.UniqueValidationError):
			pass  # a concurrent writer got there first: refresh it below
	current = frappe.db.get_value(TOUCH, name, list(TOUCH_FIELDS), as_dict=True) or {}
	changed = {k: v for k, v in values.items() if k not in ("touch_at", "observation_id", "touch_source") and current.get(k) != v}
	# Flags only ever turn on: a test lead stays a test lead.
	for k in ("is_test", "is_organic"):
		if k in changed and not changed[k]:
			changed.pop(k)
	if changed:
		frappe.db.set_value(TOUCH, name, changed, update_modified=True)
	return frappe._dict({**current, **changed}, name=name)


def apply_to_record(ref, touch) -> bool:
	"""Copy the touch onto the Lead/Opportunity when it is that record's first ad touch (or the same one
	again, with links resolved since). Fills an empty campaign attribution through crm_compat."""
	if not ref or not touch or not ref.name:
		return False
	meta = frappe.get_meta(ref.doctype)
	if not meta.has_field("ads_first_touch"):
		return False
	current = frappe.db.get_value(ref.doctype, ref.name, "ads_first_touch")
	if current and current != touch.name:
		return False
	values = {f: touch.get(t) for f, t in RECORD_MAP.items() if meta.has_field(f) and touch.get(t) not in (None, "")}
	values["ads_first_touch"] = touch.name
	frappe.db.set_value(ref.doctype, ref.name, values, update_modified=False)
	# What the marketer named the campaign in the link wins over the platform's name for it.
	title = touch.get("utm_campaign") or touch.get("campaign_name")
	if title:
		from excom.excom.services import crm_compat

		crm_compat.fill_attribution(ref.doctype, ref.name, campaign=title, medium=MEDIUM.get(touch.get("provider")) if not touch.get("is_organic") else "organic")
	if not touch.get("reference_name"):
		frappe.db.set_value(TOUCH, touch.name, {"reference_doctype": ref.doctype, "reference_name": ref.name}, update_modified=False)
	return True


# ─── entry points ─────────────────────────────────────────────────────────────

def intake_context(source, raw: dict) -> dict | None:
	"""What an intake source tells about the ad behind a lead (pure)."""
	if source.source_type == "Meta Lead Ads":
		return meta_lead_context(raw)
	if source.source_type == "Google Lead Form":
		return google_lead_context(raw)
	if source.source_type == "Website":
		return web_context(raw)
	return None


def on_intake(ctx: dict | None, log, source, ref, identity: str | None) -> frappe._dict | None:
	"""Called by excom intake once the log produced (or found) its Lead."""
	if not ctx:
		return None
	touch = record_touch(ctx, log.dedupe_key, reference_doctype=ref.doctype if ref else None, reference_name=ref.name if ref else None, omni_identity=identity, source_log=log.name, company=source.company)
	if touch and ref:
		apply_to_record(ref, touch)
	return touch


def on_whatsapp_message(message_name: str, message: dict, channel_account, display_name: str = "") -> frappe._dict | None:
	"""An inbound WhatsApp message with `referral` (click-to-WhatsApp ad): touch row, thread fields, and the
	Lead of that conversation (created when Ads Settings → ctwa_create_lead is on, the default)."""
	referral = message.get("referral") if isinstance(message, dict) else None
	if not isinstance(referral, dict) or not referral or not message_name or not available():
		return None
	msg = frappe.db.get_value("Excom Message", message_name, ["thread", "omni_identity", "provider_message_id", "provider_timestamp"], as_dict=True)
	if not msg:
		return None
	ctx = ctwa_context(referral, msg.provider_timestamp)
	company = channel_account.get("company") if channel_account else None
	identity = msg.omni_identity
	resolved = resolve(ctx)

	ref = None
	records = gw.find_open_records_for_identity(identity) if identity else []
	if records:
		ref = records[0]
	else:
		from excom.excom_ads.settings import get_settings

		if cint(get_settings().ctwa_create_lead):
			from excom.excom.services import crm_flow

			oi = frappe.db.get_value("Omni Identity", identity, ["display_name", "primary_phone"], as_dict=True) or frappe._dict()
			payload = {
				"name": display_name or oi.display_name or oi.primary_phone, "phone": oi.primary_phone or message.get("from"),
				"company": company, "source": "Click to WhatsApp", "campaign": resolved.get("campaign_name"), "medium": "paid_social",
				"first_touch_channel": "WhatsApp", "source_reference": f"ctwa:{msg.provider_message_id or message_name}"[:140],
				"notes": "Started a WhatsApp chat from an ad" + (f": {ctx['headline']}" if ctx.get("headline") else ""),
			}
			ref, _created = crm_flow.resolve_or_create_lead(identity, "WhatsApp", payload, ignore_permissions=True)

	touch = record_touch(ctx, f"wa:{msg.provider_message_id or message_name}", omni_identity=identity, thread=msg.thread, message=message_name, company=company, reference_doctype=ref.doctype if ref else None, reference_name=ref.name if ref else None)
	if touch and msg.thread:
		frappe.db.set_value("Excom Thread", msg.thread, {
			"ad_source_type": touch.get("referral_source_type"), "ad_source_id": _s(referral.get("source_id")), "ads_advertisement": touch.get("advertisement"),
			"ctwa_clid": touch.get("ctwa_clid"), "ad_attribution_note": touch.get("attribution_note"), "ad_referred_at": touch.get("touch_at"),
			"ad_headline": touch.get("headline"), "ad_body": touch.get("ad_body"), "ad_media_type": touch.get("media_type"), "ad_source_url": touch.get("landing_url"),
		}, update_modified=False)
	if touch and ref:
		apply_to_record(ref, touch)
	return touch


def attach_thread_touches(thread: str, ref) -> bool:
	"""A Lead promoted from a conversation picks up the conversation's earliest unattached CTWA touch."""
	if not available() or not thread or not ref:
		return False
	name = frappe.db.get_value(TOUCH, {"thread": thread, "touch_source": "CTWA", "reference_name": ["is", "not set"]}, "name", order_by="touch_at asc")
	if not name:
		return False
	touch = frappe._dict(frappe.db.get_value(TOUCH, name, list(TOUCH_FIELDS), as_dict=True), name=name)
	return apply_to_record(ref, touch)


def on_sales_order_submit(doc, method=None) -> None:
	"""doc_event: an order that carries storefront UTM values or click IDs is an Order touch."""
	try:
		ctx = order_context(doc)
		if not ctx or not available():
			return
		identity = frappe.db.get_value("Omni Identity Link", {"linked_doctype": gw.CUSTOMER, "linked_name": doc.customer, "parenttype": "Omni Identity"}, "parent") if doc.get("customer") else None
		record_touch(ctx, f"so:{doc.name}", reference_doctype=doc.doctype, reference_name=doc.name, company=doc.company, omni_identity=identity)
	except Exception:
		frappe.log_error(title=f"Excom Ads: order attribution failed for {doc.name}", message=frappe.get_traceback())


def on_reference_trash(doc, method=None) -> None:
	"""doc_event: touches hold click IDs tied to a person; they go with the record they describe."""
	if available():
		frappe.db.delete(TOUCH, {"reference_doctype": doc.doctype, "reference_name": doc.name})


def relink_pending(provider: str | None = None, limit: int = 1000) -> dict:
	"""Resolve touches whose mirrors were missing when they were written (a lead that arrived before its
	campaign's structure sync), then refresh the records whose first touch they are. Idempotent."""
	if not available():
		return {"checked": 0, "linked": 0}
	filters = {"link_status": ["in", list(RELINK_STATUSES)], "touch_at": [">=", add_days(now_datetime(), -RELINK_DAYS)]}
	if provider:
		filters["provider"] = provider
	rows = frappe.get_all(TOUCH, filters=filters, fields=["name", *TOUCH_FIELDS], order_by="touch_at desc", limit=limit)
	improved = 0
	for row in rows:
		res = resolve(row)
		changed = {k: v for k, v in res.items() if v not in (None, "") and row.get(k) != v}
		if not changed:
			continue
		frappe.db.set_value(TOUCH, row.name, changed, update_modified=True)
		row.update(changed)
		improved += 1
		for dt in (gw.LEAD, gw.OPPORTUNITY):
			if not frappe.get_meta(dt).has_field("ads_first_touch"):
				continue
			for name in frappe.get_all(dt, filters={"ads_first_touch": row.name}, pluck="name"):
				apply_to_record(gw.ref(dt, name), row)
		if row.get("thread") and row.get("advertisement"):
			frappe.db.set_value("Excom Thread", row.thread, "ads_advertisement", row.advertisement, update_modified=False)
	frappe.db.commit()
	return {"checked": len(rows), "linked": improved}


def enqueue_relink(provider: str | None = None) -> None:
	frappe.enqueue("excom.excom_ads.attribution.relink_pending", queue="long", job_id=f"excom_ads::{frappe.local.site}::relink::{provider or 'all'}", deduplicate=True, provider=provider)


# ─── reading ──────────────────────────────────────────────────────────────────

def kpi_lead_filters() -> dict:
	"""Filters every lead KPI applies: platform test leads are never counted."""
	return {"ads_is_test": 0}


def verified_lead_counts(campaigns: list[str]) -> dict[str, int]:
	"""ERPNext-verified leads per Ads Campaign (test leads excluded). Kept apart from platform-reported leads."""
	if not campaigns or not frappe.get_meta(gw.LEAD).has_field("ads_campaign"):
		return {}
	rows = frappe.db.sql(
		f"SELECT ads_campaign, COUNT(*) FROM `tab{gw.LEAD}` WHERE ads_campaign IN %(c)s AND ads_is_test = 0 GROUP BY ads_campaign",
		{"c": tuple(campaigns)},
	)
	return {r[0]: cint(r[1]) for r in rows}
