# HLD-009 — People Finder (Apollo, Prospeo)

Status: plan v1, 2026-11-10. Builds on HLD-007 §9 (research) and the tool layer (`excom_ai/tools`).

## 1. What it is

Agents and people can find named decision-makers at a company — their LinkedIn profile, job title and
work email — through two paid data providers, Apollo and Prospeo, called only through their APIs.
We never scrape LinkedIn ourselves.

Two uses:
- **Sales research (automatic).** After a company is profiled with a good fit, the `find_contacts` step
  today finds published and pattern addresses (and Hunter's). A new step, `find_people`, finds named
  buyers by job title at that company and saves them as Excom AI Person rows, with their LinkedIn link.
- **Any CRM record (on demand).** A "Find people" button on the contacts section of a Lead, Prospect,
  Customer or Supplier (Purchase: the supplier's sales or export person) lists candidates; a person
  ticks the ones to keep and they are added as Contacts on that record.

## 2. Provider facts (checked 2026-11-10, docs.apollo.io and prospeo.io/api-docs)

| | Apollo | Prospeo |
|---|---|---|
| Auth | `x-api-key` header, base `https://api.apollo.io/api/v1` | `X-KEY` header, base `https://api.prospeo.io`, JSON POST |
| Search people by company + title | `POST /mixed_people/api_search` — **0 credits**, no emails, last name hidden, returns `id`, `title`, `has_email` | `POST /search-person` — 1 credit per page with results (25 a page), no emails, returns `person_id`, name, title, LinkedIn |
| Enrich one person | `POST /people/match` (by `id`, or name + `domain`, or `linkedin_url`, or `email`) — 1 credit if data returned; phones need a webhook (+8 credits) | `POST /enrich-person` (`data`: name + `company_website`, `linkedin_url`, `email` or `person_id`) — 1 credit per email found, **no match free**, re-enrich within 90 days free; mobile 10 credits |
| No match | 200 with empty `person` / `match_confidence: none` | 400 `{"error": true, "error_code": "NO_MATCH"}` (not charged) |
| Errors | 401 bad key (plain text), 403 endpoint not on key, 422 params, 429 with `retry-after` | 400 with `error_code`: INVALID_API_KEY, INSUFFICIENT_CREDITS, INVALID_DATAPOINTS…; 429 rate |
| Price (unverified, third-party) | ~$49–79/user/month plans; ~$0.03–0.05 per credit | ~$49/2,000 credits → ~$0.02–0.025 per credit |

## 3. Design

### 3.1 Two tools (tier `paid`, `needs_key`)

`tools/people.py`, registered like Hunter:

- `apollo`: `search(domain, titles, seniorities, limit)` → candidates; `enrich(id | first/last + domain |
  linkedin_url | email)` → person. Cost estimates: search 0, enrich 0.04.
- `prospeo`: `search(domain, titles, limit)` → candidates (cost 0.025 a page); `enrich(person_id | name +
  domain | linkedin_url | email, only_verified_email=True)` → person (cost 0.025, refunded locally when
  NO_MATCH — the budget counts only what is charged).

One shape for both:
`candidate = {provider, external_id, first_name, last_name, full_name, title, seniority, linkedin_url, has_email}`
`person = candidate + {email, email_status: Verified|Risky|Unknown, source_url}`

All answers are untrusted text (§12.7): names and titles are cleaned (length, control characters) and
never reach a prompt without the untrusted fence. Keys never come back in errors (the `_redact` rule).

**No phone numbers in v1.** Apollo phones need a public webhook and 9× the credits; Prospeo mobiles 10×.
And a found mobile cannot get a WhatsApp template anyway without opt-in (HLD-008 send rules). Left for later.

### 3.2 Order and waterfall (cheapest first, stop at first good answer)

1. **Search**: Apollo (free) first; Prospeo search only when Apollo is off, refused or found nobody.
2. **Pick** up to `people_per_company` (default 2) candidates, best title match first, seniority as the
   tie-break; skip anyone already saved (same provider id, LinkedIn URL or email).
3. **Enrich** each pick: the provider that found them (by its id); when that gives no verified email,
   the other provider by LinkedIn URL (Prospeo `only_verified_email`, no-match free). Stop at a verified email.
4. Save what we have — a person with a LinkedIn link but no email is still useful (a person can write on
   LinkedIn by hand); they are saved with no email and never enter email sequences.

### 3.3 Records

Excom AI Person:
- `email` stops being mandatory; validate: email **or** LinkedIn URL.
- New fields: `apollo_id`, `prospeo_id`, `provider_email_status` (what the provider said), `found_on`.
- `email_source` gains Apollo, Prospeo; `verified_by` gains Apollo, Prospeo.
- `verify_email`: our verifiers run as today when switched on. When none gave an answer and the provider
  said verified, the person is Verified "by Apollo/Prospeo" — honest about who checked. The consent check
  (Precleared) runs either way.
- Everywhere that assumes an email (planner's verify query, `find_contacts` task fan-out, `promote`,
  sequencer) skips people without one; `promote` refuses with a clear message.

Excom AI Company: `people_checked_on` so a company is searched once (again after 90 days).

### 3.4 The Sales step

New task kind `find_people` (subject Excom AI Company), queued by the Sales planner for companies that
are Profiled/Contacts found/No contacts, fit ≥ `FIT_FOR_CONTACTS`, not excluded or promoted,
`people_checked_on` unset, and only when `apollo` or `prospeo` is available. `PER_TICK` 3.
Exclusions are checked again before spending (customer, DNC domain, a colleague's lead).
New people go to `verify_email` as today.

Job titles: Excom AI Settings `people_titles` (default: purchase, procurement, buyer, import, sourcing,
category manager, owner, managing director, director); an agent can override with its own `people_titles`.
Purchase on-demand uses `supplier_people_titles` (default: sales, export, key account, business development, owner).

### 3.5 On-demand "Find people" on CRM records

- `excom_ai.api.find_people(doctype, name)` — Lead, Prospect, Customer, Supplier only; caller needs
  write permission on the record and a role of Excom Agent or above; domain from the record's website or
  its contacts' email domains (free-mail ignored). Returns candidates (search only — free on Apollo).
  Rate: 20 calls an hour per user.
- `excom_ai.api.add_people(doctype, name, picks)` — enriches the ticked candidates (that is when credits
  are spent; the dialog says so) and creates a Contact for each, linked to the record, with LinkedIn URL
  in the contact's notes field / a `linkedin_url` custom field. Existing contact with the same email: link,
  don't copy.
- Frontend: a "Find people" button in `ContactsSection` → dialog with candidates (name, title, LinkedIn,
  "has email"), tick, "Add (uses up to N credits)".

### 3.6 Settings

Excom AI Settings, section "People finder": `people_per_company` (2), `people_titles`,
`supplier_people_titles`, `people_recheck_days` (90). Seeded once like the template defaults.
The tools themselves start switched off (as every paid tool); a manager adds the key, a monthly budget
and switches them on in Excom AI Tool.

## 4. Rules

- **WhatsApp:** a phone from a provider never counts as opt-in (we don't fetch phones in v1 anyway).
- **Email:** found people are cold contacts: marketing rules — unsubscribe link, suppression list,
  Precleared consent verdict, DNC domains — exactly as for Hunter's addresses today.
- **No scraping:** API calls only; LinkedIn URLs are stored as links, never fetched.
- **Data removal:** deleting an Excom AI Person deletes what we found; the source and date are kept on
  each row so a removal request can be answered ("where did you get my data").
- **Budgets:** each tool's monthly budget in Excom AI Tool caps spend; the planner stops queueing
  `find_people` when both tools are refused.

## 5. Cost

No model calls — this is plain API work.

| | per company (2 people) |
|---|---|
| Apollo search | $0 |
| Apollo enrich ×2 | ~$0.08 |
| Prospeo fallback (only when Apollo has no verified email; no-match free) | ~$0–0.05 |
| **Total** | **~$0.08–0.13** |

100 good-fit companies a month ≈ $8–13. Plan subscriptions are the real floor: Prospeo Starter ~$49/month
(2,000 credits), Apollo Basic ~$49–59/user/month. On-demand lookups: search free (Apollo), ~$0.04 per
person added.

## 6. Tests (fakes only; no network)

- adapters: request shape (URL, headers, body), mapping, no-match (Apollo empty, Prospeo NO_MATCH not
  charged), errors redact the key, 429 → ToolRefused-friendly error.
- waterfall: Apollo search → Apollo enrich; no verified email → Prospeo by LinkedIn; Apollo off →
  Prospeo search; both off → nothing queued.
- dedupe: same provider id / LinkedIn / email not saved twice; a company is not searched twice.
- LinkedIn-only person: saved, never verified, never promoted, never sequenced.
- verify_email: provider-verified accepted only when no verifier answered; `verified_by` honest.
- exclusions re-checked before spending.
- on-demand: permission (no write → refused), domain from website/contacts, contacts created and linked,
  existing email linked not copied.
- budget: a paid call over budget refused.

## 7. Plan review outcome (v1 → as built)

An agent review of v1 found seven must-fix gaps; all are in the build:

1. **A failed task must not pay twice.** `find_people` marks the company searched (`people_checked_on`) and
   commits *before* the first paid call; every paid call commits in a `finally`; a failed save rolls back
   only itself. A retry finds the mark and spends nothing.
2. **Refusals never fail the task.** Budget, 429, a bad key, no credits, or Apollo's 403 (search needs a
   master key) raise `ToolRefused`, recorded on the tool's `last_error` for the admin, and research falls
   through to the other provider. Five failures in a row would otherwise put the agent on hold.
3. **A person found again is updated, not doubled** (by provider id, LinkedIn URL or address — never an
   empty value), so the unique email index is never hit.
4. **People without an address stay out of every email path**: the verify fan-out, the planner's verify
   query, `verify_email` itself, `promote` (refuses with a message), the sequencer (needs Ready + Verified).
5. **The button takes picks only from a list kept on the server** for that user and record (30 minutes).
6. **Provider text is fenced** in the writer's buyer block (it was not before).
7. **Budget counts what is billed** (`charged` on the tool: Prospeo no-match and repeats are free) and
   Apollo's free search is budget-checked at cost 0.

Cut for v1, as the review advised: Supplier and Customer on-demand (Supplier is not a CRM record in the
gateway; Customer is admin-only to edit), re-searching after 90 days, per-agent job titles, and Prospeo
search when Apollo answered but found nobody.

## 8. As built (2026-11-14)

- `excom_ai/tools/people.py` — the two adapters, cleaning (LinkedIn URLs to one form, Frappe's own email
  check, Apollo's locked `email_not_unlocked@` placeholder dropped), ranking, fakes.
- `excom_ai/research.py` — `find_people` task kind, `people_search`, `pick`, `enrich_one`, `save_person`;
  planner queues it after `contacts_checked_on`, 3 a tick; provider verification counted only when none of
  our verifiers answered, and `verified_by` names the provider.
- `excom_ai/people_finder.py` — `find_people` / `add_people` for Leads and Prospects.
- `excom_ai/mail.py` — cold-email footer says where a provider-found address came from (GDPR Art. 14).
- `excom/api/party_contacts.py` — `put_on_record` (the insert part of `add_contact`, unchanged behaviour);
  contacts list returns `ai_linkedin_url`.
- Records: Person email optional (email or LinkedIn required), `apollo_id`, `prospeo_id`,
  `provider_email_status`; Company `people_checked_on`; Contact custom fields `ai_linkedin_url`,
  `ai_found_via`, `ai_people_id` (hidden); Settings section "People Finder"; tool rows `apollo`, `prospeo`
  (off until a manager adds a key and a budget).
- Frontend: Find people dialog on the People section of Leads and Prospects; LinkedIn links on contacts and
  on the agent's Research tab, with an "On LinkedIn only" list.

### Code review fixes

- The button never pays twice for one person: each lookup is written back to the kept list at once, a
  new search keeps those marks, and a Contact remembers the provider id (`ai_people_id`) and LinkedIn URL.
- A pick is the candidate's own id (`Apollo:<id>`), not a list position, so a second tab cannot swap people.
- The per-record lock outlives the worst case (600 s) and is released only by its owner (token).
- The daily cap is counted before spending (atomic), and given back when the call is refused.
- On demand, a person costs one lookup: the second provider is asked only when the first found no address.
- An address research already holds is reused only when checked (Verified/Risky, not Invalid or consent
  Blocked) and only from research the user may see; the Contact names the address's real source.
- The "On LinkedIn only" list leaves out excluded and stopped companies.
- The frontend draws a LinkedIn icon only for linkedin.com links.
- Apollo's plain-text 401/402/403 answers are refusals, not errors.

### Not done / limits

- **No live calls were made** to Apollo or Prospeo: request shapes follow their docs (checked 2026-11-10)
  and are tested against scripted answers. Prospeo's search filter names (`company.websites`,
  `person_job_title.match_mode`) should be confirmed with the first real key.
- No phone numbers (by design, see §3.1).
- Budget check and spend are not atomic across parallel workers (an existing tool-layer limit).
- Supplier/Customer on-demand not built.
