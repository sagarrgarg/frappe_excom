# PLAN-001 — Master Phasing

**Document:** PLAN-001
**Version:** 1.0
**Date:** 2026-09-03
**Owner:** Sagar Ratan Garg
**Consolidates:** `UX_001_ui_redesign_plan.md`, `HLD_003_native_crm_comms_flow.md`, `RES_001_native_crm_lock_and_intake.md`, `roadmap/phase_B_ai_layer.md`, `roadmap/phase_C_voice_channel.md`

Order of work, as decided:

```
P1  UI Change ─▶ P2  Excom Testing ─▶ P3  Native CRM Connection (v15) ─▶ P4  Owning the Spine ─▶ P5  Frappe CRM Harvest (separate app, later)
```

Two things cut across the sequence and are called out where they land:

- **The gateway seam is P3 discipline, not P4 work.** If P3 scatters `"Lead"` / `"Opportunity"` doctype strings through the codebase, P4 costs 2–3× more (RES-001 §4.3). One day of P3 buys the whole of P4's optionality.
- **Website intake is a headless endpoint, not a Frappe Web Form** — enquiries arrive from several external sites on their own URLs, so each site posts to us with its own token and origin allowlist (RES-001 §7.2 A).

| Phase | Name | Days | Gate to next phase |
|---|---|---|---|
| P1 | UI Change | 16–24 | New UI reaches feature parity behind the flag |
| P2 | Excom Testing | 8–12 | Pilot opt-out < 10 %, defect list burned down, default flipped |
| P3 | Native CRM Connection (v15) | 18–26 | A rep runs a full day without opening Desk; marketplace SLA met by machine |
| P4 | Owning the Spine | 6–9 (+ contingency fork 16–25) | v16 dry-run migration passes; fork plan costed and rehearsed |
| P5 | Frappe CRM Harvest | — | Deliberately unscheduled; triggers listed in §6 |
| | **Committed total (P1–P4)** | **48–71 days** | |

---

# P1 — UI Change

**Source:** UX-001 phases U1 and U3, plus the parts of U2 that need no CRM data.
**Goal:** the app people actually use every day stops being cluttered, works on every screen, and is one component tree.

## 1.1 Scope — in

| # | Deliverable | Notes |
|---|---|---|
| 1 | Design tokens + primitives | Light crayon palette on chalk neutrals, no gradients, 12 px type floor (UX-001 Part 2) |
| 2 | Router | `react-router-dom` is installed and unused today; navigation is `useState<AppPage>`. Real URLs, working back button |
| 3 | Responsive shell | 4 breakpoints (<640 / 640–1023 / 1024–1439 / ≥1440), reference target 1366×768 @1x |
| 4 | Merged conversation + `Reply via ▾` | Replaces channel tabs, account selector and the "viewing & replying via" banner — three bars become one control |
| 5 | Saved views + search chips | Replaces the seven stacked filters in `LeftSidebar.tsx` |
| 6 | Four-tier disclosure pass | Raven-style: T1 always / T2 hover-and-swipe / T3 `⋯` / T4 ⌘K |
| 7 | Record tabs — **Chat, Tasks, Notes, Activity** | These three non-Chat tabs run on core `ToDo`, core `Comment` and `Version` + thread system messages, **all of which exist in v15 today** — no CRM fields needed |
| 8 | Admin pages re-skinned | Broadcasts (wizard), Subscribers, Rules, Merge, Teams, Analytics, Settings — and made responsive, which none are today |
| 9 | Mobile tree retired | `components/mobile/` deleted once parity is signed off — it is why the phone keeps missing features (calls tab is a "Coming Soon" placeholder; broadcasts/teams/analytics/merge/subscribers do not exist on phone) |

## 1.2 Scope — out (deferred to P3)

The **Details** tab (schema-driven from `get_field_schema`), the context strip's stage/gate chips, and the `crm-today` / `crm-intake` / `crm-pipeline` pages. All need custom fields that do not exist yet. P1 ships the tab bar with Details hidden behind the same feature flag, so P3 is an unhide, not a re-layout.

## 1.3 Parallel running

Single bundle, two trees. Resolution order `?ui=next` → `localStorage.excom_ui` → per-user flag → default `legacy`. Shared `hooks/*` so there is one data layer. Legacy frozen (bug fixes only) from day 1.

## 1.4 Exit gates

- All 42 controls on the control checklist mapped old → new.
- Chrome above the message list ≤112 px at 1366×768; message area ≥400 px.
- CI gates green: zero `bg-gradient-*`, zero sub-12 px text classes outside the numeric-badge allowlist, no hardcoded hex in `src/components`.
- No overlap or horizontal scroll at 360/390/414/640/768/834/1024/1280/1366/1440/1920, DPR 1 and 2, zoom 100/125/150 %.
- Back button works phone: list → thread → details.

**Effort: 16–24 days.**

---

# P2 — Excom Testing

**Goal:** prove the whole app — new UI *and* the existing channel/broadcast/identity machinery — under real use, before any CRM work lands on top of it. This is the phase that makes P3 safe to build on.

## 2.1 Test tracks

| # | Track | Content |
|---|---|---|
| T1 | **Pilot** | 6 users, 2 per lens (Sales / Accounts / Compliance), one full working day each in week 1, one full week in week 2. Click log keyed by control id settles the T1/T2/T3 tier arguments with data |
| T2 | **Functional sweep** | Every channel path end to end: WhatsApp inbound/outbound, templates, session-window expiry → template fallback, media, delivery receipts and the 10-minute delivery timer, Gmail inbound poll + send, webchat session, broadcasts + subscriber rules + unsubscribe, merge suggestions, team transfer, tags, canned responses, stickers, pinned, internal notes |
| T3 | **Responsive/DPI sweep** | The UX-001 §11 matrix, captured as screenshots per release |
| T4 | **Load and realtime** | 2 000-message thread scroll, 50 concurrent threads, socketio reconnect, optimistic-send under packet loss, background/foreground on mobile |
| T5 | **Security pass** | Rate limits on the 136 whitelisted functions (only 4 are limited today, all user-keyed), permission checks per role, webhook HMAC behaviour including the accept-on-missing-signature path (RES-001 §6.3), token expiry alerts |
| T6 | **Data integrity** | Identity merge/unmerge, duplicate detection, thread re-pointing, no orphaned messages, idempotency on webhook replay |

## 2.2 Fix-forward rules

- Defects triaged P0 (blocks pilot) / P1 (blocks flip) / P2 (post-flip backlog); P0 same-day, P1 before the gate.
- Every P0/P1 gets a regression test or a checklist line — not a fix-and-forget.
- Anything the pilot reports as "I couldn't find X" is a **tier misassignment**, fixed by moving the control, not by adding a tooltip.

## 2.3 Exit gates

- Zero open P0/P1.
- Pilot opt-out rate < 10 %, and every opt-out has a written reason that is either fixed or explicitly accepted.
- T2 sweep 100 % pass on the checklist; T4 no regression vs legacy on thread-open latency.
- **Default flipped to the new UI**, opt-out available for two weeks, then legacy deleted.

**Effort: 8–12 days.** (Overlaps P1's tail — T3/T5 can start as soon as U1 lands.)

---

# P3 — Native CRM Connection (as of v15)

**Source:** HLD-003 N1–N3 + N5, RES-001 §7 (intake), UX-001 U2 (CRM surfaces).
**Goal:** Lead → Opportunity → Customer running on **ERPNext native CRM only**, with every enquiry source landing in it automatically, and reps working it from excom.

## 3.1 Guardrails first (day 1–2, non-negotiable)

| # | Item | Ref |
|---|---|---|
| G-a | `crm_gateway.py` created — the **only** module allowed to name `Lead` / `Opportunity` / `Prospect` / `Quotation`, enforced by CI grep | RES-001 §4.1 |
| G-b | `crm_compat.set_attribution()` — writes `source`/`campaign_name` on v15, `utm_*` on v16 | RES-001 §3.2 |
| G-c | `native_crm_manifest.yaml` — every native doctype, field and helper we depend on, with the reason | RES-001 §4.2 |
| G-d | Frappe CRM contamination guardrails G2–G7 + scheduled `assert_native_crm_only()` | RES-001 §2.3 |

G-a/G-b/G-c cost about a day between them and are what make P4 a 6–9 day phase instead of a rewrite.

## 3.2 Work items

| Step | Work | Depends on | Days |
|---|---|---|---|
| 3.1 | Guardrails + gateway + compat + manifest | — | 2 |
| 3.2 | **N1 schema**: Custom Fields on Lead/Opportunity/Prospect/Customer as excom fixtures; `Opportunity Type`, `Sales Stage`, `Opportunity Lost Reason` rows; attribution rows via G-b; six Assignment Rules; `Sales Person` tree; Company User Permissions; Customize Form hide/show | 3.1 | 2–3 |
| 3.3 | **N2 wiring**: `Opportunity` + `Prospect` hooks, `sync_single_opportunity`/`sync_single_prospect`, `services/crm_flow.py` (provenance, gates, stage advance, conversion bookkeeping, sticky assignment), `api/crm.py` (`get_field_schema`, `get_record`, `promote_thread`, `set_stage`, `convert`), thread re-pointing on conversion | 3.2 | 3–5 |
| 3.4 | **Intake spine**: `Excom Source` + `Excom Source Log` (unique `dedupe_key`) + shared pipeline → identity → gateway → thread → auto-ack → assignment | 3.1 | 3 |
| 3.5 | **IndiaMART**: pull every 5 min with −5 min overlap watermark (`crmListing/v2` + `glusr_crm_key`), optional push with per-source token; dedupe on `UNIQUE_QUERY_ID`; key-expiry monitoring | 3.4 | 2–3 |
| 3.6 | **Website**: headless `submit_enquiry` — one intake source per site/landing page, per-site token, origin allowlist, honeypot + fill-time + IP rate limit, `submission_id` idempotency, `utm.*` through G-b, plus the ~30-line `excom-intake.js` snippet | 3.4 | 2 |
| 3.7 | **Meta lead ads**: refactor the Meta webhook into a dispatch table on `entry[].changes[].field` (today it routes on `phone_number_id` and silently drops everything else), add the `leadgen` handler (webhook gives ids only → Graph fetch with Page token, `leads_retrieval` + App Review), nightly form-poll reconciliation **with pagination** | 3.4 | 3–4 |
| 3.8 | **TradeIndia**: pull every 15 min (`userid` / `profile_id` / key read from the seller panel, stored on the source row) | 3.4 | 1–2 |
| 3.9 | **HMAC + rate-limit hardening**: reject unsigned Meta requests once a secret exists for the receiving object; per-object secrets; IP-keyed limits on every guest endpoint; extend `token_monitor.py` to IndiaMART/Meta/TradeIndia credentials | 3.7 | 1–2 |
| 3.10 | **CRM UI (UX-001 U2)**: Details tab from `get_field_schema`, context strip with stage + gate chips, `crm-today`, `crm-intake`, `crm-pipeline` (phone = stage picker, not a drag board), convert/promote actions with inline gate reasons | 3.3 | 6–8 |
| 3.11 | **SLA + escalation (N5)**: `tasks/crm_sla.py` breach detection, escalation ladder, funnel analytics per type/source/company | 3.3, 3.5 | 3–5 |

Steps 3.4–3.9 (intake) and 3.10 (UI) are independent after 3.3 — two people can run them in parallel.

## 3.3 Start IndiaMART early if capacity allows

HLD-003 §12 rates N3 as the phase that pays for itself first: marketplace leads are sold to several suppliers at once and the first responder usually wins. Steps 3.4–3.5 depend only on the guardrails, not on the UI. If a second pair of hands is free during P2, this is the thing to pull forward.

## 3.4 Exit gates

- A rep runs a full day — Today → record → advance stage → task → reply — without opening Desk.
- Adding a Custom Field in Desk makes it appear in the Details tab on reload, with no frontend change.
- Every source creates exactly one Lead per enquiry under replay: re-POST, re-poll and webhook-retry all land on the same `dedupe_key`.
- IndiaMART auto-ack measured **≤ 5 minutes** from `creation`, evidenced by `auto_ack_sent_at`.
- `assert_native_crm_only()` clean: no `CRM Lead`/`CRM Deal` rows created after go-live, no `crm_deal` custom fields on `Quotation`/`Customer`/`Item`.
- Zero `"Lead"` / `"Opportunity"` doctype strings outside `crm_gateway.py` + `crm_compat.py` (CI).

**Effort: 18–26 days.**

---

# P4 — Owning the Spine

**Goal:** make the native dependency reversible. Not a fork — a *rehearsed ability* to fork, plus the v16 upgrade handled.

## 4.1 Why this is a phase, not a panic

RES-001 §3 verified: `frappe/erpnext@develop` (v16) still ships the whole CRM module — Lead, Opportunity with `items[]` and `sales_stage`, Prospect unchanged. Native CRM is **not** deprecated. What v16 *does* change is attribution: `Lead.source`, `Lead.campaign_name`, `Opportunity.source`, `Opportunity.campaign` and the `Lead Source` doctype are removed in favour of `utm_*` fields pointing at frappe-core `UTM Source` / `UTM Medium` / `UTM Campaign`, with `erpnext/patches/v15_0/migrate_to_utm_analytics.py` doing the data move. G-b already absorbs that.

The residual risk is directional: ERPNext v15 already ships a bridge toward Frappe CRM (`erpnext/crm/frappe_crm_api.py`, the "Frappe CRM" section of CRM Settings), and v16 hands attribution primitives to core. So P4 is insurance, sized deliberately.

## 4.2 Work items

| # | Item | Days |
|---|---|---|
| 4.1 | **v16 dry run**: clone the site, upgrade to v16 in a scratch bench, run migrate, verify attribution migrated, gateway tests green, fixtures apply, no data loss. Produce a written upgrade runbook | 2–3 |
| 4.2 | **Gateway hardening**: contract tests for every gateway function against native v15 *and* v16; a compatibility test that fails loudly if a manifested field disappears | 1–2 |
| 4.3 | **Manifest enforcement**: CI check that every native field excom reads is in `native_crm_manifest.yaml`, and that every manifest entry still exists on the installed version | 1 |
| 4.4 | **Fork rehearsal**: build `Excom Lead` as a *shadow doctype* behind the gateway, write the mapper, run the test suite against it in CI only. Not shipped, not installed — proof the seam works | 2–3 |
| 4.5 | **Costed fork plan**: what a real fork replaces (schema + naming, the four `make_*` mappers, `Opportunity Item` pricing, stages/probability/lost reasons, funnel reports, Company/Territory permissions), with the decision criteria for pulling the trigger | included |

**Contingency (only if native CRM is actually withdrawn): 16–25 days** to execute the fork, per RES-001 §4.3. Explicitly *not* forked in any scenario: Quotation, Sales Order, Delivery Note, Invoice — those are ERP documents and stay in Desk.

## 4.3 Exit gates

- The site upgrades to v16 in the scratch bench with attribution intact and every gateway contract test green.
- Removing a manifested field in a test fixture makes CI fail with a clear message.
- The shadow-doctype run passes the same contract tests as native — i.e. the seam is real, not theoretical.

**Effort: 6–9 days.**

---

# P5 — Frappe CRM Harvest (separate app, unscheduled)

Deliberately not on the critical path. Frappe CRM stays **uninstalled** from the site after P3 (RES-001 D1); nothing from it is adopted as a dependency.

**Licence boundary, decided once:** `apps/crm` is **AGPL-3.0**, excom is **MIT**. We take designs, never code. If we ever want to run actual CRM code, it goes in a **separate app** with its own licence, talking to excom over the gateway — which is exactly why P4 exists.

Candidates, with the trigger that would justify building each (all designs, to be written from scratch):

| Candidate | Build when |
|---|---|
| SLA engine with working hours + holiday list + priorities (their `CRM Service Level Agreement` model) | The simple `crm_sla.py` from P3 §3.11 starts producing false breaches out of hours |
| Stage-duration history (their `CRM Status Change Log`: from/to/duration/owner) | Someone asks "how long do deals sit in Compliance Check?" twice |
| Configurable field layouts + saved views as data (`CRM Fields Layout`, `CRM View Settings`) | Users start requesting per-team layouts faster than releases can ship them |
| Domain enrichment (crawl the email domain → company data) | Manual research time on inbound leads becomes visible |
| Telephony adapters (Twilio/Exotel patterns) | Phase C voice work starts — use as reference, not as dependency |
| Additional lead sources beyond the four (JustDial, Alibaba, exhibitions) | Each is one `Excom Source` row + one mapper on the P3 spine — no new subsystem |

Nothing here blocks P1–P4, and nothing here is committed.

---

# Ordering notes and cross-phase dependencies

```
P1 UI ──────────────┐
                    ├──▶ P2 Testing ──▶ P3 Native CRM (v15) ──▶ P4 Own the spine ──▶ (P5 optional)
legacy frozen ──────┘                        │
                                             ├─ 3.1 guardrails/gateway ── makes P4 cheap
                                             ├─ 3.2/3.3 schema + wiring ── unblocks 3.10 UI
                                             └─ 3.4 intake spine ── unblocks 3.5–3.8 adapters (parallel)
```

| # | Note |
|---|---|
| N-1 | P1 ships Tasks and Notes tabs on core `ToDo` and `Comment`, which exist in v15 — so two of the four record tabs are real before any CRM work |
| N-2 | Do not let P3 write native doctype names outside the gateway. This is the single highest-leverage rule in the plan |
| N-3 | Intake (3.4–3.9) has no UI dependency. If P2 leaves capacity, pull IndiaMART forward — it is the highest-return item in the whole roadmap |
| N-4 | Phase B (AI) and Phase C (voice) from the existing roadmap slot after P3; voice needs the merged-thread surface from P1 and the record spine from P3 |
| N-5 | Instagram is described as shipped in `README.md` and `roadmap/README.md`, but there is no Instagram code in the backend. Either correct the docs in P2 or schedule the adapter — the Meta dispatch table built in 3.7 is where it would land |
