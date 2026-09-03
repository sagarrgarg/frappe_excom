# Excom Implementation Roadmap

Phases 0-3 built the working product — WhatsApp, Email, Instagram channels, real-time inbox, Omni Identity, and basic frontend. The remaining work is deliberately minimal: ship security, add AI, then grow based on real usage.

---

## Phase Overview

| Phase | Name | Effort | Status |
|---|---|---|---|
| ~~0~~ | ~~Critical Fixes and Stabilization~~ | ~~1-2 days~~ | DONE |
| ~~1~~ | ~~Schema, Validation, Backend Hardening~~ | ~~5-8 days~~ | DONE |
| ~~2~~ | ~~Frontend Completion and Real-Time UX~~ | ~~7-10 days~~ | DONE |
| ~~3~~ | ~~Omnichannel Expansion~~ | ~~15-20 days~~ | DONE (MVP) |
| **A** | **Security Essentials** | **3-5 days** | DONE |
| **B** | **AI Intelligence Layer** | **5-8 days** | Not Started |
| **C** | **Voice Channel** | **12-18 days** | Not Started |

Remaining effort: ~20-31 days

### The current programme — P1 to P5

A/B/C above are the original feature roadmap. The programme now running is the five-phase plan in `design/PLAN_001_master_phasing.md`, which sequences the UI rewrite, the native CRM connection, and the intake endpoints. Phases B and C slot in after P3.

| Phase | Name | Effort | Status |
|---|---|---|---|
| **P1** | **UI Change** | **16-24 days** | Not Started |
| **P2** | **Excom Testing** | **8-12 days** | Not Started |
| **P3** | **Native CRM Connection (v15)** | **18-26 days** | Not Started |
| **P4** | **Owning the Spine** | **6-9 days** | Not Started |
| **P5** | **Frappe CRM Harvest** | unscheduled | Parked by design |

Committed effort P1-P4: ~48-71 days.

Two rules cut across the programme:

- **ERPNext native CRM only.** `Lead` / `Opportunity` / `Prospect` / `Customer`. Frappe CRM is not used and is uninstalled from the site at the end of P3.
- **One gateway.** Native doctype names appear only in `crm_gateway.py` and `crm_compat.py`, so owning the spine later stays a scoped exercise rather than a rewrite.

---

## Phase A: Security Essentials (3-5 days)

Ship-blocking security — the minimum you can't go live without.

- HMAC webhook signature validation (WhatsApp)
- Input sanitization on all APIs
- Basic rate limiting on send/query endpoints
- Role-based access: Agent (own threads) vs Admin (everything)
- Token expiry monitoring alerts

**No enterprise bloat.** No DLP, no audit log DocType, no supervisor roles, no session tracking. Add when needed.

---

## Phase B: AI Intelligence Layer (5-8 days)

Replace hardcoded AI stubs with real intelligence. Three features, one LLM client.

- **LLM Client:** Single function calling OpenAI-compatible API (works with OpenAI + Ollama)
- **Suggested Replies:** 3 contextual reply suggestions per conversation (replaces hardcoded stubs)
- **Conversation Summary:** Auto-generated on thread close, on-demand refresh
- **Contact Profiling:** Behavioral summary from conversation history + ERP context

**No over-abstraction.** No provider factory, no sentiment analysis, no auto-translation, no AI routing. Add when agents ask for it.

---

## Phase C: Voice Channel (12-18 days)

Calls as a first-class channel, in the same threads as WhatsApp and Email.

- **Excom is the brain, the provider is a dumb pipe** — it supplies numbers and executes instructions
- **Provider-neutral** — UI says "Calls"; vendor name lives in one Select field and one adapter file
- **Teams ring, not agent lists** — reuses existing `allowed_teams` + core `User.mobile_no`
- **Sticky-then-team routing** — last rep who handled this Omni Identity, else the whole team in parallel
- **One new doctype** (`Excom Call`); everything else is fields on existing doctypes

**No enterprise bloat.** No call queues, no dialler, no QA scoring, no barge-in, no skills routing. Add when asked.

---

## Philosophy: Add Based on Need

These features exist in the old detailed specs but are **intentionally deferred**. Build them only when real usage demands it:

| Feature | Build When... |
|---|---|
| Teams + Assignment Engine | You have 3+ agents and need workload distribution |
| Pipeline / Kanban | Sales team needs visual funnel tracking |
| CRM Sync | You're actively using Frappe CRM alongside Excom |
| Routing Rules + SLA | You have multiple departments with different response targets |
| Deep ERPNext Integration | Agents frequently context-switch to ERPNext for invoice/ticket info |
| Analytics Dashboard | Management needs operational metrics |
| Sentiment Analysis | You want AI to flag frustrated customers |
| Auto-Translation | You serve customers in multiple languages |
| CSAT Surveys | You need customer satisfaction measurement |
| Audit Logging | Compliance requires immutable event trails |

---

## Files

- `phase_A_security_essentials.md` — Webhook HMAC, sanitization, rate limits, RBAC, token monitoring
- `phase_B_ai_layer.md` — LLM client, suggested replies, conversation summary, contact profiling
- `phase_C_voice_channel.md` — Voice channel, provider abstraction, routing engine, call UI

### Current programme (P1-P5)

- `phase_P1_ui_change.md` — Tokens, router, responsive shell, merged thread + Reply via, tier pass, admin pages, mobile parity
- `phase_P2_excom_testing.md` — Pilot, functional sweep, responsive/DPI matrix, load, security, data integrity, the flip
- `phase_P3_native_crm_v15.md` — Gateway, N1 schema, N2 wiring, intake spine + IndiaMART/Website/Meta/TradeIndia, CRM UI, SLA
- `phase_P4_owning_the_spine.md` — v16 dry run, gateway contract tests, manifest enforcement, fork rehearsal and costing
- `phase_P5_frappe_crm_harvest.md` — Standing register of Frappe CRM designs worth building, with triggers and the licence boundary

### Design documents

- `design/PLAN_001_master_phasing.md` — How P1-P5 fit together, gates and cross-phase dependencies
- `design/HLD_003_native_crm_comms_flow.md` — Native CRM + communications data model, intake spine, six pipelines
- `design/UX_001_ui_redesign_plan.md` — UI design language, layout system, disclosure tiers, parallel testing
- `design/RES_001_native_crm_lock_and_intake.md` — Native-only verification, v16 impact, Frappe CRM harvest, intake endpoint specs
- `design/HLD_unified_platform_v2.md`, `design/LLD_unified_platform_v2.md` — Wider platform context
- `design/HLD_field_sales_platform.md`, `design/LLD_field_sales_platform.md` — Field sales / fieldforce context

---

## Reference Documents

- `technical_handbook.md` — Architecture decisions and implementation log
- `psychological_handbook.md` — Design principles and anti-patterns
- `whatsapp_handbook.md` — WhatsApp API and integration guide
