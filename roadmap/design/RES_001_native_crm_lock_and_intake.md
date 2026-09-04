# RES-001 — Native CRM Lock-In, Frappe CRM Harvest, and Lead Intake Endpoints

**Document:** RES-001 (research)
**Version:** 1.0
**Date:** 2026-09-03
**Owner:** Sagar Ratan Garg
**Relates to:** `HLD_003_native_crm_comms_flow.md` (§2, §4, §10, Q8), `UX_001_ui_redesign_plan.md`
**Scope:** (1) prove and keep excom on ERPNext native CRM, (2) plan for owning the spine if native CRM ever goes away, (3) harvest what Frappe CRM does well, (4) specify the missing intake endpoints — website, IndiaMART, TradeIndia, Meta lead ads.

Everything below is verified against the code on this bench (`erpnext` 15.120.0, `frappe` 15.119.1, `crm` 2.0.0-dev @ `5245a51a`) and against `frappe/erpnext@develop` (= v16) fetched live. Where a fact came from a vendor doc rather than code, it is marked **[vendor]** and must be re-confirmed against the seller panel before implementation.

---

## Table of Contents

1. [Verdict Summary](#part-1--verdict-summary)
2. [Native-Only: current state and contamination vectors](#part-2--native-only)
3. [What v16 does to native CRM](#part-3--what-v16-does-to-native-crm)
4. [Owning the spine — the anti-corruption layer](#part-4--owning-the-spine)
5. [Harvest from Frappe CRM](#part-5--harvest-from-frappe-crm)
6. [Intake endpoints — current state](#part-6--intake-endpoints-current-state)
7. [Intake endpoints — specification](#part-7--intake-endpoints-specification)
8. [Security contract for all intake](#part-8--security-contract)
9. [Gaps, risks, open decisions](#part-9--gaps-risks-open-decisions)

---

# Part 1 — Verdict Summary

| # | Question | Answer |
|---|---|---|
| 1 | Is excom coupled to Frappe CRM today? | **No.** Zero references to `CRM Lead`, `CRM Deal`, `fcrm`, or `crm.` anywhere in `apps/excom` (Python, JSON, TS). The app is already native-only |
| 2 | Is the Frappe CRM app harmless while installed? | **No — three live vectors.** Site-wide `Communication`/`ToDo` doc hooks, an ERPNext-side bridge that writes custom fields onto `Quotation`/`Customer`/`Item`, and its own scheduler jobs. §2.2 |
| 3 | Is native CRM deprecated in v16? | **No.** `frappe/erpnext@develop` still ships the full `erpnext/crm` module — Lead, Opportunity (+ items), Prospect, Sales Stage, lost reasons. §3.1 |
| 4 | Does v16 change anything we depend on? | **Yes, one thing, and HLD-003 depends on it.** `Lead.source`, `Lead.campaign_name`, `Opportunity.source`, `Opportunity.campaign` and the whole `Lead Source` doctype are **gone**, replaced by UTM fields + core `UTM Source`/`UTM Medium`/`UTM Campaign`. §3.2 |
| 5 | Can we own the spine if native CRM ever goes? | Yes, and cheaply — **if** we route every native call through one gateway module now. §4 |
| 6 | Do we have intake endpoints for website / IndiaMART / TradeIndia / Meta leads? | **None of the four exist.** The only Meta webhook is WhatsApp-only and silently drops every non-WhatsApp payload. §6 |
| 7 | Can we copy Frappe CRM code? | **No.** `apps/crm` is **AGPL-3.0**; excom is **MIT**. Harvest designs, write our own code. §5 |

---

# Part 2 — Native-Only

## 2.1 Excom is clean

```
grep -riE "CRM Lead|CRM Deal|CRM Task|fcrm|apps/crm|from crm|import crm" apps/excom  →  0 hits
```

`excom/hooks.py` hooks only native doctypes — `Customer`, `Supplier`, `Lead`, `Contact`, `Party Link` — via `identity_hooks.on_entity_created`. HLD-003 §2.3's claim holds: `Opportunity` and `Prospect` are the only two missing, and adding them is additive.

`resolve_identity()` (`excom/excom/doctype/omni_identity/omni_identity.py:146`) already implements the full HLD-003 §4.3 ladder — normalized phone → phone/WhatsApp alias → `channel_user_id` → normalized email → email alias → ERPNext Contact reverse lookup (phone, then email) → create. It takes `phone`, `email`, `channel`, `channel_user_id`, `display_name`, so **every intake adapter in Part 7 can call it directly** with whatever the source gives us. No new resolution code is needed.

## 2.2 Contamination vectors from the installed `crm` app

Frappe CRM is installed on the live site (`testerp-1.rbcolour.com`, idx 9, `2.0.0-dev`, branch `feat/extensible-form-tabs-develop`). Three ways it reaches into our data even though excom never calls it:

**V1 — Site-wide `Communication` hook.** `crm/hooks.py:174` registers `Communication.after_insert → crm.utils.on_communication_insert`, which calls `create_lead_from_incoming_email()` (`crm/utils/__init__.py:194`). Guards: `sent_or_received == "Received"`, no existing `reference_doctype`, an `email_account`, and the per-account flag `create_lead_from_incoming_email` (a custom field the crm app adds to `Email Account`). If that flag is ever ticked, an inbound email creates a **CRM Lead** *and* rewrites `Communication.reference_doctype`/`reference_name` to point at it — a shadow funnel plus stolen linkage.

**V2 — Site-wide `ToDo` hooks.** `crm/hooks.py:170` registers `ToDo.after_insert` and `ToDo.on_update` → `crm.api.todo.*`. This matters because UX-001 puts the Tasks tab on core `ToDo`: our tasks would run through Frappe CRM's handlers.

**V3 — The ERPNext-side bridge, which ships in ERPNext v15 itself.** `ERPNext CRM Settings` (the singleton in the `crm` app) writes custom fields when enabled — `Quotation.crm_deal`, `Customer.crm_deal`, `Item.crm_product_code`, `CRM Deal.erpnext_customer` (`crm/fcrm/doctype/erpnext_crm_settings/erpnext_crm_settings.py:94–145`), and can do it **on a remote site** via `FrappeClient`. In the other direction, ERPNext 15 already carries `erpnext/crm/frappe_crm_api.py` with whitelisted endpoints such as `create_prospect_against_crm_deal`, plus a "Frappe CRM" section in `CRM Settings` (`allowed_users` + `enable_frappe_crm_data_synchronization`). This is ERPNext shipping a bridge *to* Frappe CRM, not the reverse.

**V4 — Namespace and scheduler overlap.** `crm/hooks.py:74` claims the website routes `/crm/<path>` and `/crm-form/<route>`; its scheduler runs `crm.lead_syncing.background_sync.*` and `crm.telemetry.capture_feature_state` on our site every hour/day.

## 2.3 Guardrails (config-level, verifiable)

| # | Guardrail | Check |
|---|---|---|
| G1 | Frappe CRM uninstalled from the site after HLD-003 N1 sign-off (HLD-003 Q8) | `bench --site <s> list-apps` shows no `crm` |
| G2 | Until then: `ERPNext CRM Settings.enabled = 0` | Desk / `frappe.db.get_single_value` |
| G3 | Until then: `CRM Settings.enable_frappe_crm_data_synchronization = 0`, `allowed_users` empty | as above |
| G4 | Every `Email Account`: `create_lead_from_incoming_email = 0` | one query over `tabEmail Account` |
| G5 | No custom fields named `crm_deal` / `crm_product_code` on `Quotation`, `Customer`, `Item`, `Prospect` | query `tabCustom Field` |
| G6 | No rows in `tabCRM Lead` / `tabCRM Deal` created after the excom go-live date | one query; a non-zero count means a shadow funnel started |
| G7 | CI grep in excom: no `CRM Lead|CRM Deal|fcrm|from crm` | pre-commit hook |

G2–G6 belong in a scheduled `excom.excom.tasks.guardrails.assert_native_crm_only()` that logs an error rather than throwing — a config drift detector, not a blocker.

---

# Part 3 — What v16 Does to Native CRM

## 3.1 The module survives

`frappe/erpnext@develop` (v16) `erpnext/crm/doctype/` contains 30 entries — `lead`, `opportunity`, `opportunity_item`, `opportunity_type`, `opportunity_lost_reason(+_detail)`, `prospect`, `prospect_lead`, `prospect_opportunity`, `sales_stage`, `competitor(+_detail)`, `campaign`, `email_campaign`, `contract*`, `appointment*`, `crm_note`, `crm_settings`, `market_segment`, `lost_reason_detail`, `frappe_crm_allowed_user`. Opportunity keeps `items[]`, `sales_stage`, `probability`, `first_response_time`, `competitors`, `lost_reasons`. Prospect is byte-for-byte the same field list as v15.

**Conclusion: the Lead → Opportunity → Quotation → Sales Order chain that HLD-003 §8 is built on is intact in v16.** HLD-003's core bet is not at risk.

## 3.2 The one real change: attribution moves to UTM

| | v15 (installed) | v16 (`develop`) |
|---|---|---|
| `Lead.source` | Link → `Lead Source` | **removed** |
| `Lead.campaign_name` | Link → `Campaign` | **removed** |
| `Opportunity.source`, `Opportunity.campaign` | present | **removed** |
| `Lead Source` doctype | `erpnext/crm/doctype/lead_source` | **deleted** (only a dashboard chart of the same name remains) |
| New on Lead + Opportunity | — | `utm_source`, `utm_medium`, `utm_campaign`, `utm_content`, `utm_analytics_section` |
| Target doctypes | ERPNext-owned | **frappe core**: `frappe/website/doctype/utm_source`, `utm_medium`, `utm_campaign` |
| Migration | — | `erpnext/patches/v15_0/migrate_to_utm_analytics.py` — copies every `Lead Source` into `UTM Source`, every `Campaign` into `UTM Campaign` (also setting `crm_campaign`), then deletes the `Lead Source` doctype |

This lands squarely on HLD-003 §2.2, which lists `source (→ Lead Source)` and `campaign_name` among "native fields reused rather than duplicated", and on §12 N1, which seeds nine `Lead Source` rows (IndiaMART Direct, IndiaMART Buy Lead, TradeIndia, Meta Lead Ad, Website, Exhibition, Cold Call, Referral, Walk-in).

**Required amendment to HLD-003:**

1. Excom's own provenance fields stay the system of record: `first_touch_channel`, `first_touch_at`, `first_touch_by`, `source_reference` (already specified), **plus a new `Excom Source` link** (Part 7.1) that we own outright.
2. Native attribution is written through a shim, never inline:

```python
# excom/excom/services/crm_compat.py  (illustrative)
def set_attribution(doc, source: str, campaign: str | None = None, medium: str | None = None):
    meta = frappe.get_meta(doc.doctype)
    if meta.has_field("utm_source"):          # v16+
        doc.utm_source = ensure("UTM Source", source)
        if campaign: doc.utm_campaign = ensure("UTM Campaign", campaign)
        if medium:   doc.utm_medium  = ensure("UTM Medium", medium)
    elif meta.has_field("source"):            # v15
        doc.source = ensure("Lead Source", source)
        if campaign: doc.campaign_name = ensure("Campaign", campaign)
```

3. N1's nine seed rows are created in whichever doctype exists — same names either way, so reports keep working across the upgrade.
4. The intake adapters (Part 7) call `set_attribution()`; they never touch `source` or `utm_source` directly.

## 3.3 Direction of travel — read honestly

ERPNext v15 already ships a bridge toward Frappe CRM (`frappe_crm_api.py`, the "Frappe CRM" section in `CRM Settings`, the `Frappe CRM Allowed User` child table), and v16 hands attribution primitives to frappe core. Native CRM is being **thinned and re-pointed**, not deleted. The prudent posture is therefore neither panic nor complacency: keep using native, but make the dependency surface small, enumerated and swappable — Part 4.

---

# Part 4 — Owning the Spine

The requirement: *if native CRM is ever deprecated, excom owns everything from Opportunity to sales pipeline.* That is affordable only if we never scatter native doctype names across the codebase.

## 4.1 The anti-corruption layer

One module — `excom/excom/services/crm_gateway.py` — is the **only** place in excom that may name a native doctype or fieldname. Everything else (API, UI, intake, hooks) speaks the gateway's vocabulary.

| Gateway function | v15/v16 implementation | Post-native implementation |
|---|---|---|
| `create_lead(payload) -> ref` | `frappe.get_doc({"doctype": "Lead", ...})` | `Excom Lead` |
| `get_record(ref)` | native read + `get_field_schema` | own doctype |
| `advance_stage(ref, stage)` | `pipeline_stage` custom field + `sales_stage` mapping | own field |
| `convert(ref, target)` | `lead.make_opportunity` / `make_customer` / `make_quotation` | own mapper |
| `set_attribution(...)` | §3.2 shim | own field |
| `link_identity(ref)` | `Omni Identity Link` (already ours) | unchanged |
| `list_pipeline(filters)` | query `Opportunity` | own query |

Rule, enforceable by grep in CI: **`"Lead"`, `"Opportunity"`, `"Prospect"`, `"Quotation"` as doctype strings may appear only in `crm_gateway.py` and `crm_compat.py`.**

## 4.2 The dependency manifest

A checked-in `roadmap/design/native_crm_manifest.yaml` listing every native doctype, field, and helper excom relies on, with the reason. Today that list is small:

- Doctypes: `Lead`, `Opportunity`, `Opportunity Item`, `Prospect`, `Customer`, `Contact`, `Party Link`, `Quotation` (read-only), `Company`, `Territory`, `Sales Stage`, `Opportunity Lost Reason`, `Incoterm`.
- Helpers: `make_opportunity`, `make_customer`, `make_quotation`, `make_lead_from_communication` (HLD-003 §2.1).
- Fields: the reuse list in HLD-003 §2.2, minus `source`/`campaign_name` (§3.2).

Every addition to that file is a deliberate decision. This is what makes a future fork mechanical rather than archaeological.

## 4.3 What a fork would actually cost

If native CRM disappeared tomorrow, excom would have to replace:

| Native capability | Replacement effort |
|---|---|
| Lead/Opportunity/Prospect schema + naming series | 3–4 d (doctypes + fixtures) |
| `make_*` transition mappers (4 helpers) | 3–5 d — the fiddly part is Quotation/Sales Order field mapping |
| `Opportunity Item` + pricing/currency/conversion | 4–6 d (or: keep quoting in Desk, link by name) |
| Sales Stage / probability / lost reasons | 1–2 d |
| Native funnel + sales reports | 3–5 d (or accept excom's own analytics) |
| Permissions: Company/Territory User Permissions, `Sales Person` tree | 2–3 d |
| **Total** | **~16–25 d**, provided §4.1 holds. Without the gateway, multiply by 2–3 |

Explicitly **not** forked: Quotation, Sales Order, Delivery Note, Invoice. Those are ERP documents, not CRM; HLD-003 §12 already says "do not rebuild Desk Quotation".

---

# Part 5 — Harvest from Frappe CRM

**Licence constraint first.** `apps/crm/LICENSE` is **GNU AGPL-3.0**; `apps/excom/license.txt` is **MIT**. Copying code across is a licence conflict. Everything below is *design harvest* — read it, then write our own.

| # | Feature | Where in `apps/crm` | Verdict |
|---|---|---|---|
| H1 | **Lead sync framework** — `Lead Sync Source` (enabled, type, credentials, `background_sync_frequency`, `last_synced_at`) + `Failed Lead Sync Log` (type, raw `lead_data`, source, traceback, retry) + frequency-bucketed scheduler jobs (`Every 5/10/15 Minutes`, Hourly, Daily, Monthly) | `crm/lead_syncing/` | **Adopt the pattern.** This is exactly our marketplace intake spine (Part 7.1). Copy the *shape*: watermark, per-source frequency, failure log with replay |
| H2 | **Facebook lead-form mapping** — `Facebook Page` → `Facebook Lead Form` → `Facebook Lead Form Question` (`key` → `mapped_to_crm_field`), fetch `/{form_id}/leads?fields=id,created_time,field_data` filtered by `time_created > last_synced_at` | `crm/lead_syncing/doctype/lead_sync_source/facebook.py` | **Adopt, and fix two weaknesses:** it uses `limit: 100000  # TODO: pagination` (no paging), and it is **poll-only** — no `leadgen` webhook, so it cannot meet a 5-minute SLA reliably. We add the webhook and keep polling as reconciliation |
| H3 | **Hybrid web forms** — delegate storage/validation to core `Web Form`; the app only curates mappable fields (`SUPPORTED_FIELDTYPES`, `DENIED_FIELDNAMES`), caps Link options (`MAX_LINK_OPTIONS = 500`), validates embedding domains against a regex that rejects CSP metacharacters, supports `?embed=1`, and re-applies source/organization enrichment on submit | `crm/api/form.py`, `crm/www/crm_form.py` | **Rejected for us — enquiries arrive from several external sites we do not host, so a Frappe-hosted form is the wrong shape (§7.2 A).** Two ideas still harvested: the curated field allowlist/denylist, and the domain-allowlist regex that rejects CSP metacharacters — both reused in the headless endpoint |
| H4 | **SLA engine** — `CRM Service Level Agreement`: `apply_on`, `condition` + `condition_json`, `priorities[]`, `working_hours[]`, `holiday_list`, `start/end_date`, `rolling_responses`; plus `CRM Rolling Response Time` | `crm/fcrm/doctype/crm_service_level_agreement/` | **Adopt the model.** HLD-003 §10.2 notes ERPNext has no CRM SLA doctype and proposes Notifications + an hourly task. This gives us the field model to build `Excom SLA` properly — especially `working_hours` + `holiday_list`, which a naive "5 minutes since creation" check gets wrong |
| H5 | **Stage-duration history** — `CRM Status Change Log` (`from`, `from_date`, `to`, `to_date`, `duration`, `log_owner`, `from_type`, `to_type`) | `crm/fcrm/doctype/crm_status_change_log/` | **Adopt.** HLD-003 only has `stage_entered_at` (current stage). A log row per transition gives stage-ageing analytics and the Activity tab (UX-001 §6.3) for free |
| H6 | **Task model** — `CRM Task`: title, priority, status, `start_date`, `due_date`, `assigned_to`, `reference_doctype`/`reference_docname` | `crm/fcrm/doctype/crm_task/` | **Reference only.** UX-001 Q4 puts Tasks on core `ToDo`; this confirms the field set we need (priority + start + due + reference) |
| H7 | **Schema-driven layouts and saved views** — `CRM Fields Layout`, `CRM View Settings` (with `clear_old_versions` housekeeping), `CRM Form Script` | `crm/fcrm/doctype/crm_fields_layout|crm_view_settings|crm_form_script/` | **Adopt the concept**, already mirrored by HLD-003 §3.4 `get_field_schema` and UX-001's saved views. Confirms the approach is the norm, not an invention |
| H8 | **Telephony** — `CRM Call Log`, `CRM Telephony Agent`, Twilio + Exotel adapters with guest webhook handlers (`crm/integrations/twilio/api.py`, `crm/integrations/exotel/handler.py`) | as listed | **Reference for Phase C.** Note their guest endpoints carry `# nosemgrep: guest-whitelisted-method` — i.e. even upstream treats these as deliberate exceptions needing justification |
| H9 | **Domain enrichment** — crawler → extractors → mapper → pipeline, seeded rules, cross-record enrichment | `crm/domain_enrichment/` | **Later.** Enrich company data from an email domain. Nice-to-have after Phase B AI |
| H10 | Deal/lead duplicate handling, `CRM Notification`, `CRM Invitation`, dashboards, `CRM Sales Hierarchy` | `crm/fcrm/doctype/*` | **Skip.** We have Omni Identity merge, Excom Notification, Excom Team + ERPNext `Sales Person` tree |

Two structural lessons worth stealing outright: **(a)** every external source is a row in one doctype with a frequency and a watermark, not a bespoke integration; **(b)** every failed inbound lead becomes a durable log row with the raw payload and a one-click replay, never a swallowed exception.

---

# Part 6 — Intake Endpoints: Current State

## 6.1 Complete inventory of guest-reachable endpoints in excom

| Endpoint | File | Purpose | Auth |
|---|---|---|---|
| `excom.excom.utils.webhook.webhook` | `utils/webhook.py:19` | Meta webhook (WhatsApp only) | `X-Hub-Signature-256` HMAC, with a fallback (§6.3) |
| `excom.excom.channels.whatsapp.api.webhook` | `channels/whatsapp/api.py:8` | Channel-scoped alias of the above | same |
| `...whatsapp.api.handle_flow_request` / `api.flow_endpoint.handle_flow_request` | `api/flow_endpoint.py:12` | WhatsApp Flow data exchange | Flow encryption |
| `excom.excom.api.webchat.*` (5 endpoints) | `api/webchat.py` | Web chat widget: config, session, send, poll, end | session token |
| `excom.excom.api.unsubscribe.unsubscribe` | `api/unsubscribe.py:14` | Broadcast unsubscribe | signed link |
| `excom.excom.api.mobile.get_client_id` | `api/mobile.py:68` | Push client id | none |
| `excom.www.excom.get_context_for_dev` | `www/excom.py:75` | Dev bootstrap | none (POST) |

136 `@frappe.whitelist()` functions in total; `frappe.rate_limiter.rate_limit` is used in exactly four places, all in `api/chat.py` (60/min, 120/min, 30/min, 30/min, keyed by user).

**There is no website-form intake, no IndiaMART, no TradeIndia, and no Meta lead-ads endpoint. Four of four are missing.**

## 6.2 The Meta webhook is WhatsApp-only

`_process_webhook_payload()` (`utils/webhook.py:139`) reads `entry[0].changes[0].value.metadata.phone_number_id`, looks up an `Excom Channel Account`, and does:

```python
channel_account = get_channel_account(phone_id) if phone_id else None
if not channel_account:
    return
```

A `leadgen` change, an Instagram messaging event, a Page `feed` comment — none carry `phone_number_id`, so **all of them are dropped silently** after being acknowledged with HTTP 200. Meta sees success and never retries.

Related: `README.md` and `roadmap/README.md` describe Instagram as a shipped channel ("Phase 3 — Omnichannel Expansion — DONE (MVP)"), but `grep -ril instagram apps/excom/excom --include=*.py` returns **nothing**. Instagram exists in the frontend labels and icons only. Meta comments/DMs are therefore greenfield, not an extension.

## 6.3 HMAC fallback to accept

`_verify_hmac_signature()` (`utils/webhook.py:43`) returns **`True` when no request signature is present and no WhatsApp account has an app secret configured** — deliberate graceful degradation for half-configured accounts. It also only ever compares against secrets stored on `channel = "whatsapp"` accounts. Before any lead-ads traffic shares this endpoint, both need to change: unsigned requests must be rejected once any secret exists for the *receiving object*, and the secret lookup must be per Meta app/page, not per WhatsApp account.

---

# Part 7 — Intake Endpoints: Specification

Design goal: **one intake spine, four adapters**, so a fifth source (JustDial, Alibaba, an exhibition CSV) is a row plus a mapper, never a new subsystem.

## 7.1 Two new doctypes

**`Excom Source`** — one row per external lead feed.

| Field | Type | Notes |
|---|---|---|
| `source_name` | Data | Unique; also the attribution value written by `set_attribution()` (§3.2) |
| `source_type` | Select | `Website`, `IndiaMART`, `TradeIndia`, `Meta Lead Ads`, `Exhibition`, `Manual` — one row **per website/landing page**, not one row for "the website" (§7.2 A1) |
| `allowed_origins` | Small Text | Website sources: the origins permitted to POST (§7.2 A2) |
| `enabled` | Check | |
| `company` | Link → Company | Stamped onto every Lead from this source (HLD-003 §11.2) |
| `channel_account` | Link → Excom Channel Account | Which WhatsApp number / inbox auto-acks (HLD-003 §4.5) |
| `mode` | Select | `Push` (webhook), `Pull` (poller), `Both` |
| `pull_frequency` | Select | `Every 5 Minutes`, `Every 15 Minutes`, `Hourly`, `Daily` — harvested from H1 |
| `last_synced_at` | Datetime | Watermark; pulls always re-query with a 5-minute overlap |
| `credentials` | Password fields | `api_key`, `api_secret`, `user_id`, `profile_id`, `access_token` as applicable |
| `push_token` | Password | Secret path token for push endpoints that have no HMAC |
| `allowed_ips` | Small Text | Optional CIDR allowlist |
| `default_lead_owner` | Link → User | |
| `auto_ack_template` | Link → WhatsApp Templates | Utility template, HLD-003 §9.4 |
| `sla_first_response` | Duration | 5 min IndiaMART, 15 min TradeIndia, 30 min Meta, 1 h web (HLD-003 §10.1) |
| `field_map` | Table → `Excom Source Field Map` | `source_key` → `target_fieldname` → `transform` |

**`Excom Source Log`** — one row per inbound payload, the audit trail and the replay queue.

| Field | Type | Notes |
|---|---|---|
| `source` | Link → Excom Source | |
| `dedupe_key` | Data, **unique index** | IndiaMART `UNIQUE_QUERY_ID`, Meta `leadgen_id`, TradeIndia inquiry id, Web Form submission name. This *is* the idempotency mechanism |
| `raw_payload` | Long Text | Verbatim, before any mapping |
| `status` | Select | `Received`, `Processed`, `Duplicate`, `Failed`, `Ignored` |
| `lead` | Dynamic Link | The created record |
| `omni_identity` | Link | Resolved identity |
| `error` | Long Text | Traceback |
| `received_at`, `processed_at` | Datetime | Feeds the SLA measurement |

Retention: raw payloads purge at 90 days (they contain buyer PII — DPDP, per HLD unified §5.7); log rows keep the dedupe key indefinitely.

## 7.2 The four adapters

### A. Website — headless endpoint, not a Frappe Web Form

**Decision (supersedes H3):** enquiries come from several websites and landing pages we do not host on this site, each on its own URL. A Frappe-hosted `Web Form` would mean either redirecting buyers off the marketing site or iframing our page into it — both worse than letting each site POST its own form to us. So: **one headless JSON endpoint, many registered origins.**

```
excom.excom.api.intake.submit_enquiry
    @frappe.whitelist(allow_guest=True, methods=["POST"])
    @rate_limit(key="ip", limit=10, seconds=60)

POST /api/method/excom.excom.api.intake.submit_enquiry
  { "source_token": "<per-site token>",       # identifies the Excom Source
    "submission_id": "<client uuid>",          # dedupe_key
    "name": "...", "email": "...", "phone": "...",
    "message": "...",
    "utm": {"source": "...", "medium": "...", "campaign": "...", "content": "..."},
    "page_url": "https://site.example/pricing",
    "extra": { ... }                           # anything unmapped, kept in raw_payload
  }
```

Rules:

| # | Rule | Why |
|---|---|---|
| A1 | One `Excom Source` row **per website/landing page**, each with its own `source_token`, `company`, `channel_account`, `default_lead_owner` and SLA | Attribution and company routing are structural (HLD-003 §11.2), and a leaked token is revoked for one site, not all |
| A2 | `allowed_origins` on the source; the endpoint returns CORS headers only for a match and rejects a mismatched `Origin`/`Referer` | Harvested from H3's domain-allowlist regex — reject any value containing CSP/header metacharacters |
| A3 | Field mapping via `field_map` rows, never positional; unmapped keys survive in `raw_payload` | A marketer adding a field must not need a release (S10) |
| A4 | Spam controls: honeypot field, minimum fill-time, per-IP rate limit, optional hCaptcha/Turnstile token verified server-side per source | A public write endpoint with no CSRF needs its own defences |
| A5 | `submission_id` → `Excom Source Log.dedupe_key`; a retried POST returns the same result, 200 | Flaky mobile networks double-submit |
| A6 | Response is minimal — `{"ok": true, "ref": "<log id>"}`. Never echo whether the email already exists | No enumeration oracle |
| A7 | `utm.*` written through `crm_compat.set_attribution()`; `page_url` retained on the log | Survives the v15 → v16 attribution change (§3.2) |

A thin `excom-intake.js` snippet (≈30 lines, no dependencies) ships with the app so each site can wire its existing form markup without touching layout.

> Core `Web Form` remains available for anything hosted on this Frappe site (an internal enquiry page, an exhibition capture form). It is simply not the website path.

### B. IndiaMART

Two mechanisms, both worth having; the pull is the reliable one, the push is the fast one.

**Pull (primary).** Verified against a working ERPNext integration **[vendor]**:

```
GET https://mapi.indiamart.com/wservce/crm/crmListing/v2/
      ?glusr_crm_key=<key>&start_time=<t0>&end_time=<t1>
```

- `glusr_crm_key` is generated from *seller.indiamart.com → Lead Manager → CRM Integration* and mailed to the primary address; **it expires after ~15 days of no use** — so `tasks/token_monitor.py` (already exists for WhatsApp tokens) must watch it too.
- Scheduler entry: `excom.excom.tasks.intake.pull_indiamart` at `Every 5 Minutes`, `start_time = last_synced_at − 5 min` (overlap by design; `Excom Source Log.dedupe_key` absorbs the duplicates).
- Vendor guidance suggests polling every 5–15 minutes and requesting only the delta **[vendor]**; published 429/limit numbers are not in the public docs, so the poller must treat a non-200 as backoff-and-retry, not as an error to swallow.
- Map `UNIQUE_QUERY_ID` → `dedupe_key` and `source_reference`; split attribution into `IndiaMART Direct` / `IndiaMART Buy Lead` / `IndiaMART Call` sources (HLD-003 Q4).

**Push (optional, for the ≤5 min SLA).**

```
excom.excom.api.intake.indiamart_push   @frappe.whitelist(allow_guest=True, methods=["POST"])
```
IndiaMART's push API posts to a URL you register in the seller panel and carries no HMAC **[vendor]**, so authentication is: an unguessable `push_token` in the path/query matched against `Excom Source`, plus optional IP allowlist, plus `rate_limit(key="ip")`. Treat push as an *accelerator*: the poller remains the source of truth so a missed push self-heals within 5 minutes.

### C. TradeIndia

Pull only. Credentials `userid`, `profile_id`, and API key come from *TradeIndia → Inquiries & Contacts → My Inquiry API*, which also displays the API link **[vendor]** — the exact host/path must be read from that panel at configuration time and stored on `Excom Source`, not hardcoded. Frequency `Every 15 Minutes` (HLD-003 §4.2 sets a 15-minute first-response target). Dedupe on the inquiry id.

### D. Meta lead ads (+ comments, + Instagram)

**One Meta entry point, dispatched by field.** Refactor `utils/webhook.py` so `_process_webhook_payload` routes on `entry[].changes[].field`:

| `field` | Handler | Result |
|---|---|---|
| `messages` | existing WhatsApp path | thread + message |
| `leadgen` | **new** | fetch + create Lead |
| `feed` / `comments` / `mentions` | **new** (later) | comment item + thread |
| `messaging` (IG/Messenger) | **new** (later) | thread + message |

Verified mechanics for `leadgen` **[vendor, Meta developer docs]**:
1. The app subscribes the Page: `POST /{page-id}/subscribed_apps?subscribed_fields=leadgen` with a **Page access token**.
2. On submission Meta posts a notification containing `leadgen_id`, `page_id`, `form_id`, `ad_id`, `adgroup_id`, `created_time` — **not** the answers.
3. Excom then fetches the lead by id from the Graph API with the Page token; the app needs the **`leads_retrieval`** permission (plus `pages_manage_ads`/pages permissions for ad-level fields) and must pass App Review.

So: webhook → verify HMAC → log raw → enqueue → fetch `/{leadgen_id}` → map via `field_map` → `resolve_identity()` → Lead + thread + auto-ack. Add a nightly reconciliation poll of `/{form_id}/leads?filtering=[time_created>watermark]` (H2's approach, **with pagination**) to catch anything the webhook missed.

Form-question mapping mirrors H2: a child table of `source_key` → `target_fieldname` populated by fetching the form definition, so a marketer adding a question does not require a code change.

## 7.3 Shared pipeline — every adapter ends the same way

```
adapter ──▶ Excom Source Log (raw, dedupe_key)          # idempotent: unique index
        ──▶ map via field_map
        ──▶ resolve_identity(phone=, email=, channel=, channel_user_id=)   # exists today
        ──▶ crm_gateway.create_lead(...)  +  set_attribution(...)          # §3.2, §4.1
        ──▶ stamp_provenance(first_touch_*, source_reference, company)      # HLD-003 §4.4
        ──▶ thread_service.upsert_thread(identity, channel, account)
        ──▶ enqueue auto_ack (marketplace only, utility template)           # HLD-003 §4.5
        ──▶ Assignment Rule / sticky owner                                  # HLD-003 §7.3
        ──▶ log.status = Processed, processed_at = now
```

Nothing in this chain is new except the first two steps and the gateway — HLD-003 already specifies the rest, and `resolve_identity`, `upsert_thread`, `whatsapp_service` and the Assignment Rules exist.

---

# Part 8 — Security Contract

Applies to every endpoint in Part 7. Phase A of the roadmap already established HMAC, sanitisation, rate limiting and RBAC as ship-blockers; this extends the same rules to intake.

| # | Rule | Implementation |
|---|---|---|
| S1 | **Authenticate every inbound** | Meta: `X-Hub-Signature-256` HMAC-SHA256 over the raw body with `hmac.compare_digest`, per receiving object's app secret. IndiaMART push: secret path token + optional IP allowlist. Web form: framework CSRF |
| S2 | **No accept-on-missing-signature** | Fix `utils/webhook.py:43` — once any secret exists for the receiving object, unsigned requests are 403. Keep degradation only for accounts with no secret at all, and log a warning each time it is used |
| S3 | **Respond fast, process later** | 200 within Meta's 20 s window: log raw → `frappe.enqueue(queue="short")` → return. Already the pattern in `webhook.py:129` |
| S4 | **Idempotency is a database constraint** | Unique index on `Excom Source Log.dedupe_key`; never a "does it already exist?" query race |
| S5 | **Rate limit every guest endpoint** | `@rate_limit(key="ip", ...)` — today only four whitelisted functions in the entire app are rate-limited, all user-keyed |
| S6 | **`ignore_permissions` only inside the intake path** | HLD-003 §3.4's rule; the gateway is the boundary |
| S7 | **Raw payload retention + PII** | 90-day purge, per-source; buyer phone/email are personal data under DPDP |
| S8 | **Credential monitoring** | Extend `tasks/token_monitor.py` to IndiaMART key (15-day inactivity expiry **[vendor]**), Meta Page tokens, TradeIndia key |
| S9 | **Replay, not loss** | Every `Failed` log row is replayable from the Desk form (harvested from H1's `Failed Lead Sync Log`) |
| S10 | **Never trust vendor field names** | All mapping through `field_map` rows; an unmapped key lands in `raw_payload` and is visible, not dropped |

---

# Part 9 — Gaps, Risks, Open Decisions

## 9.1 Corrections this research forces on existing documents

| Doc | Correction |
|---|---|
| HLD-003 §2.2 | `Lead.source`, `Lead.campaign_name`, `Opportunity.source`, `Opportunity.campaign` do not exist in v16. Route attribution through `crm_compat.set_attribution()`; add `Excom Source` as our own provenance link |
| HLD-003 §12 N1 | "Seed `Lead Source` rows" becomes "seed intake sources + native attribution rows in whichever doctype the version provides" |
| HLD-003 §10.2 | SLA design should adopt the working-hours/holiday/priority model from H4 rather than a bare elapsed-time check |
| HLD-003 Q8 | Uninstalling `crm` is no longer just hygiene — V1/V2/V3 in §2.2 are concrete write paths into our data |
| `README.md`, `roadmap/README.md` | Instagram is claimed as a shipped channel; there is no Instagram code in the backend. Fix the claim or build the adapter |
| UX-001 Part 5 | The Meta-comments view needs the `feed`/`comments` webhook branch (§7.2 D) before it can render anything |

## 9.2 Risks

| # | Risk | Mitigation |
|---|---|---|
| R1 | v16 upgrade silently drops attribution written to `source` | Shim now (§3.2); manifest test asserts one of the two field sets exists |
| R2 | Frappe CRM's `Email Account` flag gets ticked by someone in Desk → shadow CRM Leads | G4 + the scheduled guardrail check; better, uninstall |
| R3 | IndiaMART key expiring after 15 days of inactivity kills intake silently **[vendor]** | S8 monitoring + alert when `last_synced_at` is older than 3× `pull_frequency` |
| R4 | Meta App Review for `leads_retrieval` takes weeks | Start the review during N1; poll-only mode works meanwhile with a Page token from a test app |
| R5 | Vendor payload shapes are undocumented publicly and change | `field_map` + raw payload retention + replay; never parse positionally |
| R6 | Sharing one Meta webhook URL across WhatsApp, lead ads, IG and comments concentrates blast radius | Dispatch table with per-field handlers and per-object secrets; a handler exception must not 500 the whole endpoint |
| R7 | AGPL code from `apps/crm` accidentally pasted into MIT excom | Review rule: patterns only, no copy-paste; note it in `AGENTS.md`/`CLAUDE.md` |

## 9.3 Open decisions

| # | Decision | Recommendation |
|---|---|---|
| D1 | Uninstall `crm` from the site now, or after N1? | **After N1 sign-off**, with G2–G6 enforced immediately in the meantime |
| D2 | Website capture: core `Web Form` or headless endpoint? | **Decided — headless `submit_enquiry`** (§7.2 A). Enquiries originate on several external sites/URLs we do not host; one intake source row per site, per-site token + origin allowlist. Core Web Form only for pages hosted on this site |
| D3 | IndiaMART: push + pull, or pull only? | **Both**, pull authoritative. Push alone cannot be trusted; pull alone risks the 5-minute SLA |
| D4 | One Meta webhook or one per field? | **One**, with a dispatch table — Meta sends everything for an app to one URL anyway |
| D5 | Build `Excom SLA` (H4) now or keep HLD-003 §10.2's notification approach? | **Notifications for N3; the doctype when the second source with different targets appears** |
| D6 | Instagram/Meta comments in this scope? | **No** — separate slice after lead ads, but the dispatch table must be built to accept it |
| D7 | Where does `Excom Source` live relative to `Excom Channel Account`? | Separate doctype, linked. A source is a *lead feed*; an account is a *conversation endpoint*. IndiaMART has no conversation endpoint |

## 9.4 Suggested sequencing (fits HLD-003 N-phases)

| Step | Work | Days |
|---|---|---|
| 0 | Guardrails G2–G7 + `crm_compat.set_attribution()` + `crm_gateway.py` skeleton + manifest | 2 |
| 1 | `Excom Source` + `Excom Source Log` + shared pipeline (§7.3) | 3 |
| 2 | IndiaMART pull (+push) — highest commercial return, per HLD-003 §12 N3 | 2–3 |
| 3 | Website via core Web Form | 1–2 |
| 4 | Meta lead ads: webhook dispatch refactor + `leadgen` handler + reconciliation poll | 3–4 |
| 5 | TradeIndia pull | 1–2 |
| 6 | HMAC hardening (S2), rate limits (S5), token monitoring (S8) | 1–2 |
| | **Total** | **13–18 d**, overlapping HLD-003 N2/N3 |
