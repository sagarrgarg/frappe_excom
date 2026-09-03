# Phase P3 — Native CRM Connection (as of v15)

**Phase:** P3 of 5 (`PLAN_001_master_phasing.md`)
**Design sources:** `design/HLD_003_native_crm_comms_flow.md` (N1–N3, N5), `design/RES_001_native_crm_lock_and_intake.md` (§2–§4, §7, §8), `design/UX_001_ui_redesign_plan.md` (U2)
**Duration:** 18–26 days
**Depends on:** P2 signed off
**Blocks:** P4 (needs the gateway seam it creates)
**Status:** Not started

---

## 1. Objective

Run Lead → Opportunity → Customer on **ERPNext native CRM only**, on the v15 schema installed today, with every enquiry source landing in it automatically and reps working the whole flow from excom.

**Definition of done:** a rep completes a full working day — Today → open record → advance stage → add task → reply on the right channel — without opening Desk; every enquiry from IndiaMART, TradeIndia, Meta lead ads and our websites creates exactly one Lead with correct provenance; and the marketplace first-response SLA is met by machine, not by a human being fast.

---

## 2. Non-negotiable framing

| # | Rule | Reason |
|---|---|---|
| F1 | **Native CRM only.** `Lead`, `Opportunity`, `Prospect`, `Customer`. Frappe CRM is not used and is uninstalled from the site at the end of this phase | HLD-003 §1.1; RES-001 §2 |
| F2 | **No shadow schema.** If a value is not on a native doctype or an excom-owned doctype, it does not exist | HLD-003 §1.3 |
| F3 | **One gateway.** `"Lead"`, `"Opportunity"`, `"Prospect"`, `"Quotation"` as doctype strings appear **only** in `crm_gateway.py` and `crm_compat.py` — enforced by CI | RES-001 §4.1; makes P4 cheap |
| F4 | **Attribution through the shim.** Never write `source` / `campaign_name` inline; v16 removes them | RES-001 §3.2 |
| F5 | **Website intake is a headless endpoint**, one intake source per site — not a Frappe Web Form | RES-001 §7.2 A |
| F6 | **Idempotency is a database constraint**, not a lookup | RES-001 §8 S4 |

---

## 3. Work breakdown

### 3.1 — Guardrails, gateway, compat, manifest (2 d) · **do this first**

**New:** `excom/excom/services/crm_gateway.py`, `excom/excom/services/crm_compat.py`, `roadmap/design/native_crm_manifest.yaml`, `excom/excom/tasks/guardrails.py`.

Gateway contract (the only vocabulary the rest of the app speaks):

```
create_lead(payload) -> ref            get_record(ref) -> dict
advance_stage(ref, stage)              convert(ref, target) -> ref
set_attribution(ref, source, campaign=None, medium=None)
link_identity(ref, identity)           list_pipeline(filters) -> [dict]
promote_thread(thread, customer_type) -> ref
```

`crm_compat.set_attribution()` writes `source` + `campaign_name` on v15 (creating `Lead Source` / `Campaign` rows as needed) and `utm_source` / `utm_campaign` / `utm_medium` on v16 (creating core `UTM Source` / `UTM Campaign` / `UTM Medium` rows), chosen by `frappe.get_meta(...).has_field(...)`.

Guardrails against the installed Frappe CRM app, as a scheduled `assert_native_crm_only()` that logs rather than throws:

| # | Assertion |
|---|---|
| G2 | `ERPNext CRM Settings.enabled = 0` |
| G3 | `CRM Settings.enable_frappe_crm_data_synchronization = 0`, `allowed_users` empty |
| G4 | Every `Email Account`: `create_lead_from_incoming_email = 0` — otherwise `crm.utils.create_lead_from_incoming_email` creates a CRM Lead **and rewrites `Communication.reference_doctype/name`** |
| G5 | No `crm_deal` / `crm_product_code` custom fields on `Quotation`, `Customer`, `Item`, `Prospect` |
| G6 | Zero `tabCRM Lead` / `tabCRM Deal` rows created after go-live |
| G7 | CI grep in excom: no `CRM Lead|CRM Deal|fcrm|from crm` |

### 3.2 — N1: schema and configuration (2–3 d, no frontend)

Custom Fields as excom fixtures — never by editing ERPNext JSON.

**On `Lead`:** `customer_type` (Select: Distributor / Retailer / Export Importer / OEM / Corporate Gifting / Online B2C), `omni_identity` (Link, read-only), `first_touch_at`, `first_touch_channel`, `first_touch_by`, `source_reference`, `intake_source` (Link → Excom Intake Source), `exhibition`, `auto_ack_sent_at`, `intake_stage` (Captured / Deduped / Responded / Classified / Qualified).

**On `Opportunity`:** `customer_type` (mandatory, fetched), `pipeline_stage` (Select — must be Select, Frappe Kanban requires it), `omni_identity`, `stage_entered_at`, `next_action_at`, `gate_flags` (Small Text JSON), `event_date`, `design_by`, `sample_round`, `incoterm`, `proposed_pincodes`.

**On `Prospect`:** `customer_type`, `omni_identity`. **On `Customer`:** `first_touch_*`, `source_reference`, `customer_type`.

Configuration: attribution rows via `set_attribution()` (IndiaMART Direct, IndiaMART Buy Lead, IndiaMART Call, TradeIndia, Meta Lead Ad, Website, Exhibition, Cold Call, Referral, Walk-in); `Opportunity Type`, `Sales Stage`, `Opportunity Lost Reason` rows; six Assignment Rules (Intake-Unclassified, Distributor, Retailer, Export, OEM, Gifting); `Sales Person` tree; User Permissions on `Company`; Customize Form to hide unused native fields and set type-driven `depends_on`.

**Deliverable:** the entire flow is operable in Desk. Everything after this is ergonomics.

### 3.3 — N2: excom ↔ CRM wiring (3–5 d)

**New:** `excom/excom/services/crm_flow.py`, `excom/excom/api/crm.py`. **Modified:** `hooks.py`, `services/identity_hooks.py`, `services/identity_sync.py`.

```python
doc_events = {
  "Opportunity": {"after_insert": "...crm_flow.on_opportunity_created",
                  "on_update":    "...crm_flow.on_opportunity_updated"},
  "Prospect":    {"after_insert": "...identity_hooks.on_entity_created"},
}
```

`crm_flow.py`: `resolve_or_create_lead`, `stamp_provenance` (write-once), `evaluate_gates` (per type), `advance_stage` (gate check → stage write → status-change log → thread system message), `on_conversion` (re-point `Omni Identity Link`, re-point open threads' `account_doctype`/`account`, copy provenance to Customer, post system message, carry `customer_type`), `map_stage_to_sales_stage`, sticky assignment (~20 lines: last owner on this identity gets the ToDo before the Assignment Rule sees it).

`api/crm.py`: `get_field_schema(doctype, customer_type)`, `get_intake_queue(filters)`, `get_pipeline(customer_type, filters)`, `get_record(doctype, name)`, `set_stage`, `classify_lead`, `promote_thread` (wraps native `make_lead_from_communication`), `convert` (wraps `make_opportunity` / `make_customer` / `make_quotation`), `get_gate_status`. Every endpoint calls `frappe.has_permission`; no `ignore_permissions` outside the intake path.

**Stage-duration history:** add `Excom Stage Change Log` (from / from_date / to / to_date / duration / owner) written by `advance_stage` — harvested from Frappe CRM's `CRM Status Change Log` design (RES-001 H5). `stage_entered_at` alone answers "how long in this stage"; the log answers "how long do deals sit in Compliance Check", which is the question that actually gets asked.

**Deliverable:** conversations and CRM records joined both ways; promote-to-lead works from the inbox.

### 3.4 — Intake spine (3 d)

**New doctypes:**

`Excom Intake Source` — `source_name`, `source_type` (Website / IndiaMART / TradeIndia / Meta Lead Ads / Exhibition / Manual), `enabled`, `company`, `channel_account`, `mode` (Push / Pull / Both), `pull_frequency`, `last_synced_at`, credentials (`api_key`, `api_secret`, `user_id`, `profile_id`, `access_token` — Password fields), `push_token`, `allowed_origins`, `allowed_ips`, `default_lead_owner`, `auto_ack_template`, `sla_first_response`, `field_map` (child: `source_key` → `target_fieldname` → `transform`).
**One row per feed** — per website/landing page, per marketplace account, per lead form.

`Excom Intake Log` — `source`, `dedupe_key` (**unique index**), `raw_payload`, `status` (Received / Processed / Duplicate / Failed / Ignored), `lead` (Dynamic Link), `omni_identity`, `error`, `received_at`, `processed_at`. Failed rows are replayable from the Desk form. Raw payloads purge at 90 days (buyer PII, DPDP).

**Shared pipeline** (`services/intake.py`), identical for all adapters:

```
adapter → Intake Log (raw, dedupe_key)        # unique index = idempotency
        → map via field_map
        → resolve_identity(phone=, email=, channel=, channel_user_id=)   # exists today
        → crm_gateway.create_lead(...) + set_attribution(...)
        → stamp_provenance(first_touch_*, source_reference, company)
        → thread_service.upsert_thread(identity, channel, account)
        → enqueue auto_ack (marketplace only, approved utility template)
        → Assignment Rule / sticky owner
        → log.status = Processed
```

### 3.5 — IndiaMART (2–3 d) · highest commercial return

**Pull (authoritative).** `excom.excom.tasks.intake.pull_indiamart`, every 5 minutes, `start_time = last_synced_at − 5 min` (deliberate overlap; `dedupe_key` absorbs it):

```
GET https://mapi.indiamart.com/wservce/crm/crmListing/v2/
      ?glusr_crm_key=<key>&start_time=<t0>&end_time=<t1>
```

`dedupe_key` = `UNIQUE_QUERY_ID`, also written to `source_reference`. Separate intake sources for Direct / Buy Lead / Call so lead quality is measurable per stream (HLD-003 Q4). Non-200 → backoff and retry, logged, never swallowed. The key expires after ~15 days of inactivity, so it joins `tasks/token_monitor.py`, and a "no leads and no successful call in 3× `pull_frequency`" alarm fires independently.

**Push (accelerator).** `excom.excom.api.intake.indiamart_push` — guest POST, authenticated by an unguessable `push_token` matched against the source, optional IP allowlist, IP-keyed rate limit. Push is never the source of truth: a missed push self-heals within 5 minutes via the poller.

**Auto-ack.** On create, enqueue a pre-approved **utility** WhatsApp template to `mobile_no` (email fallback), write `auto_ack_sent_at`, post the outbound message into the thread. Content: courteous acknowledgement plus one qualifying question, never a price.

### 3.6 — Website (2 d)

`excom.excom.api.intake.submit_enquiry` — `@frappe.whitelist(allow_guest=True, methods=["POST"])`, `@rate_limit(key="ip", limit=10, seconds=60)`.

```json
{ "source_token": "<per-site>", "submission_id": "<client uuid>",
  "name": "...", "email": "...", "phone": "...", "message": "...",
  "utm": {"source": "...", "medium": "...", "campaign": "...", "content": "..."},
  "page_url": "https://site.example/pricing", "extra": { } }
```

One `Excom Intake Source` per site/landing page, each with its own token, `allowed_origins`, company, owner and SLA — a leaked token is revoked for one site, not all. CORS headers returned only on origin match; origins containing CSP/header metacharacters rejected. Spam controls: honeypot, minimum fill-time, IP rate limit, optional Turnstile/hCaptcha verified server-side per source. `submission_id` → `dedupe_key`, so a retried POST returns the same 200. Response is `{"ok": true, "ref": "<log id>"}` — never reveals whether the email is already known. `utm.*` through `set_attribution()`; `page_url` retained on the log.

Ships with `excom-intake.js` (~30 lines, no dependencies) so each site wires its existing form markup without touching layout.

### 3.7 — Meta lead ads (3–4 d)

**Refactor first.** Today `_process_webhook_payload` (`utils/webhook.py:139`) reads `entry[0].changes[0].value.metadata.phone_number_id` and returns early when no WhatsApp account matches — so `leadgen`, Instagram and comment payloads are acknowledged 200 and **silently dropped**. Replace with a dispatch table on `entry[].changes[].field`:

| field | Handler | Phase |
|---|---|---|
| `messages` | existing WhatsApp path | now |
| `leadgen` | new | this step |
| `feed` / `comments` / `mentions` | new | later (UX-001's Comments view) |
| `messaging` (IG/Messenger) | new | later |

A handler exception must not 500 the endpoint; each is wrapped and logged to `Excom Intake Log`.

**`leadgen` handler.** The webhook carries ids only — `leadgen_id`, `page_id`, `form_id`, `ad_id`, `adgroup_id`, `created_time` — so the job fetches the lead from the Graph API with the Page access token (`leads_retrieval` permission, App Review required; start the review during 3.2). Answers map through `field_map` populated by fetching the form definition, so a marketer adding a question needs no release. `dedupe_key` = `leadgen_id`.

**Reconciliation.** Nightly poll of `/{form_id}/leads?fields=id,created_time,field_data&filtering=[time_created > watermark]` **with pagination** — the reference implementation we studied uses `limit: 100000  # TODO: pagination`, which is exactly the bug not to inherit.

### 3.8 — TradeIndia (1–2 d)

Pull only, every 15 minutes. Credentials `userid`, `profile_id`, key — read from *TradeIndia → Inquiries & Contacts → My Inquiry API*, which also shows the API link; store host/path on the source row rather than hardcoding it. `dedupe_key` = inquiry id. Same pipeline, same auto-ack (15-minute target).

### 3.9 — Security hardening (1–2 d)

| # | Change |
|---|---|
| S2 | `_verify_hmac_signature` (`utils/webhook.py:43`) currently returns **True for unsigned requests when no WhatsApp account has an app secret**. Reject unsigned once a secret exists for the *receiving object*; look secrets up per Meta app/page, not per WhatsApp account; log every degraded acceptance |
| S5 | IP-keyed `@rate_limit` on every guest endpoint (today: 4 of 136 whitelisted functions, all user-keyed) |
| S6 | `ignore_permissions` confined to the intake path, behind the gateway |
| S8 | `token_monitor.py` extended to IndiaMART key (15-day inactivity expiry), Meta Page tokens, TradeIndia key |
| S7 | 90-day purge job for `Excom Intake Log.raw_payload` |

### 3.10 — CRM UI (6–8 d) — UX-001 U2 unhidden

Built on the P1 shell; no new layout work.

- **Details tab** rendered from `get_field_schema` — adding a Custom Field in Desk makes it appear on reload with no frontend release. Start against a fixture JSON matching the endpoint's contract so this work runs parallel to 3.3.
- **Context strip**: stage · gate chips · next action · amount, lens-aware.
- **`crm-today`**: overdue `next_action_at`, then SLA-breaching threads, then today's actions, then unassigned for my teams.
- **`crm-intake`**: S1–S5 queue by `intake_stage`, SLA pip, bulk classify, one-click open conversation.
- **`crm-pipeline`**: kanban on `pipeline_stage`, one board per `customer_type`, drag validates gates and writes `pipeline_stage` + `stage_entered_at` + mapped `sales_stage`/`probability`; blocked drags surface the failing gate inline. **On phone this is a stage-picker list, not a drag board.**
- **Convert / promote** actions with inline gate reasons.

### 3.11 — SLA, escalation, analytics (3–5 d) — N5

`excom/excom/tasks/crm_sla.py`, hourly, patterned on the existing `delivery_watchdog.py`. Targets per source (IndiaMART 5 min · TradeIndia 15 min · Meta 30 min · web 1 h · organic 4 h); classification 4 business hours; qualification 2 business days; stage ageing; `next_action_at` overdue; reply latency 4 business hours.

Escalation ladder: breach → in-app notification to owner; +30 min → WhatsApp to owner's `User.mobile_no`; +2 h → team manager; +1 day → unassign back to the intake queue with a logged reason.

Funnel analytics per type / source / company added to the existing `AnalyticsPage`.

> Working-hours and holiday handling is deliberately simple here (elapsed time + a holiday list). If false out-of-hours breaches appear, that is the trigger to build the fuller SLA model in P5.

---

## 4. Sequencing

```
3.1 guardrails ──┬──▶ 3.2 schema ──▶ 3.3 wiring ──┬──▶ 3.10 CRM UI ──▶ 3.11 SLA
                 └──▶ 3.4 intake spine ──┬──▶ 3.5 IndiaMART ──┐
                                          ├──▶ 3.6 Website     ├──▶ 3.9 hardening
                                          ├──▶ 3.7 Meta        │
                                          └──▶ 3.8 TradeIndia ─┘
```

Intake (3.4–3.9) and UI (3.10) are independent after 3.3 — two people run them in parallel. If capacity exists during P2, pull 3.4–3.5 forward: it depends only on the guardrails and is the highest-return item in the roadmap.

---

## 5. Testing

| Track | Content |
|---|---|
| Intake replay | Re-POST, re-poll and webhook-retry each source → exactly one Lead. Kill the worker mid-job → resume without duplication |
| Provenance | Every path stamps `first_touch_*`, `source_reference`, `company`, `intake_source`; write-once holds on re-touch |
| Identity | Existing Customer enquiring again creates **no** duplicate Lead (HLD-003 R1); precedence Customer → Opportunity → open Lead → new |
| Gates | Each type's gates block advancement; override requires the right role and writes a comment |
| Conversion | Lead → Opportunity → Customer re-points identity links and open threads, copies provenance, posts a system message; prior links retained |
| SLA | Simulated breaches walk the full escalation ladder; auto-ack measured against `creation` |
| Permissions | Company/territory User Permissions honoured on every `api/crm.py` endpoint; `if_owner` roles see only their own |
| Security | Unsigned Meta request rejected once a secret exists; bad `push_token` rejected; origin mismatch rejected; rate limits trip |
| UI | Kanban at 1366×768 (5 columns, cards ≤132 px); phone stage-picker; Details tab picks up a newly added Custom Field |

---

## 6. Exit gates

| # | Gate |
|---|---|
| E1 | A rep runs a full day without opening Desk |
| E2 | New Custom Field in Desk → visible in Details on reload, no frontend change |
| E3 | Replay-safe: every source produces exactly one Lead per enquiry under retry |
| E4 | IndiaMART auto-ack **≤5 minutes** from `creation`, evidenced by `auto_ack_sent_at` |
| E5 | `assert_native_crm_only()` clean; Frappe CRM uninstalled from the site |
| E6 | CI: zero native doctype strings outside `crm_gateway.py` / `crm_compat.py` |
| E7 | Every guest endpoint rate-limited and authenticated; no accept-on-missing-signature path remains |
| E8 | Attribution written through `set_attribution()` everywhere — grep shows no direct `source =` / `utm_source =` writes |

---

## 7. Risks

| # | Risk | Mitigation |
|---|---|---|
| R1 | Vendor payload shapes are undocumented and change | `field_map` + raw payload retention + replay; never parse positionally |
| R2 | Meta App Review for `leads_retrieval` takes weeks | Start during 3.2; poll-only with a test-app Page token meanwhile |
| R3 | IndiaMART key expires silently after 15 days idle | Token monitor + stale-watermark alarm |
| R4 | One Meta URL for WhatsApp + leads + IG + comments concentrates blast radius | Per-field handlers, per-object secrets, handler exceptions contained |
| R5 | Someone ticks `create_lead_from_incoming_email` in Desk → shadow CRM Leads | G4 guardrail; uninstall the app at E5 |
| R6 | Auto-ack reads as robotic to returning customers | Marketplace sources only in v1 (HLD-003 Q6) |
| R7 | Gate rules encoded only in the UI | `evaluate_gates` is server-side; Desk users are bound by the same rule |

---

## 8. Effort

| Step | Days |
|---|---|
| 3.1 guardrails/gateway/compat/manifest | 2 |
| 3.2 schema · 3.3 wiring | 5–8 |
| 3.4 spine · 3.5 IndiaMART · 3.6 Website | 7–8 |
| 3.7 Meta · 3.8 TradeIndia · 3.9 hardening | 5–8 |
| 3.10 CRM UI | 6–8 |
| 3.11 SLA + analytics | 3–5 |
| **Total (with parallelism)** | **18–26** |
