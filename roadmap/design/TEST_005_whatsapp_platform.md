# HLD-005 — What Still Needs Testing (living document)

Every phase of HLD-005 adds rows here. A row stays until it has a result.

**Results:** ⏳ not run yet · ✅ pass · ❌ fail (fixed in commit …) · ⏭ skipped (reason)

**How it was tested:**
- **U** — automated test (`excom.excom.tests.test_whatsapp_platform`)
- **M** — migrate / patch on a site
- **L** — live with a real WhatsApp Business Account (test number)
- **D** — diagnostics screen (Admin → WhatsApp → Diagnose)

**Blocked right now:** MariaDB is stopped on the dev machine, so nothing below has run. Phase 0 was committed untested at the owner's request (2026-11-03).

---

## 0. How to run

```bash
# start services (needs sudo for MariaDB)
sudo systemctl start mariadb
cd ~/frappe-bench
redis-server config/redis_cache.conf --daemonize yes
redis-server config/redis_queue.conf --daemonize yes

# schema + patch
bench --site frappe.localhost migrate

# Phase 0 tests only
bench --site frappe.localhost execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom.tests.test_whatsapp_platform'}"

# full excom suite (regressions)
bench --site frappe.localhost execute excom.excom.tests.run.run
```

---

## Phase 0 — platform deadlines (commit: "WhatsApp platform deadlines (HLD-005 Phase 0)")

### Migrate and patch

| # | Check | How | Expected | Result |
|---|---|---|---|---|
| 0.1 | `bench migrate` applies the new fields and doctypes (Excom Consent, Excom WhatsApp Event) | M | no errors | ⏳ |
| 0.2 | Patch `upgrade_graph_api_version` | M | every stored `wa_version` / `meta_api_version` / `api_version` below v24 becomes v26.0; v24+ left alone | ⏳ |
| 0.3 | Channel Account form | M | new "WhatsApp Platform" section; Share Activity is blank / On / Off | ⏳ |
| 0.4 | Excom Consent permissions | M | an agent can read but not create; nobody can edit; delete is refused | ⏳ |

### Automated tests (31)

Version, errors and routing:

| # | Test | Result |
|---|---|---|
| 0.5 | graph version floor; calls use v26 even with v21 stored | ⏳ |
| 0.6 | error classes; rate-limit error is still an ExcomRateLimitError and keeps retry_after; 135000 never pauses a campaign | ⏳ |
| 0.7 | marketing goes to /marketing_messages when enabled (product policy; activity sharing only when chosen); utility always on /messages | ⏳ |
| 0.8 | 131063 retries once on /marketing_messages and remembers the category | ⏳ |
| 0.9 | held_for_quality_assessment is recorded on the message | ⏳ |

Consent gate:

| # | Test | Result |
|---|---|---|
| 0.10 | marketing opt-out blocks marketing only; STOP blocks every template; unknown category is gated as marketing | ⏳ |
| 0.11 | the gate sees an opt-out on a duplicate record; a merge keeps the opt-out and writes a ledger row | ⏳ |
| 0.12 | a business-scoped user id is sent as `recipient` | ⏳ |

Statuses and consent webhooks:

| # | Test | Result |
|---|---|---|
| 0.13 | status batch: every entry applied; never goes backwards; pricing stored | ⏳ |
| 0.14 | 131050 failure → marketing opt-out + ledger row | ⏳ |
| 0.15 | user_preferences stop → resume (scoped to the account) | ⏳ |
| 0.16 | unsigned payload cannot change consent | ⏳ |
| 0.17 | "Stop promotions" button stops marketing only; START only undoes the person's own STOP | ⏳ |

Identity:

| # | Test | Result |
|---|---|---|
| 0.18 | username-only sender gets an identity; the same user id with a number joins it | ⏳ |
| 0.19 | user_changed_number relinks; the old number is history only | ⏳ |
| 0.20 | recycled number → new identity, old record releases the number, both flagged | ⏳ |

Account and template events:

| # | Test | Result |
|---|---|---|
| 0.21 | template_category_update (done) changes routing; advance notice does not | ⏳ |
| 0.22 | number quality / capability / MM_LITE_TERMS_SIGNED update the account; account_alerts reads `alert_info` | ⏳ |

Ledger, broadcasts and analytics:

| # | Test | Result |
|---|---|---|
| 0.23 | consent ledger is append-only | ⏳ |
| 0.24 | broadcast: opted-out person skipped, campaign pauses on 132015, resume sends nobody twice | ⏳ |
| 0.25 | analytics overview no longer asks for conversation_analytics | ⏳ |
| 0.26 | **full excom suite still passes** (send_transactional, auto-ack, watchdog, subscribers, identity naming…) | ⏳ |

### Live with Meta (test WABA + test number)

Sending and webhooks:

| # | Check | How | Expected | Result |
|---|---|---|---|---|
| 0.27 | Send a utility template | L | delivered; Excom Message gets `wa_pricing_category=utility` from the status webhook | ⏳ |
| 0.28 | Accept the Marketing Messages terms → webhook `account_update MM_LITE_TERMS_SIGNED` | L | "Marketing Messages Terms Signed" ticked; diagnostics shows ONBOARDED | ⏳ |
| 0.29 | Send a marketing template with "Send Marketing via Marketing Messages API" on | L | goes to /marketing_messages; status pricing category `marketing_lite` | ⏳ |
| 0.30 | Stop promotions in the WhatsApp app (test phone) | L | `user_preferences` stop → Stopped WhatsApp Marketing ticked; next marketing send refused before Meta; a utility send still goes | ⏳ |
| 0.31 | Resume promotions | L | opt-out lifted; ledger has Withdrawn then Granted | ⏳ |
| 0.32 | Reply STOP, then START | L | WhatsApp opt-out set, then cleared | ⏳ |
| 0.33 | Template quick-reply "Stop promotions" button tapped | L | marketing opt-out from `template_optout_button@<waba>` | ⏳ |

Identity:

| # | Check | How | Expected | Result |
|---|---|---|---|---|
| 0.34 | A contact with a WhatsApp username (no number shared) messages in | L | identity created with WhatsApp User ID and username; the agent can reply (sent with `recipient`) | ⏳ |
| 0.35 | Test phone changes number (if possible) | L | user_changed_number relinks; old number kept as history | ⏳ |

Diagnostics:

| # | Check | How | Expected | Result |
|---|---|---|---|---|
| 0.36 | Diagnose: Graph version, app secret, app subscribed, webhook fields (and their version), billing currency, Marketing Messages, health, quality | D | each check green, or red with the next step | ⏳ |
| 0.37 | Webhook field versions ≥ v24 in App Dashboard | D | no "payload version" warning | ⏳ |
| 0.38 | Analytics page | L | conversation card shows pricing-based counts and cost; no Meta error | ⏳ |

### Outside the code (deadlines)

| # | Item | Deadline | Result |
|---|---|---|---|
| 0.39 | WhatsApp billing in INR (migration creates a new WABA id and new template ids: update Business Account ID, re-subscribe the app, run template sync) | 2026-12-31 | ⏳ |
| 0.40 | Graph v21 retired — patch run on every site | 2027-01-21 | ⏳ |
| 0.41 | Business verification (messaging limit 250 → 2,000) | before the dealer list passes 250 | ⏳ |
| 0.42 | Marketing on /messages retired — every account switched to the Marketing Messages API | 2027-10-13 (2027-04-13 for new accounts) | ⏳ |
| 0.43 | DPDP consent: consent rows imported for existing dealers before the first broadcast | ~2027-05-13 | ⏳ |
