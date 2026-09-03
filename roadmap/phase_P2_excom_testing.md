# Phase P2 — Excom Testing

**Phase:** P2 of 5 (`PLAN_001_master_phasing.md`)
**Duration:** 8–12 days (T3/T5 may start during P1's tail)
**Depends on:** P1 feature-complete behind the flag
**Blocks:** P3 — CRM work must land on a proven base, not on an untested rewrite
**Status (2026-09-03, evening):** legacy tree deleted (E7 done), automated tests added (`excom/excom/tests/test_core_flows.py`, 12 cases; `frontend/src/lib/__tests__`, 6 cases), Frappe CRM migration rehearsed (`roadmap/frappe_crm_migration.md`). Still pending real users: T1 pilot, reconnect/packet-loss, live vendor intake.

---

## 1. Objective

Prove the **whole application** — the new UI *and* the channel, broadcast, identity and realtime machinery underneath it — under real use by real users, then flip the default and delete the legacy tree.

**Definition of done:** zero open P0/P1 defects, pilot opt-out under 10 % with every opt-out reason resolved or explicitly accepted, the functional sweep passing 100 %, and the new UI serving as default with legacy removed.

---

## 2. Why a dedicated phase

P1 rewrites the presentation layer of an app whose backend has never had an end-to-end functional sweep written down. Two risks converge: new-UI regressions, and pre-existing backend behaviour nobody has exercised deliberately (the delivery watchdog, session-window expiry, webhook replay, merge/unmerge). Testing them together, once, is cheaper than discovering them during CRM work when the blast radius includes live leads.

---

## 3. Test tracks

### T1 — Pilot (runs the whole phase)

| Week | Group | Task | Signal |
|---|---|---|---|
| 1 | 2 sales reps | One full working day on `?ui=next` | Time-to-first-reply, missed conversations, count of "where is X?" |
| 1 | 1 accounts + 1 compliance | Same, with their lens | Whether the lens strip carries their whole job |
| 2 | The 4 above + 2 managers | A full week, all work in the new UI | Sustained use, admin-page usability |

Instrumentation is deliberately cheap and self-hosted: a click log keyed by control id (feeds the tier rule — any T1 control used in fewer than one in three sessions moves to T3), plus route + viewport + DPR captured automatically on every feedback submission. No third-party analytics.

**Rule:** a pilot report of "I couldn't find X" is a **tier misassignment**, fixed by moving the control — never by adding a tooltip.

### T2 — Functional sweep (the checklist that outlives this phase)

Written once as `roadmap/test_checklist.md`, re-run every phase thereafter.

| Area | Cases |
|---|---|
| WhatsApp | Inbound text/image/video/audio/document/sticker/location/reaction/button/interactive/flow reply; outbound text + media; template send; **24 h session expiry → template fallback**; delivery receipts (sent/delivered/read/failed); the 10-minute `DeliveryTimer`; media download path; reply-to threading |
| Email | Gmail poll → thread; send with subject/cc/bcc; signature; attachment; HTML body rendering; reply threading; bounce handling |
| Web chat | Config fetch, session create, visitor message, agent reply, poll, session end, identity capture |
| Identity | Resolution ladder — phone, alias, `channel_user_id`, email, email alias, ERPNext Contact reverse lookup, new-identity creation; merge suggestion; merge; unmerge; thread re-parenting; `is_spam` suppression |
| Threads | Assignment, team transfer + `Excom Thread Transfer Log`, priority, status transitions, unread counters, tags, pinned, internal notes |
| Broadcasts | Subscriber list build, subscriber rules on doc events, schedule, send fan-out, per-recipient log, metrics, unsubscribe link, opt-out honoured on next send |
| Notifications | `Excom Notification` triggers (all/hourly/daily/weekly/monthly), push tokens, delivery watchdog on stale messages |
| Teams/Settings | Team CRUD, membership, account access gating, canned responses, stickers, templates sync, branding |
| UI parity | The 42-control checklist from P1, exercised on desktop **and** phone |

### T3 — Responsive and low-DPI sweep

Full matrix per release candidate:

| Width | Class | DPR | Must hold |
|---|---|---|---|
| 360 / 390 / 414 | phones | 2–3 | 44 px targets, safe-area insets, composer above keyboard |
| 640 | boundary | 2 | Clean layout switch, no flash |
| 768 / 834 | tablets | 2 | Rail + one column, details as sheet |
| 1024 | small laptop | 1 | Three columns fit |
| **1366×768** | **reference** | **1** | **Record pane ≥990 px, message area ≥400 px, all 13 px text crisp** |
| 1440 / 1920 | desktop | 1–2 | Details persistent; content max-width caps |

Plus Windows scaling 100/125/150 %, browser zoom 90/100/110 %, `prefers-reduced-motion`, and the seven low-DPI checks (12 px floor, no weight 300, 1 px solid borders, integer-grid icons, pre-checked tint/text pairs, shadow cap, ≥2 px chart strokes).

### T4 — Load and realtime

- 2 000-message thread: scroll performance, memory, initial paint.
- 50 concurrent threads in the list with realtime updates arriving.
- Socket.io disconnect/reconnect (kill the connection mid-session; verify no duplicate or lost messages).
- Optimistic send under 30 % packet loss and on 3G throttling.
- Mobile background → foreground resume; PWA offline behaviour.
- Baseline comparison against legacy for thread-open latency — **no regression allowed**.

### T5 — Security pass

| # | Check |
|---|---|
| S1 | Rate limits: today only **4 of 136** whitelisted functions are rate-limited, all keyed by user. Audit every guest-reachable endpoint and add IP-keyed limits |
| S2 | Webhook HMAC: valid signature accepted; invalid rejected; **the accept-on-missing-signature path** (`utils/webhook.py:43`) documented and ticketed for P3 §3.9 |
| S3 | Webhook replay: same `provider_message_id` twice → one message (idempotency already in `thread_service.ingest_inbound_message`) |
| S4 | Permissions: agent sees own threads only; admin sees all; account access gating on send; no `ignore_permissions` leakage in API paths |
| S5 | Guest endpoints: webchat session token cannot read another session; unsubscribe link is signed and single-purpose |
| S6 | Token expiry: `tasks/token_monitor.py` alerts fire before WhatsApp/Gmail credentials lapse |
| S7 | Input sanitisation on message content and template variables (stored XSS in the message feed) |

### T6 — Data integrity

Identity merge/unmerge round-trip with no orphaned messages; thread re-pointing preserves history; broadcast logs reconcile with messages sent; no duplicate `Excom Message` under webhook retry; counters (`unread_count`, `last_*_at`) agree with the underlying rows after each scenario.

---

## 4. Defect process

| Severity | Definition | SLA |
|---|---|---|
| **P0** | Blocks the pilot: data loss, message not delivered, cannot reply, security hole | Same day |
| **P1** | Blocks the default flip: parity gap, layout breakage at a matrix width, workflow that needs the legacy UI | Before the exit gate |
| **P2** | Post-flip backlog: polish, rare edge case | Logged, scheduled |

Every P0/P1 fix ships with either a regression test or a new line on the T2 checklist. No fix-and-forget.

---

## 5. The flip

1. Default flag flips to `next` for all users; opt-out link stays visible for **two weeks**.
2. Every opt-out submits a reason (free text, one line) — reviewed daily.
3. After two weeks with opt-out under 10 % and no P0/P1: delete `ChannelTabsView.tsx`, the old `LeftSidebar.tsx`, `ChatThreadList.tsx`, `components/mobile/`, and the flag machinery.
4. Bundle size measured before and after — **must not grow** despite the added surfaces.

---

## 6. Exit gates

| # | Gate |
|---|---|
| E1 | Zero open P0/P1 |
| E2 | T2 functional sweep 100 % pass, checklist committed to the repo |
| E3 | T3 matrix clean, screenshots archived per width/DPR |
| E4 | T4 shows no regression vs legacy on thread-open latency; no message loss under reconnect |
| E5 | T5 findings either fixed or ticketed with an owner and a phase (HMAC hardening explicitly ticketed to P3 §3.9) |
| E6 | Pilot opt-out < 10 %, every reason resolved or accepted in writing |
| E7 | Default flipped; legacy tree deleted; `grep -r "components/mobile"` returns nothing |
| E8 | Documentation corrected — including the Instagram claim in `README.md` and `roadmap/README.md`, which describe a shipped channel for which **no backend code exists** |

---

## 7. Risks

| # | Risk | Mitigation |
|---|---|---|
| R1 | Pilot users revert to legacy at the first friction and stop reporting | Opt-out requires a one-line reason; daily 10-minute standup with the pilot group in week 1 |
| R2 | Backend defects surface that predate P1 and get blamed on the UI | T2 runs the same checklist against legacy first, establishing a baseline |
| R3 | Testing slides because "it looks fine" | Exit gates are checklist-based, not opinion-based; the checklist is a committed file |
| R4 | Deleting legacy too early removes the escape hatch | Two-week opt-out window is mandatory before deletion |
| R5 | Load testing needs production-like data | Use a restored copy of the site backup, scrubbed — do not test against production |

---

## 8. Deliverables

- `roadmap/test_checklist.md` — the living T2 sweep.
- Screenshot archive per matrix width and DPR.
- Click-log summary feeding the P1 tier rules.
- Security findings list with owners and target phases.
- Corrected README channel claims.
- Legacy tree deleted; flag machinery removed.

---

## 9. Effort

| Track | Days |
|---|---|
| T2 sweep authoring + first run | 3–4 |
| T1 pilot (calendar, overlaps everything) | 8–10 elapsed, ~2 of effort |
| T3 responsive/DPI | 1–2 |
| T4 load/realtime | 1–2 |
| T5 security | 1–2 |
| T6 data integrity | 1 |
| Defect burn-down + flip | 2–3 |
| **Total** | **8–12** |
