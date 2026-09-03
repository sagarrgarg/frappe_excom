# Phase P5 — Frappe CRM Harvest (separate app, unscheduled)

**Phase:** P5 of 5 (`PLAN_001_master_phasing.md`)
**Design source:** `design/RES_001_native_crm_lock_and_intake.md` §5
**Duration:** unscheduled — each candidate is independently sized and independently triggered
**Depends on:** P3 complete (Frappe CRM uninstalled from the site)
**Status:** Parked by design

---

## 1. Objective

Keep a standing, evidence-based list of the things Frappe CRM does well, each with the **trigger** that would justify building our own version — so that we benefit from their thinking without taking on their data model, their app, or their licence.

**This phase is deliberately not on the critical path.** Nothing here blocks P1–P4, and nothing here is committed work.

---

## 2. Two boundaries, decided once

### 2.1 Licence

`apps/crm/LICENSE` is **GNU AGPL-3.0**. `apps/excom/license.txt` is **MIT**. Copying code across is a licence conflict, not a style preference.

| Allowed | Not allowed |
|---|---|
| Reading their code to understand a design | Copy-pasting functions, doctype JSON, or Vue/JS components |
| Reproducing a field model we independently write | Vendoring `crm/` modules into excom |
| Citing file paths in our design docs (as RES-001 does) | Importing from `crm.*` at runtime |

If we ever want to run their actual code, it goes in a **separate app** with its own AGPL licence, talking to excom through the gateway from P4 — which is one of the reasons that gateway exists.

Add this rule to `CLAUDE.md` / `AGENTS.md` so it survives contributor turnover.

### 2.2 Installation

Frappe CRM stays **uninstalled** from the site after P3 (RES-001 D1). While it was installed it reached into shared data three ways — a site-wide `Communication.after_insert` hook that can create CRM Leads and rewrite `Communication.reference_doctype/name`, site-wide `ToDo` hooks, and an ERPNext-side bridge that writes `crm_deal` / `crm_product_code` custom fields onto `Quotation`, `Customer` and `Item`. The repo stays on the bench for reference reading only.

---

## 3. Candidate register

Each candidate: what it is, where to read it, what we would build, and the trigger.

### C1 — SLA engine with working hours

**Theirs:** `CRM Service Level Agreement` — `apply_on`, `condition` + `condition_json`, `priorities[]`, `working_hours[]`, `holiday_list`, `start_date`/`end_date`, `rolling_responses`; plus `CRM Rolling Response Time`.
**Ours today:** P3 §3.11 ships `tasks/crm_sla.py` with elapsed-time targets per source and a simple holiday list.
**Would build:** `Excom SLA` with per-priority response/resolution targets, working-hours calendars per team, pause-on-waiting-for-customer, and rolling response measurement.
**Trigger:** the simple version starts producing false breaches out of hours, or a second source needs different targets per priority rather than per source.
**Size:** 4–6 d.

### C2 — Stage-duration history

**Theirs:** `CRM Status Change Log` — `from`, `from_date`, `to`, `to_date`, `duration`, `log_owner`, `from_type`, `to_type`.
**Ours today:** P3 §3.3 already adds `Excom Stage Change Log` on this model, so **this one is harvested up front** rather than deferred — it is listed here for provenance.
**Trigger:** none — done in P3.

### C3 — Configurable layouts and saved views as data

**Theirs:** `CRM Fields Layout`, `CRM View Settings` (with a `clear_old_versions` housekeeping job), `CRM Form Script`.
**Ours today:** `get_field_schema` drives the Details tab (P3 §3.10); saved views are code-defined (P1 W5).
**Would build:** per-user and per-team saved views stored as documents, and layout overrides per `customer_type` editable without a release.
**Trigger:** users request per-team layouts or custom views faster than releases can ship them — roughly, the third such request in a month.
**Size:** 3–5 d.

### C4 — Lead source framework generalisation

**Theirs:** `Lead Sync Source` + `Failed Lead Sync Log` + frequency-bucketed scheduler jobs (`Every 5/10/15 Minutes`, Hourly, Daily, Monthly) + `last_synced_at` watermark + retry-from-log.
**Ours today:** P3 §3.4 implements this shape as `Excom Intake Source` + `Excom Intake Log`, so the pattern is already harvested.
**Would build:** additional adapters only — JustDial, Alibaba, exhibition CSV import, Google Lead Form extensions.
**Trigger:** the business signs up to a new lead channel. Each is one source row plus one mapper — **no new subsystem**, roughly 1–2 d each.

### C5 — Domain enrichment

**Theirs:** `crm/domain_enrichment/` — crawler, extractors, mapper, pipeline, seeded rules, cross-record enrichment; enriches company data from an email domain.
**Would build:** on inbound lead creation, look up the sender's domain and pre-fill `website`, `industry`, `no_of_employees`, `annual_revenue`, with a confidence flag and manual override.
**Trigger:** reps spend visible time manually researching inbound leads; or lead volume passes the point where classification (HLD-003 S4) becomes the bottleneck.
**Size:** 5–8 d. Depends on Phase B (AI layer) being useful first — extraction quality is the whole value.

### C6 — Telephony adapters

**Theirs:** `CRM Call Log`, `CRM Telephony Agent`, `CRM Twilio Settings`, `CRM Exotel Settings`, and guest webhook handlers under `crm/integrations/` (note their guest endpoints carry `# nosemgrep: guest-whitelisted-method` — even upstream treats these as deliberate exceptions).
**Ours:** `roadmap/phase_C_voice_channel.md` already specifies a provider-neutral `Excom Call` with sticky-then-team routing.
**Use:** reference implementation for provider callback validation and call-state modelling while building Phase C. Not a dependency.
**Trigger:** Phase C starts.

### C7 — Deliberately skipped

| Theirs | Why we skip |
|---|---|
| `CRM Notification` | `Excom Notification` exists |
| `CRM Invitation` | Frappe user invitation is sufficient |
| `CRM Sales Hierarchy` | ERPNext `Sales Person` tree is the native hierarchy and doubles as commission structure |
| `CRM Dashboard` | `AnalyticsPage` + native ERPNext reports |
| `CRM Deal` / `CRM Lead` data model | The entire point of HLD-003 is not having a second CRM |
| `CRM Products` / product sync | Native `Item` is already the product master |
| Their web-form module | Rejected in RES-001 §7.2 A — our enquiries come from several external sites, so we POST to a headless endpoint instead |

---

## 4. Two structural lessons already adopted

Worth recording because they shaped P3 rather than waiting for P5:

1. **Every external source is a row in one doctype with a frequency and a watermark** — not a bespoke integration per vendor.
2. **Every failed inbound lead becomes a durable log row with the raw payload and a one-click replay** — never a swallowed exception.

Two weaknesses in their implementation that we deliberately did **not** inherit: `limit: 100000  # TODO: pagination` in the Facebook fetch, and poll-only lead sync with no webhook (which cannot meet a 5-minute first-response SLA).

---

## 5. Review cadence

| When | Action |
|---|---|
| Each quarter | Re-read the register; move any candidate whose trigger has fired into the next planning cycle |
| Each Frappe CRM major release | Skim their changelog for new patterns worth adding to the register — reading only |
| Whenever a candidate is built | Record in the register what we built, how it differs, and that it was written from scratch |

---

## 6. Exit condition

There is none. P5 is a standing register, not a phase with a finish line. It is complete in the sense that it is written down, triggered rather than scheduled, and bounded by the licence rule in §2.1.
