# HLD-010 — AI Setup guide (Excom Admin)

Status: plan v1, 2026-11-14. Owner asked for "a proper guided setup in excom admin ui" for everything
HLD-007/008/009 added.

## 1. What it is

A new Admin section, **AI setup** (`/admin/ai-setup`), for Excom Admins and System Managers: one page
with the steps in order. Each step shows a live status (Done, Needs attention, Not started, Optional),
what is missing in plain words, and the fix on the same page — a small form, a test button, or the exact
values to copy where the fix lives outside Excom (DNS, Meta, the voice VM). Status is always computed
from the real records, never stored, so it cannot drift.

## 2. Steps and checks

| # | Step | Done when | Fix on the page |
|---|---|---|---|
| 1 | AI provider | ai_interface has an enabled AI Provider with a key, set as default; its health is not Down | provider key + type form (creates/updates the provider); **Test connection** (AI Provider.test_connection: one tiny call, says so first); default model |
| 2 | Budgets | Excom AI Settings daily_budget_total > 0; ai_interface budget on | two number fields |
| 3 | Company basics | a postal address for every company that has a working/draft outreach agent; alert users set | postal address rows per company; alert users; quiet dates; do-not-contact domains (links to the rows editor) |
| 4 | Email sending | every email account an agent uses for Outreach has SPF, DKIM, DMARC, MX and is not blocklisted | **Check DNS** per domain (mail.check_domain) with the record that is missing and an example value; warm-up start date shown |
| 5 | WhatsApp | an active WhatsApp account; agents that write on WhatsApp have a number; Template Steward on; consent records exist | status of accounts (reuses Diagnose); steward switches; **consent import**: sample CSV + link to Data Import for Excom Consent |
| 6 | Data tools | the tools the existing agent types need are on with a key and a monthly budget | one card per tool: what it is for, rough cost, needed-by (agent types), key (write-only), budget, on/off, last error, **Test key** where the provider has a free check (Hunter account, ZeroBounce credits, Prospeo account info); others: "tested on first use" |
| 7 | Voice (optional) | voice_service_url set, key ≥ 32 chars, service health OK, at least one line with purpose Calls on a Live agent with voice_call Auto | **Make a key** (48 chars, shown once, saved); install command to copy; **Check service** (GET {url}/health?frappe=1 from the server); recording notice field |
| 8 | Agents | at least one agent; none stuck with readiness gaps | list: agent, type, status, gaps count, link to its page; "Build an agent" link |
| 9 | Go live | steps 1–6 Done (7 optional) | master switch (Excom AI Settings.enabled) with a confirm; shows what is still missing if pressed early |

## 3. Backend — `excom/excom_ai/setup_guide.py`

All endpoints: `_check_setup_access()` = Excom Admin or System Manager (added to the endpoint-guard list).

- `status()` → `{steps: [{id, title, state: done|attention|todo|optional, summary, items: [{label, ok, detail, fix?}]}], ready_to_go_live}`. Cheap: no network calls (DNS / ping / health are separate buttons).
- `save(step, values)` → writes only the fields that step owns (allow-list per step); secrets via set_password-style fields (never returned).
- `test_provider()` → AI Provider.test_connection on the default provider; returns ok/error.
- `check_dns(account)` → mail.check_domain for that account's domain + DKIM selector (from the agent channel row); stores `health` / `health_checked_on` on the channel rows that use it.
- `test_tool(key)` → `ping()` on the tool if it has one (free endpoints only), else `{tested: false, note}`.
- `make_voice_key()` → 48-char random key, saved, returned once.
- `check_voice()` → GET `{voice_service_url base}/health?frappe=1`, 5 s timeout, only the admin-set URL.
- `consent_sample()` → a CSV with the import columns (omni identity, action, channel, purpose, recorded_on, method, source).
- `go_live(confirm)` → sets enabled = 1 only when steps 1–6 are done (or `force` by System Manager).

Tool `ping()` methods (free, no credits): Hunter `GET /v2/account`, ZeroBounce `GET /v2/getcredits`,
Prospeo `GET /account-information`. Others are tested on first use (last_error shown).

## 4. Frontend

`frontend/src/components/admin/AISetupAdmin.tsx`, registered as a new Admin section (group "System",
first after Overview) and linked from the Agents home ("Finish setup" banner when not ready).
A vertical stepper; each step a collapsible panel with its checks, inline form, and buttons. Plain copy.
Phone width works (16px gutter, no horizontal scroll).

## 5. Rules

- Admin-only page and endpoints; secrets are write-only (shown as "set"/"not set").
- Test buttons say what they do and cost before running; none spends credits.
- What lives outside Excom (DNS host, Meta verification, the VM) is stated plainly with values to copy,
  and verified by a check button.

## 6. Tests

- status(): each step's done/attention logic from fixtures (no provider → step 1 todo; provider → done;
  outreach agent's company without postal address → attention; voice not set → optional).
- save(): only allow-listed fields per step; non-admin refused; secrets not echoed.
- check_dns / test_tool / check_voice with scripted network; make_voice_key length and once-only reveal.
- go_live refuses while steps are missing; sets enabled when ready.
- endpoint guards.
- frontend: a pure helper for step state → label/colour, tested with vitest.

## 7. Plan review outcome (v1 → as built)

- **The AI provider is System Manager only.** AI Provider and AI Settings serve every app; an Excom Admin
  sees their state and a Desk link. "Test connection" is a real (tiny, logged) paid call, offered to System
  Managers only, and the step also checks that the provider has an enabled model.
- **Real fields:** the default model is AI Settings.default_model (Excom AI Settings.default_model is never
  read); "budget on" is AI Settings.enable_budget plus an Excom row in its app budgets (recommended).
- **Tools:** every tool ships switched off, free ones included, so OpenStreetMap, the website reader and the
  email finder must be on. What each agent type needs comes from the packs' own lists (research.DISCOVERY,
  VERIFIERS, PEOPLE_PROVIDERS, purchase.DISCOVERY_SOURCES, compliance.SEARCH_TOOLS) as any-of / all-of
  groups. Key checks call the tool's `ping()` directly — never `tools.use()`, which would count a paid
  tool's cost against its budget — and only where the provider has a free check (Hunter account, ZeroBounce
  credits — a bad key answers 200 with Credits -1 — Prospeo account information, TrulyInbox workspaces).
- **status() makes no network call** (no Graph diagnose, no voice_ready alert): stored values only.
- **Email is checked per agent mailbox** (mail.check_channel): it also records bounces and pauses a mailbox
  whose domain is on a blocklist, which the page says. It is the way out of the readiness deadlock (the
  nightly check runs only with the master switch on). Also checked: account active, mailbox connected,
  not paused, warm-up start set (without it the agent sends nothing).
- **Voice health is SSRF-safe:** wss→https (ws/http only in developer mode), `/stream` stripped, no IP
  literals, no host resolving to private/loopback/link-local/reserved addresses, no redirects, 2 KB read.
  /health does not prove the keys match. A new key needs a confirm (AI calls stop until the VM has it) and
  is saved through the document so its validation runs.
- **Editing reuses the admin form** (DocForm) with a server-enforced field allow-list per doctype; the master
  switch, the AI identity policy and engine limits are not editable there; spend and last error are read-only.
- **Steps not needed** (no agent uses that channel or tool) say so and never block going live.
- **Added:** a Trade signals step (research only collects Confirmed signals; all are seeded Proposed).
- **Cut for v1:** consent import (Excom Consent is not importable and needs hash-named identities; a WhatsApp
  message from the person already counts as opt-in), go-live `force`, steps for MSME flags / compliance
  calendar / policies / playbooks (the agents' own readiness covers them and the Agents step shows blockers).

## 8. As built (2026-11-14)

- `excom/excom_ai/setup_guide.py`; `excom/excom/api/admin.py` (Excom AI Settings and Excom AI Tool with field
  allow-lists); `ping()` on ZeroBounce, Hunter, TrulyInbox (tools/email.py) and Prospeo (tools/people.py).
- Frontend: Admin → **AI setup** (`AISetupAdmin.tsx`, helpers in `lib/ai-setup.ts`), `DocForm` gains a `fields`
  prop, and the Agents home shows admins a banner while setup is unfinished.
- Built bundles were rebuilt in this commit (the two commits before shipped without a rebuild).
