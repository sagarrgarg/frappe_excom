# HLD-008: The Template Steward

**Status:** plan v2, 2026-11-10. It builds on HLD-007 (Excom AI agents). A planning agent reviewed v1 against the code and Meta's rules; §9 lists what changed and why.

## 1. What it is

The Template Steward is a background service, not an agent. It finds what the agents (Sales, Accounts, Purchase, Compliance, Custom) need to send again and again, and makes templates for it:
- **WhatsApp:** each template is submitted to Meta on its own, with no internal approval; Meta's review is the approval (the owner's decision, 2026-11-10). It is limited by the code checks in §5, a daily cap, and stops on rejections and poor quality.
- **Email:** each template is approved by a person in Needs you.

Agents then send from approved templates; code fills the variables from the records. That is the only way an agent may write on WhatsApp outside the 24-hour window.

**What exists and is reused:**
- `WhatsApp Templates`: inserting a row submits it to Meta; the status webhook keeps `status` and `category` current.
- Meta's template library: `whatsapp_template_library.create` makes ready-written UTILITY templates that are approved at once.
- `send_template_to_thread`, with the agent send rules.
- The consent ledger: `Excom Consent` and `latest_consent`.

## 2. Needs (v1)

A need is "an agent wants to send this kind of message, on this channel, in this language, from this WhatsApp business account (WABA) or mailbox". Needs come only from the packs' declarations, and they are recorded when an agent prepares a message and finds no template. That happens at writing time, inside a task that completes normally, so a later rollback never loses it.

| Key | Channel | Category | Library use cases tried first | Variables (code fills them) |
|---|---|---|---|---|
| `accounts.payment_reminder` | WhatsApp, Email | UTILITY | PAYMENT_OVERDUE, PAYMENT_DUE_REMINDER | contact name, total due, invoice numbers |
| `accounts.statement_ready` | WhatsApp | UTILITY | STATEMENT_AVAILABLE | contact name, as-of date |
| `purchase.po_chase` | WhatsApp, Email | UTILITY | none | supplier, PO number, due date |
| `purchase.rfq_reminder` | WhatsApp, Email | UTILITY | none | supplier, RFQ number, reply-by date |
| `sales.order_update` | WhatsApp | UTILITY | ORDER_CONFIRMATION, SHIPMENT_CONFIRMATION, DELIVERY_UPDATE | buyer name, order number, status |
| `sales.quote_follow_up` | WhatsApp | UTILITY, informational only ("Quotation {{1}} is valid until {{2}}") | none | quotation number, valid until |

**Left out of v1:**
- **Marketing (cold Sales follow-ups):** nobody has a WhatsApp marketing opt-in recorded, and HLD-007 rules out cold WhatsApp.
- **Voice callback:** "X will call you back at …" implies a person and makes a promise.
- **Compliance and Custom:** they only write ToDos for people.
- **Email templates mined from repeated writing:** a later step.

Each pack declares its needs with `templates.declare(...)`, so a new pack adds its own.

## 3. Records

**Excom AI Template Need** (new) holds:
- the key, channel, category, language (Meta code), channel account and WABA, and the agents that need it;
- source (Library / Written), status, attempts, last error;
- the `WhatsApp Templates` link; for email, the subject, body and variable map;
- samples, a back-translation, and the review proposal;
- a unique key `key|channel|WABA-or-mailbox|language`, so the same need is never recorded twice, even by two workers at once.

Statuses:

| Status | Meaning |
|---|---|
| Open | waiting to be drafted |
| Drafting | claimed by one run |
| Submitted | WhatsApp, waiting for Meta |
| In review | email, waiting for a person |
| Met | the template is approved and usable |
| Failed | the reason is on the record |
| Withdrawn | a person took it back |

**WhatsApp Templates** gets four fields, so no second copy of its status exists: `xai_need`, `xai_variable_map`, `xai_back_translation` and `xai_first_use_done`.

**Settings** (Excom AI Settings):

| Setting | Default |
|---|---|
| `templates_enabled` | on |
| `whatsapp_auto_submit` | on |
| `template_submissions_per_day` | 10 |
| `template_max_attempts` | 2 |
| `template_first_use_review` | on |

## 4. The run (hourly, in monitor.hourly)

Nothing runs while the master switch or `templates_enabled` is off.

1. **Statuses.** For each submitted need, read `WhatsApp Templates.status` and `category`, compared without regard to case.
   - **APPROVED in the declared category:** the need is Met, and the owners get a notice with a Withdraw button.
   - **APPROVED in another category:** not met (Meta moved it, e.g. utility to marketing). It is recorded and never used.
   - **REJECTED:** reworked by editing the same Meta template (same id, no new slot), up to `template_max_attempts`. After that the need is Failed and the owners get one alert.
   - **PAUSED, FLAGGED, DISABLED or PENDING_DELETION:** recorded; a template that is not APPROVED is never used.
   - **Pending for more than 24 hours:** polled from Meta (`GET /{id}?fields=status,category,rejected_reason,quality_score`), in case the webhook is not subscribed.
2. **Drafting.** Open needs, oldest first, up to the daily cap.
   - Each need is claimed with a conditional update (Open → Drafting).
   - It is queued as `template_draft` on a Live agent that needs it and may work now. Shadow, On Hold and Retired agents never cause anything to go to Meta; with no such agent the need waits.
3. **Stops per WABA.** Auto-submission pauses for a WABA when:
   - it had 3 rejections in 7 days;
   - its number's or a template's quality is YELLOW or RED;
   - it holds 200 templates or more (80% of Meta's 250 for an unverified business; each language counts).
   The owners are alerted once.

## 5. Drafting a WhatsApp template (`template_draft`)

1. **Library first.** Search Meta's library by the need's use cases and language. When an entry fits, the model maps its `{{n}}` to the declared variables, and an entry with a variable we cannot fill is skipped. It is then created through `whatsapp_template_library.create`, and Meta approves it at once.
2. **Otherwise, written.** The model writes the body in the need's language, using only the declared variables, plus made-up samples and an English back-translation. Code checks:
   - variables numbered in order from 1, none at the very start or end, none side by side; at most one variable per three words (our own guard; Meta publishes no ratio);
   - at most 1024 characters, with nothing but the declared variables;
   - one non-empty sample per variable, matching no real customer, supplier, contact, invoice or order;
   - no prices, discounts, delivery promises, threats, links, or claims about being a person or an AI;
   - UTILITY bodies state a transaction fact, with no promotional words;
   - a second model check in the target language, returning the back-translation: the English word lists do not cover other languages.
   - Examples and inbound text in the prompt are fenced as untrusted.
3. **Submit:**
   - name `xai_<key>_<lang>_<last 6 digits of WABA>` (lowercase letters, digits and `_` only);
   - `whatsapp_account` set to the agent's own number;
   - the declared category (never the model's).
   - Before posting, if a template of that name already exists on Meta (an earlier try), it is adopted instead.
   - The row is committed right after the insert.
   - A Meta error is caught and written on the need. It never fails the task, so no retry loop posts again and the agent's failure count does not rise.
4. **Email:** the model writes the subject and the pack's words (opening and closing), and the same code checks run. The template goes to an `approve_template` proposal under Review (policy `template`, ceiling Review), never in bulk approval. The executor re-checks the edited text before marking the need Met.

## 6. Using templates

**Lookup.** `templates.find(agent, key, channel, language, account)` returns an approved template with its variable map, or None (and records the need). Languages fall back: exact, then base (`es_MX` → `es`), then the company's.
- **WhatsApp:** usable only when APPROVED, in the declared category, and linked to the account.
- **Fallback:** the packs keep today's lookups (Accounts' hand-made reminder templates) when `find` returns None.

**Sending.** `templates.whatsapp_values(template, record)` fills the variables:
- each value on one line, whitespace collapsed, at most 200 characters;
- the variable map's fallback when a value is empty;
- the filled text at most 1024 characters.

**Send rules** (`send_rules.check`, for every agent route, including today's Accounts path) for an agent's template send:
- the template is APPROVED and linked to the agent's number;
- **opt-in:** the person has a recorded WhatsApp "Granted" in the consent ledger (any purpose), or has written to us on WhatsApp before.

**First use.** The first send of each new template version goes to Review whatever the action's policy. Needs you shows the text, the back-translation and Meta's category. Once a person approves one send, that version follows the policy (`template_first_use_review` can turn this off).

**Packs:**
- **Accounts:** the WhatsApp reminder and the statement notice; the email reminder's opening and closing come from an approved email template instead of the model.
- **Purchase:** the PO chaser and RFQ reminder on WhatsApp when the supplier has a number and no email (or the email went unanswered); the email words from approved templates.
- **Sales:** the order update and the quotation-validity notice on WhatsApp outside the window, under the pack's existing actions (no new `send_whatsapp` policy).

## 7. Safety, summed up

- The steward never sends; every send is an agent action under its policy, its send rules and first-use review.
- Auto-submission is limited to the UTILITY needs declared in code, and to the code and model checks, the daily cap, and the per-WABA stops. It can be switched off.
- A notice for each submission tells the owners, with Withdraw, which deletes the template on Meta.
- New doctype permissions match the other Excom AI doctypes: an agent's team sees its needs. Rows are written with system rights, by the steward only.

## 8. Tests (frappe.localhost, scripted model, Meta mocked)

**Needs**
1. A blocked Accounts WhatsApp reminder records one need; a second agent is added to it; two numbers on one WABA give one need; two WABAs give two needs and two names.
2. Recording survives: a need recorded at writing time is kept when the later send is cancelled.

**Drafting and submission**
3. Library path: a library entry is created through the library helper (Meta mocked) and the need is Met at once.
4. Written path, good: a valid body passes the checks and is inserted with the right name, account, category and samples.
5. Written path, bad, each with one rewrite: a price, a variable at the start, variables side by side, an undeclared variable, promotional words in UTILITY, a sample that is a real customer, a body over 1024 characters.
6. Meta refusal or a missing WABA id: the error is on the need, the task is Done, nothing is retried, and the agent's failure count does not rise. The orphan case: a template already on Meta is adopted.

**Safeguards**
7. Shadow or On Hold agents: nothing goes to Meta; the need waits or moves to a Live agent. A race between two claims gives one submission. The daily cap holds.

**The hourly run**
8. Statuses: APPROVED in the right category → Met and a notice. APPROVED in another category → not Met and not used. REJECTED → the same template is edited and resubmitted; after the limit, Failed and one alert. PAUSED, DISABLED and FLAGGED are never used. Statuses are compared regardless of case. A pending template older than 24 hours is polled.
9. Per-WABA stops: 3 rejections in 7 days, and a RED quality rating, stop auto-submission.

**Email**
10. An email template proposal is approved in Needs you and the need is Met. An edit is re-checked. Bulk approval skips it.

**Using templates**
11. `find`: language fallback; the hand-made Accounts template still used when none is found.
12. Values: newlines, empty values and an over-long filled text are cleaned or refused.
13. Send rules: an agent's template send is refused for an unapproved template, or with no opt-in; it is allowed with a Granted consent, or after an inbound WhatsApp message.
14. First-use review: the first send goes to Review; after one approval, the policy applies.
15. Permissions; endpoint guards; the existing suites stay green.

## 9. Review outcome (v1 → v2)

**Adopted from the review:**
- Names follow Meta's name rule, and the language and WABA are in the name, because template sync matches by name alone.
- Needs are keyed by WABA, not by number; the agent's own number is set on the template.
- Excom's own validators cover little for a text template, so the steward has its own checks (samples, length, name, language).
- Meta errors are caught on the need and never retried by the runner; orphan templates are adopted.
- Shadow agents never submit.
- Needs are claimed with a conditional update and keyed uniquely.
- Needs are recorded at writing time, not inside sends.
- The proposal and policy options for email are added, with re-checks after an edit and no bulk approval.
- The packs keep their own actions; a shared helper sends.
- The approved-template check sits in `send_rules`.
- Marketing and the voice callback are dropped from v1.
- Meta language codes and a fallback order.
- Every status is handled, and pending ones are polled.
- The packs' old lookups stay as the fallback.
- Variable values are cleaned.
- Permissions follow the other Excom AI doctypes.
- Meta can recategorise a template, so a need is Met only in its declared category.
- Samples must be made up.
- The template-slot alert.
- Rework edits the same template.
- Opt-in for every template send.
- First-use review, the library first, a second check in the target language with a back-translation, quality and rejection stops, and owner notices with Withdraw.
- The separate template record is dropped; WhatsApp state stays on `WhatsApp Templates`.

**Not adopted:**
- **Dropping email entirely (S1):** the owner asked for email templates. They are kept for the declared needs only; mining repeated writing is left for later.

## 10. As built (2026-11-10)

**Where it lives:**
- **Core:** `excom/excom_ai/templates.py` holds the declarations, `need`/`find`, the fill helpers, the checks, the hourly `run` (from `monitor.hourly`), `template_draft`, `submit`, the `approve_template` executor and `withdraw`.
- **Records:** the doctype `Excom AI Template Need`, plus four `xai_*` fields on `WhatsApp Templates`.
- **Settings:** under "Template Steward" in Excom AI Settings. Migrate seeds them once, and leaves any value a person saved.
- **Send rules:** for an agent's template, `send_rules.check(..., template=)` checks that it is approved, on the agent's number, and still in its declared category. It also checks opt-in: a recorded "Granted", or an inbound WhatsApp message (marketing needs a marketing "Granted"). `send_template_to_thread` checks UTILITY templates as transactional.
- **Opt-in at writing time:** `number_opted_in` lets the packs choose the channel when they write.

**The packs:**
- **Accounts:** the WhatsApp reminder from the steward first, then hand-made templates; email words from an approved template, with no model call; the statement notice.
- **Purchase:** the PO chaser and RFQ reminder on WhatsApp for suppliers with no email, and the email words.
- **Sales:** the order update and the quotation-validity notice outside the window, through `responder.send_reply`.

**Screens:** a Templates tab on the Agents home and on each agent's page, with Withdraw and Try again. Email template reviews appear in Needs you.

**Code review (a separate agent, before commit):** 13 findings, all fixed:
- A reworked template is marked PENDING locally, so the old rejection no longer fails it.
- `approve_template` acts only on the proposal's own need, and only while that need is in review.
- `withdraw` closes the open review.
- `withdraw` handles a template that was already sent: the row is kept, marked DELETED, and removed on Meta.
- After a withdrawal the next try gets a new versioned name.
- A review that ended frees the need.
- Submission is claimed (Drafting → Submitted once), and stale claims are not reopened while their task is open.
- A shared need may be changed only by its single owner or an Excom Admin, and is visible to every agent's people.
- A template Meta moved is refused at send time, and marketing needs a marketing opt-in.
- The real-record sample check skips lone first names.
- The switch is honoured inside queued work.
- Pending templates are given up on after 14 days.
- The email subject is no longer limited to 140 characters.
- The Sales planner no longer proposes a follow-up on every tick. That bug dated from Phase 1G.

**Not done (later):**
- email templates per ladder step (one per need for now);
- mining repeated writing;
- marketing templates (they need a marketing opt-in record);
- WhatsApp-only buyers on Sales (buyers are found by email);
- translated status words in order updates;
- re-checking the 1024-character limit after a reviewer edits the variables.
