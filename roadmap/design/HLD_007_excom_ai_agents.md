# HLD-007 — Excom AI Agents: module design and build plan

| | |
|---|---|
| **Document** | HLD-007, version 0.2 (owner's decisions of 2026-11-09 in §20; sales data model in Appendix C) |
| **Date** | 2026-11-08 |
| **Status** | Design approved in direction. Nothing is built. Open: the `ai_interface` branch question (Appendix B) and the decisions marked open in §20. |
| **Owner** | Sagar Ratan Garg |
| **Replaces** | The structure of HLD-006 (AI Sales Agents), which stays as reference for the Harvey port details, and `roadmap/phase_B_ai_layer.md` |
| **Related** | HLD-004 (voice), HLD-005 (WhatsApp platform), `roadmap/export_pilot.md`, `roadmap/sales_sop.md`, `~/Documents/AI-Sales-Agents-Design.md` (the abstract) |

---

## 0. Summary

Excom gets a separate module, **Excom AI**, for AI colleagues ("agents"). An agent is a record that a manager creates, not a program:

1. Pick a type with one button: **Sales, Purchase, Accounts, Compliance** or **Custom**.
2. Type a few comma-separated keywords, for example `export, water guns, latin america, spanish`.
3. Excom reads the ERP for everything those keywords touch: current export customers and their countries, what they buy, the price lists and payment terms in use, the ports, forwarders and transporters already used, open leads and quotations, past conversations.
4. From the type, the keywords and those facts, the AI drafts the agent: what it is, its mission, tone, languages, audience, products and prices, channels, rules and targets. Every line shows where it came from.
5. A **readiness check** lists what must be in place before the first new customer arrives, for example "no export price list in EUR", "11 items without HS code", "no forwarder for Chile", "LUT expires in 20 days". Fixes become tasks for people.
6. The owner approves. The agent starts in **Shadow** (it works, but nothing leaves the company), then **Review** (a person approves each outgoing action), and moves to **Auto** one action at a time, only when the numbers say so.

**Phase 1 is the Sales agent.** It rebuilds what Harvey does today (find companies, research them, write, send, handle replies) inside ERPNext, for any number of salespeople, through Excom's mailboxes and WhatsApp numbers. It adds what Harvey lacks:
- quotations from the real price list;
- reactivation of existing customers;
- an owner and backup for every agent;
- monitoring.

**Later phases:**
- **Phase 2** generalises the builder and adds Accounts, Purchase, Compliance and Custom agents on the same engine. Accounts is recommended first: on GGIL, ₹8.74 Cr of receivables are over 90 days and ₹1.58 Cr is owed to MSME suppliers beyond 45 days.
- **Phase 3** adds voice.
- **Phase 4** adds the learning loop.

**What the GGIL data shows for the export example (§17).** The ERP is not yet ready to serve a new export buyer:
- 5 overseas customers in 2 countries, and 11 export invoices ever;
- no payment terms;
- no export price list for water guns;
- photos on 18 of 189 water-gun items;
- no freight forwarder on record;
- nowhere to record the LUT.

The readiness check turns that into a short list of fixes for people, which can start now.

**What this plan changes in HLD-006** (each needs the owner's yes, §20):

- Mail goes through **Excom's own mailboxes** (Stalwart over JMAP, or Gmail), built on 2026-11-08 with folders, drafts, scheduled send and two-way sync. HLD-006 planned a separate SMTP/IMAP transport.
- **All AI goes through `ai_interface`.** It grows, in a generic way, into the AI mediator for every app on the bench (Appendix B). The agents name what they need (tools, structured output, vision), never a provider, and `ai_interface` routes, budgets, logs and costs every call.
- The engine is generic from day one. Sales is the first **capability pack**, not a one-off.
- Work is committed to `main` and pushed once verified, per the owner's standing rule. HLD-006 said feature branches.

---

## 1. Goals and non-goals

**Goals**

- Several agents of several types work at once, each with its own persona, scope, channels, limits and human owner.
- Creating an agent takes minutes: a type, keywords, a review of what the ERP shows, an approval.
- Every agent is grounded in ERP facts (customers, items, price lists, terms, logistics, invoices), never in invented ones.
- Nothing about Garg, water guns or Holi colour lives in code. Products, prices, rules and personas are records the business edits.
- Every customer-facing word can be traced to a task, its inputs, the prompt version, the model and the person who approved it.
- People stay in charge of money, legal terms, delivery promises and anything irreversible.
- It runs on the production Frappe Cloud site. Voice needs one small extra service (§11.4).

**Non-goals**

- No accounting entries, payments, submitted documents or price changes made by an AI. It may prepare drafts; a person submits.
- No cold WhatsApp to people who have not opted in, and no LinkedIn scraping.
- No second CRM. Everything lands on native ERPNext records (Prospect, Lead, Opportunity, Quotation, Customer, Supplier, ToDo, Comment) and on Excom threads.
- No general-purpose chatbot. An agent works only within its type, scope and playbooks.
- No voice in Phase 1.

---

## 2. Principles

1. **An agent is a record.** A new salesperson, a new market or a new language is a new record, not new code.
2. **Readiness before reach.** An agent cannot go live until its readiness check passes, and it is checked again every night.
3. **Stateless work, one queue.** Workers remember nothing between tasks. Every task is built fresh from one agent's brief, its playbooks, a slice of its ERP knowledge and the subject's own records. One queue feeds every agent, in turns, within budgets.
4. **Code checks before AI checks.** Prices, recipients, opt-outs, banned claims, length, language, send windows and caps are checked by code. The AI checker covers tone and implied claims only.
5. **A person owns every agent.** Each agent has an owner and a backup who are active users. Without them it holds (§12.3).
6. **Proposals, not surprises.** Anything that leaves the company or changes a business record is a proposal until its action is switched to Auto for that agent.
7. **Native records only.** Prospects, Leads, Opportunities, Quotations and conversations are ERPNext and Excom records. The desk sees AI work and human work in one place and can take over at any moment.
8. **Untrusted text stays untrusted.** Web pages, emails, chat messages and attachments are data. They never change an agent's instructions, recipients or tools.
9. **Off costs nothing.** A master switch, off by default. With it off there are no scheduled AI calls, no queue entries and no import-time side effects.
10. **Learning is visible.** Lessons become playbook changes a person approves, never hidden agent memory.

---

## 3. Vocabulary

| Term | Meaning |
|---|---|
| **Agent** | One AI colleague: persona, type, keywords, brief, scope, channels, owner, limits and autonomy |
| **Agent type / capability pack** | What an agent can do: Sales, Purchase, Accounts, Compliance or Custom. Code defines the abilities (task kinds, tools, data recipes, checks); records hold the editable defaults |
| **Keyword** | A word or phrase typed when creating an agent; resolved to ERP data through recipes |
| **Recipe** | A safe, read-only ERP query that turns a keyword into facts, for example *export → customers with GST category Overseas* |
| **Knowledge pack** | The facts the recipes produced for one agent, versioned and refreshed nightly; what prompts read |
| **Brief** | The generated and approved description of the agent: what it is, mission, audience, tone, languages, rules, targets |
| **Playbook** | Knowledge a person writes or approves: product facts, allowed and banned claims, objection answers, FAQ, process rules |
| **Readiness check** | Rules that test whether the ERP and the agent are ready; gaps become tasks for people |
| **Subject** | The record an agent works on: a Prospect, Lead, Customer, Supplier, Sales Invoice, Purchase Order or compliance item |
| **Task** | One unit of work in the queue: research a company, write a sequence, classify a reply, draft a quotation |
| **Proposal** | An action an agent wants to take that needs approval under its policy: send an email, send WhatsApp, create a quotation |
| **Sequence** | A cadence of steps over days (cold outreach, payment reminders, RFQ follow-ups) that stops on reply or on a business event |
| **Policy** | For each agent and action: Off, Shadow, Review or Auto, with sampling and caps |
| **Service user** | The Frappe user an agent acts as, with only the permissions its type needs |
| **Owner, backup** | The people responsible for an agent. The agent's authority never exceeds its owner's status |

---

## 4. Architecture

```
                        ┌──────────────────────────── Excom AI module ────────────────────────────┐
  Manager ─ Builder ──▶ │ Agent records ◀── Knowledge packs ◀── Recipes (safe ERP reads)          │
  (type + keywords)     │      │                  ▲                                               │
                        │      │ brief, playbooks │ nightly refresh, drift alerts                 │
                        │      ▼                  │                                               │
  Scheduler (2 min) ──▶ │ Orchestrator: gates (switch, owner, budget, hours, health) → planners   │
  Excom events ───────▶ │   (new reply, bounce, opt-out, overdue invoice, late PO) → tasks        │
                        │      │                                                                  │
                        │      ▼  one queue, priorities, turns per agent, budgets                 │
                        │ Workers (stateless): assemble prompt → AI call (ai_interface)           │
                        │   → tools (bounded) → structured output → code checks → AI check        │
                        │      │                                                                  │
                        │      ▼                                                                  │
                        │ Proposals ──(policy: Review)──▶ people approve in Excom (desk, phone)   │
                        │      │ (policy: Auto, with sampling)                                    │
                        │      ▼                                                                  │
                        │ Executor (as the agent's service user, idempotent)                      │
                        └──────┬──────────────────────────────────────────────────────────────────┘
                               ▼
     ERPNext records (Prospect, Lead, Opportunity, Quotation draft, ToDo, Comment)
     Excom channels (mailbox drafts and sends, WhatsApp, Instagram and Messenger, later voice)
     Audit trail, usage, metrics → monitoring, alerts, daily digest
```

**Where it runs.** All of the above runs on the Frappe Cloud site, in background workers, calling Claude through `ai_interface` with an API key. Two things need a small VM: the voice service (Phase 3), and optionally the Claude command-line provider, if the owner keeps using it for anything (§10.6).

**Code layout** (a new Frappe module, "Excom AI", in the excom app):

```
excom/excom_ai/
  doctype/                 the records in §5
  engine/                  orchestrator (tick, gates, turns), tasks, runner (prompt assembly, AI call,
                           validation), proposals (approve, execute), guardrails, budgets,
                           ownership (dead-man switch, hold, handover), audit
  builder/                 keywords (resolution), recipes/ (registry), knowledge, brief, readiness/
  packs/                   sales/  purchase/  accounts/  compliance/  custom/
                           (each: planner, task kinds, prompts, output schemas, readiness rules)
  tools/                   discovery, web reader, email finder and verifier, imports, ERP read,
                           ERP draft, Excom channels, DNS check
  api/                     whitelisted endpoints for the Excom screens
  tests/                   fakes (AI, search, mailbox), golden sets, end-to-end
frontend/src/routes/agents/   the screens in §15
```

**One engine, many packs.** Every type works the same way: a **planner** picks subjects that need work, **sequences** pace multi-step outreach, **event handlers** react to replies and business events, and everything ends in **proposals**. Sales uses all four. Accounts uses sequences (payment reminders) and handlers (payment received, dispute). Purchase uses sequences (RFQ follow-ups, PO chasers) and handlers (supplier reply, receipt). Compliance mostly uses the planner (due dates, document expiry) and creates ToDos. Building the engine generic in Phase 0 is what makes Phase 2 cheap.

---

## 5. Records

**As built (2026-11-09, Phase 0.2):** the records are named **Excom AI …**, not "Excom Agent …":
- **Why:** "Excom Agent" is the human agent role in Excom, so "Excom Agent Task" would read as a person's task.
- **Renames:** Excom Agent → Excom AI Agent, Excom Agent Type → Excom AI Agent Type, Excom Agent Settings → Excom AI Settings, and every other "Excom Agent X" → "Excom AI X". Excom Email Domain is now Excom AI Email Domain.
- **Field prefix:** fields added to native records use the prefix `ai_` (`Lead.ai_agent`, `Prospect.ai_company_stop`, `Excom Thread.ai_mode`…).
- **Budget scope:** an agent's budget scope in ai_interface is `Excom AI Agent:<name>`.

The text below keeps the original names.

Names start with **Excom Agent** to avoid clashes with `ai_interface`, which already has AI Settings, AI Provider, AI Prompt Template and AI Call Log.

### 5.1 Configuration

**Excom Agent Settings** (single)

| Field | Type | Purpose |
|---|---|---|
| `enabled` | Check, off | Master switch. Off means no ticks, no AI calls, no queue entries |
| `default_model` | Data | `claude-opus-5-5` unless the owner chooses otherwise (§10.3) |
| `effort_classify`, `effort_write`, `effort_reason` | Select | Effort per task class (§10.3) |
| `use_batch_for_night_work` | Check | Non-urgent tasks go through the Batch API at half price (§10.4) |
| `refusal_fallback` | Check | Use the server-side fallback when a request is declined (§10.3) |
| `daily_budget_total` | Currency | Hard stop across all agents |
| `review_sample_percent` | Percent, 10 | Share of Auto actions drawn for after-the-fact review |
| `prompt_retention_days` | Int, 30 | How long full prompts and outputs are kept |
| `alert_users` | Table MultiSelect (User) | Who gets system alerts |
| `digest_time` | Time | Morning digest |
| `postal_addresses` | Table (company, address) | Required footer for commercial email |
| `disclosure_default` | Small Text | Default AI disclosure line (§12.6) |
| `dnc_domains` | Table | Global do-not-contact domains (competitors, own group companies) |
| `quiet_dates` | Table | Holidays when no outreach is sent |

**Excom Agent Type** (seeded: Sales, Purchase, Accounts, Compliance, Custom)

`type_name`, `pack` (sales, purchase, accounts, compliance, custom), `icon`, `colour`, `description`, `enabled`, `default_policies` (table: action, mode), `default_tools`, `brief_prompt` (Link AI Prompt Template), `readiness_rules` (table), `default_kpis`.

**Excom Agent** (one per AI colleague)

| Group | Fields |
|---|---|
| Identity | `agent_name` (persona name customers see, for example Harish), `title` (for example "Export Sales, Garg Global"), `agent_type`, `status` (Draft, Preparing, Shadow, Live, On Hold, Retired), `service_user` (Link User, created automatically), `avatar` |
| People | `owner_user` (required), `backup_user` (required before Live), `team` (Excom Team), `escalation_user`, `owner_last_seen` (Datetime), `hold_reason` |
| Keywords | `keywords` (Small Text), `keyword_rows` (table: keyword, resolved as, recipe, matches, included) |
| Brief | `what_it_is`, `mission`, `audience`, `tone`, `tone_examples`, `rules_do`, `rules_dont`, `kpi_targets` (table), `brief_version`, `brief_approved_by`, `brief_approved_on` |
| Scope | `markets` (table: country or territory, language, timezone, priority), `products` (table: item or item group, price list, minimum quantity, note), `customer_groups`, `supplier_groups`, `company` |
| Channels | `channels` (table: Excom Channel Account, purpose (outreach, replies, both), daily cap, warm-up start, signature mode, disclosure line) |
| Knowledge | `playbooks` (Table MultiSelect), `knowledge` (Link Excom Agent Knowledge, latest), `readiness_state` (Not checked, Blocked, Warnings, Ready) |
| Behaviour | `tools` (table: tool, enabled, monthly budget), `policies` (table Excom Agent Policy: action, mode, sample %, daily cap, condition), `office_hours` (market timezones, or custom), `daily_ai_budget` |
| Read-only | `metrics` (JSON, refreshed hourly) |

**Excom Agent Playbook**: `title`, `agent_type`, `language`, `status` (Draft, In Review, Approved, Retired), `version`, `sections` (table: kind, title, content, source (Manual, ERP, Coach), source reference), `approved_by`, `approved_on`, `change_note`. Section kinds: Facts, Prices and terms, Allowed claims, Banned claims, Objections, FAQ, Process rules, Compliance, Tone examples. `track_changes` is on. Only Approved versions reach prompts.

**Excom Agent Recipe**: `recipe_key`, `label`, `keywords` (synonyms), `agent_types`, `kind` (Built-in or Query spec), `query_spec` (JSON for the safe query builder), `privacy` (aggregates only, names allowed, contacts allowed), `section`, `enabled`. Built-in recipes are Python and are seeded as records so they can be found, switched off and extended (§6.3).

**Excom Agent Knowledge**: `agent`, `version`, `generated_on`, `recipes_used` (JSON), `facts` (JSON by section), `summary` (Markdown), `changes_since_last`, `approx_tokens`.

**Excom Agent Readiness Rule** (seeded per type) and **Excom Agent Readiness Gap** (results): rule `key`, `severity` (Blocker, Warning, Info), `applies_when`, `fix_hint`, `fix_route`; gap `agent`, `rule`, `status` (Open, Fixed, Waived), `detail`, `todo` (Link ToDo), `waived_by`, `waive_reason`, `checked_on`.

### 5.2 Work

**Excom Agent Task**: `agent`, `kind`, `priority` (0 is most urgent), `status` (Queued, Running, Done, Failed, Skipped, Held, Batched), `subject_doctype` and `subject_name`, `thread` (Link Excom Thread), `dedupe_key` (one open task per key), `not_before`, `attempts`, `started_on`, `ended_on`, `model`, `effort`, `ai_calls`, `input_tokens`, `cached_tokens`, `output_tokens`, `cost`, `prompt_versions` (JSON), `outcome`, `error`. Kept 90 days.

**Excom Agent Proposal**: `agent`, `task`, `action`, `subject`, `thread`, `channel_account`, `payload` (JSON), `preview_html` (exactly what the recipient will see), `checks` (JSON: each code check, pass or fail with reason), `checker_verdict` (Pass, Fixed, Fail, with notes), `risk` (Low, Medium, High), `status` (Pending, Approved, Edited, Rejected, Expired, Executing, Done, Failed, Cancelled), `decided_by`, `decided_on`, `reject_reason` (wrong facts, wrong tone, wrong recipient, not needed, other) and note, `edit_diff`, `email_draft` (Link Excom Email Draft, for emails), `executed_ref`, `expires_on`.

An email proposal **is** an Excom Email Draft. It is saved in the mailbox's Drafts folder, so the owner can also see it in the Stalwart webmail or Gmail. Approving it sends that draft, through the code built on 2026-11-08.

**Excom Agent Sequence**: `title`, `agent_type`, `steps` (table: step, wait in business days, channel, purpose, prompt, stop on). **Excom Agent Enrollment**: `agent`, `sequence`, `subject`, `company_key` (a buyer's domain or a customer, so one reply stops the whole company), `status` (Active, Paused, Replied, Stopped, Completed), `stop_reason`, `step`, `next_due`.

### 5.3 Research and tools

**Excom Agent Signal Code** and **Excom Agent Observation** (from Harvey): signal `code`, `label`, `category`, `value_type`, `collector`, `confidence_floor`; observation `subject`, `signal`, `value`, `confidence`, `evidence_url`, `observed_on`, `task`. **Excom Email Domain** (from HLD-006): `domain`, `mx_provider`, `pattern`, `catch_all`, `verified_on`, cached results. **Excom Agent Tool**: `tool_key`, `enabled`, credentials (Password fields), `monthly_budget`, `used_this_month`, `rate_limit`, `last_error`. AI usage and cost are not duplicated here: they live in `ai_interface`'s call log and budgets, attributed to the Task (Appendix B). The full Sales data model, field by field, is Appendix C.

### 5.4 Fields added to existing records

| Record | Fields |
|---|---|
| Prospect | `excom_agent`, `fit_score`, `research_notes`, `website`, `found_via`, `checked_on` |
| Lead | `excom_agent`, `email_status` (verified, risky, guess, invalid), `timezone` |
| Opportunity | `origin_agent` |
| Excom Thread | `handled_by_agent` (Link Excom Agent), `agent_mode` (Off, Assist, Handle, Hold), `last_intent` |
| Supplier (Phase 2) | `excom_agent`, `research_notes`, `checked_on` |

---

## 6. The agent builder: from keywords to an agent

### 6.1 How a keyword is understood

Each keyword goes through three layers, in this order. The first match wins, and every match is shown, so the manager can correct it.

1. **The company's own vocabulary.** A fuzzy match against names in the ERP: Item Group, Item (`item_name`, `brand`, `shade_of`), Customer Group, Supplier Group, Territory, Country, Currency, Price List, Excom Team and Excom Channel Account.

   ERP names are often trade words, not the words a manager types: on GGIL, water guns are the item group **Pichkari** and Holi colour is **Gulal**, **Gulal SF** and **Colour**. So the builder keeps a small **glossary** per company (Excom Agent Glossary: term, language, doctype, name). It is seeded by the AI, which proposes "water gun → Pichkari (189 items)" from item names and descriptions, and a person confirms it once; every later agent reuses it.
2. **Built-in concepts.** Recipes for words that mean a business situation rather than a record. Each is defined in code and tested against real field names (§6.3):
   - export, import, domestic, B2B, B2C;
   - distributor, retailer, OEM, marketplace;
   - transporter and logistics, receivables and collections, payables and MSME;
   - GST and compliance, raw material and purchase;
   - region names such as Latin America, EU, GCC, ANZ, North America;
   - languages: Spanish becomes the `es` locale and the Spanish-speaking countries with their timezones.
3. **Everything else** goes to the AI as a recipe proposal (§6.4).

Countries and languages need a map, because GGIL has no region tree (only India and Rest Of The World), and every customer's `language` is `en`. The map ships with the module.

### 6.2 The flow, screen by screen

1. **Type.** Five buttons, each with one line: Sales ("finds and talks to buyers, quotes from your price list"), Purchase ("finds suppliers, asks for quotes, chases deliveries"), Accounts ("chases payments, keeps promises on record, watches payables"), Compliance ("tracks due dates, checks documents and transactions"), Custom ("you describe the job").
2. **Keywords.** One box, comma-separated. Each keyword becomes a chip as it is typed, with a live count of what it matched, for example *export → 23 customers in 9 countries*. A chip that matched nothing is grey, with "Suggest" (§6.4).
3. **What the ERP shows.** One panel per section of the knowledge pack (§6.3). Each fact links to its records. The manager can untick a customer, a country or an item to leave it out.
4. **The draft agent.** The AI writes the brief from the type, the keywords and the knowledge pack:
   - a persona name suggestion and title, which the manager can replace;
   - "what this agent is", in two or three sentences;
   - mission and targets (for example "reply to every export enquiry within 2 working hours; 5 qualified buyers a month in LatAm");
   - audience, written as a lookalike of the best current customers: their countries, buyer types, order sizes;
   - tone, with two or three sample opening lines per language, learned from the desk's own past messages (§6.5);
   - languages and markets with timezones;
   - products with their price basis (price list, quantity tiers, incoterm);
   - channels it may use, from the mailboxes and numbers the owner's team is allowed on;
   - do and don't rules: type defaults, plus facts such as "quote only from the price list *Export USD*";
   - a starting policy (Shadow) and a playbook skeleton filled from the ERP, with empty sections for people to write (banned claims, objection answers).

   Every line carries a source chip, for example "from 412 invoices, last 12 months" or "from Price List *Export USD*". A line without a source is marked "AI suggestion, please check".
5. **Readiness.** The check runs (§7). Blockers stop the agent from going past Shadow. Each gap has a Fix link and can be turned into a ToDo for a named person with one click.
6. **People and limits.** Owner, backup, team, daily caps, AI budget, office hours.
7. **Save.** The agent is saved as Draft. The owner approves the brief and starts Shadow.

### 6.3 What the ERP is asked: the knowledge pack for a Sales agent

Each section comes from a recipe. Field names are the ones that exist and hold data on the GGIL site (checked 2026-11-08, read-only). "Privacy" says what may reach a prompt.

| Section | Source (doctype, filter) | Privacy |
|---|---|---|
| Customers in scope | Customer where `gst_category` = Overseas, or `customer_group` = Export-Overseas, or `default_currency` ≠ company currency; country from Address (`tabDynamic Link` to Customer) | Names, countries and totals |
| Sales history | Sales Invoice (submitted) where `gst_category` = Overseas or `currency` ≠ company currency. Both filters are needed: one GBP invoice on GGIL is tagged Unregistered. 12-month totals, top customers, items and item groups from Sales Invoice Item | Aggregates and names |
| Exclusion list | Every customer in scope; customers in group Sister Companies; open Leads and Opportunities owned by people; do-not-contact identities and domains | Kept in code, not sent to prompts |
| Products | Items in the keyword's item groups: `gst_hsn_code`, `image`, `weight_per_unit`, `country_of_origin`, `brand`; plus domestic sales of those groups as evidence of range and capacity | Item facts |
| Price basis | Price List (`selling` = 1) in the market currency and its Item Prices; Currency Exchange | Prices and tiers |
| Terms | Payment Terms Template; Incoterm; `is_export_with_gst` usage on export invoices | Terms |
| Logistics | Supplier where `is_transporter` = 1; forwarders and customs agents (supplier group, or a name pattern while groups are empty); transporters used on Delivery Note and Sales Invoice (`transporter`); ports (`port_code`) | Names |
| Pipeline | Lead where `excom_team` = Export Sales, or `country` ≠ India, or `customer_type` = Export Importer; Opportunity; Quotation in a foreign currency | Counts and stages |
| Conversations | Excom Threads on the export mailboxes and numbers; mined for frequent questions (FAQ draft) and for tone (§6.5) | Summaries only |
| Compliance | GST Settings (`enable_overseas_transactions`); the LUT; certificates and licences with expiry dates | Facts and dates |

Recipes for the other types are the same idea over other records:
- **Accounts:** Sales and Purchase Invoices with `outstanding_amount` aged by `due_date`; Payment Entry; Bank Transaction; MSME suppliers.
- **Purchase:** Purchase Invoice Item by item group, suppliers, buying Item Prices, Purchase Orders and their `schedule_date`.
- **Compliance:** GST Settings, the GSTIN status of parties, e-invoice and e-way bill status, document expiry.

Recipes run as the person building the agent, through `frappe.get_list`, so they never show more than that person may see.

### 6.4 Keywords the ERP doesn't know

A keyword that matches no master record and no built-in concept goes to the AI with a short schema summary of the allowed doctypes, in the manner of `ai_interface`'s `query_engine`. What comes back is a **proposal**: a recipe in the safe query format, its plain-English meaning and a sample of what it returns. The manager accepts or discards it. The AI never runs free SQL, and an accepted recipe is saved as an Excom Agent Recipe so the next agent can reuse it.

### 6.5 Tone from the desk's own words

For Sales, Purchase and Accounts agents, the builder reads the last 30 to 50 outbound emails and WhatsApp messages written by people on the owner's team, in the agent's scope. It extracts style traits (formality, length, greeting and sign-off habits, language mix), not sentences. The result is shown as tone traits plus sample lines the manager can edit. Customer names and personal details from those messages are never stored in the brief.

### 6.6 Staying aligned after go-live

Every night the knowledge pack is rebuilt. When something changes that matters to the agent, the owner is told and the readiness check reruns:

- a price list the agent quotes from changed;
- a new customer appeared in its scope, which also updates its exclusion list (an agent never cold-mails an existing customer);
- an item in scope was disabled or lost its HS code or photo;
- a forwarder was disabled;
- the LUT or a certificate is close to expiry;
- a playbook was edited.

---

## 7. Readiness: aligned before the first customer arrives

### 7.1 How it works

Each pack ships readiness rules. A rule is a code check that returns pass or fail, a detail line, a Fix link and a suggested person to fix it. The check runs:
- when the agent is built;
- before it moves from Shadow to Live;
- every night;
- whenever a drift event in §6.6 fires.

**Blockers** keep an agent from going Live. In Shadow it may still work, so that its preparation (research, drafts, the FAQ) is ready the day the gaps close. **Warnings** are shown and tracked. An Excom Admin can waive a blocker with a written reason, and the waiver is logged.

### 7.2 Sales rules, and what GGIL shows today for `export, water guns`

| Rule | Check | GGIL today |
|---|---|---|
| Price basis | A selling price list in each market's currency covers every item in scope, with quantity tiers | **Fails.** The only foreign-currency list is "Color Blaze" (USD, 41 Gulal prices, one customer). No Pichkari price in USD |
| Payment terms | An export Payment Terms Template exists and is the agent's default | **Fails.** No Payment Terms Template on the site |
| Product facts | Items in scope have a photo, weight and carton details, and an HS code | **Fails.** Pichkari: 18 of 189 items have a photo and 6 have a weight; all have an HS code |
| Incoterm | A default incoterm and named place for the agent | Warning. Incoterm records exist but were used on 2 quotations only |
| Freight and customs | At least one freight forwarder and one customs agent for the target lanes, and reference freight rates | **Fails.** None recorded. FedEx is the only carrier on export invoices |
| LUT | A valid LUT, with its expiry, for exports without IGST | **Fails.** No field for it anywhere. Only `is_export_with_gst` exists on invoices |
| Export documents | Quotation and proforma print formats show currency, incoterm, HS code and the bank's SWIFT details | To check |
| Exclusion list | Built and non-empty | Passes: 5 overseas customers, the sister companies, and people's open leads |
| Audience basis | Customers in or near the target markets to model the audience on | Warning. None in Latin America, so the audience comes from the playbook and market data, not lookalikes |
| Mailbox | The agent's mailbox is connected; SPF, DKIM and DMARC pass; not on blocklists; warmed up for at least 14 days | To set up on Stalwart |
| WhatsApp | An approved first-contact template; the number's quality rating is green | Only if WhatsApp is in scope |
| Playbook | Approved, with Certifications, Banned claims and Objections filled | **Fails.** Not written yet. Harvey's `product_knowledge` and `competitive_intel` files are the starting point |
| Certificates | Every certificate the playbook claims is on record with a valid expiry | Warning. Certificate expiry dates live in the docpulse register, which is not installed on the GGIL site |
| People | Owner and backup active; team set | Set while building |
| Lead hygiene | Leads tagged to the export desk really are export leads | Warning. 37 of the 46 Export Sales leads are in India |
| Thread hygiene | Conversations get closed when they end | Warning. 99.6% of Excom threads are still Open, which spoils reply-time numbers |

### 7.3 Other types

- **Accounts:**
  - a billing email or WhatsApp contact for each customer with dues;
  - payment terms, so that a due date means something. On GGIL there are none, so the due date equals the posting date and "over 90 days" overstates lateness;
  - an approved reminder template on WhatsApp and email, and a statement print format;
  - a way to mark a dispute;
  - the bank feed reconciled recently (41 bank transactions are unreconciled);
  - MSME status in usable fields (on GGIL it sits in orphaned columns that Frappe cannot see).
- **Purchase:** supplier groups (664 of 668 suppliers have none), supplier contacts, buying price history (Standard Buying holds 2,166 prices) and an RFQ mailbox.
- **Compliance:** an owner for every item; a document register installed (docpulse holds GGIL's certificate and licence expiry dates but is not installed on the site); counterparty GSTIN statuses (57 are not Active).

---

## 8. Agent types (capability packs)

### 8.1 Sales (Phase 1)

The keywords choose one or more modes:

| Mode | What the agent does | GGIL starting point |
|---|---|---|
| **Prospecting** | Finds buyers in new markets, researches them, finds contacts, writes and sends sequences, handles replies | Harvey's 677 companies and 335 contacts, imported in Phase 1I |
| **Reactivation** | Proposes check-ins to existing customers who have not ordered for a set number of months, with the current range. Customers owned by people get suggestions to their owner, never direct mail | 586 registered B2B customers; 2 real export customers |
| **Inbound qualification** | For new Excom intake leads: asks the qualifying questions inside the WhatsApp window, fills `customer_type`, then hands to a person. Per `sales_sop.md`, a lead is classified before it becomes an Opportunity | 976 leads in 12 months; 733 at "Responded" after the auto-acknowledgement |
| **Deal support** | Answers price and product questions from the price list and playbook, drafts quotations and follows them up | 66 quotations submitted in 12 months |
| **Order follow-through** | Proforma and advance reminders, production and dispatch updates to the buyer, under Review | 2,036 sales orders in 12 months |

- **Task kinds:** `discover_companies`, `profile_company`, `find_contacts`, `verify_contacts`, `write_sequence`, `check_draft`, `send_due`, `handle_reply`, `qualify_inbound`, `reactivate`, `draft_quotation`, `follow_up_quote`, `order_update`, `weekly_coach`.
- **Targets:** bounce rate, reply rate, positive replies, time to a person's reply for interested buyers, quotations, orders, revenue.

### 8.2 Accounts (recommended first in Phase 2)

On GGIL:
- **Receivables:** ₹14.32 Cr outstanding across 1,605 invoices and 994 customers. ₹8.74 Cr of it is over 90 days, from 172 customers.
- **Dunning:** never used.
- **MSME payables:** ₹1.58 Cr owed to Micro and Small suppliers beyond 45 days (114 invoices, 32 suppliers), a tax-deduction risk under section 43B(h).

The value is large, the contacts are already known, and no cold outreach is involved.

| Work | How |
|---|---|
| Collections ladder | Per customer, not per invoice: statement and a polite reminder, then firmer, then a call request, then the owner. Stops on payment, dispute or promise. WhatsApp utility template or email, to the billing contact only |
| Promise tracking | Reads replies such as "will pay on the 15th", records the promise date, follows up; a broken promise moves to the next step |
| Payment matching | Suggests matches between unreconciled bank transactions and open invoices. Suggestions only |
| MSME payables watch | A weekly internal list for finance of Micro and Small supplier invoices nearing 45 days. No external messages |
| Statements on request | Prepares the customer's statement for Review |

- **Never:** waive, discount, threaten, or post an entry.
- **Promotion:** reminders start in Review and may move to Auto under the promotion rule.

### 8.3 Purchase

On GGIL: 790 purchase orders and ₹67.9 Cr of purchases in 12 months. RFQ, Supplier Quotation and Material Request have never been used.

- **Supplier discovery** for the biggest spend groups (Raw ₹8.5 Cr, Pichkari ₹7.1 Cr, Gadget ₹4.5 Cr): researched supplier drafts.
- **RFQs:** drafted and sent under Review, then followed up. Replies, including PDFs, become Supplier Quotation drafts, using `ai_interface` vision.
- **Comparison** with price history (Standard Buying prices and the `material_price_control` rules).
- **PO chasers:** when a schedule date approaches or passes with no receipt, the supplier is messaged (Review, then Auto).
- **Never:** creates or submits purchase orders, or commits to quantity or price.

### 8.4 Compliance (internal)

- **Due-date calendar:** GST returns, TDS, PF and ESI, ROC filings and licences, with reminders and ToDos for named owners.
- **Document expiry:** certificates and licences. GGIL's register holds BIS certificates expiring on 2026-12-18, an FSSAI licence on 2027-02-15, and a character licence that expired on 2026-03-31.
- **Transaction checks:**
  - pending e-invoices and e-way bills (29 e-way bills are pending);
  - counterparties whose GSTIN is cancelled, invalid or suspended (57), flagged before new invoices;
  - export invoices without a LUT reference.
- **Regulatory watch:** a weekly search for GST, CBIC and DGFT notices on its keywords, summarised for a person to verify.
- **No external messages** by default.

### 8.5 Custom

Built from keywords and a short description, combining up to four abilities:
- **watch:** a scheduled data scan that notifies;
- **answer:** questions on one channel, from a playbook;
- **chase:** a sequence over one record type;
- **draft:** documents or messages.

It is restricted to Review, and to the read and draft tool tiers unless an Excom Admin adds more.

---

## 9. Tools and plugins

Each tool has:
- a **tier:** read, draft, communicate, or paid;
- a monthly budget and a rate limit, with a cost estimate shown before a paid run;
- a fake for tests.

Tool output is untrusted text (§12.7). Harvey's own code is MIT-licensed, and its notice is kept in ported files.

| Tool | From Harvey | Use | Notes |
|---|---|---|---|
| OpenStreetMap (Overpass, Nominatim) | Yes, its default | Local businesses by trade tag and city | Free; only the trades in its tag map |
| Serper | Yes (organic `/search` only) | Keyword and country searches, including Spanish queries | About $0.001 a query (Harvey's estimate). Its places and news endpoints can be added; Harvey never used them |
| Scavio, DataForSEO, TinyFish | Yes | Maps listings, search ranks | Optional, pay per use, behind one discovery interface with `max_spend` |
| Volza import | Yes (local scripts) | Real importers of a product category, by country, from customs data | The strongest source for an exporter |
| CSV and JSON import | Yes (local) | Fair lists, directories | |
| Claude web search and web fetch | No | Research reading without a crawler of our own | Optional server tools (`web_search_20260209`, `web_fetch_20260209`) |
| Website profiler | Yes | Homepage, robots, sitemap, team and careers pages | No AI and no cost; public addresses only, as an SSRF guard |
| Email finder | Yes | MX type, addresses published on the site, address patterns | Never guesses an address another named person could own |
| Verifiers: Reoon, ZeroBounce, Hunter | Yes | Address status: verified, risky, guess, invalid | Through their APIs only. Harvey's raw port-25 probing is not ported: it is blocked on most hosts and harms reputation |
| Precleared | Yes | Per-address consent verdict: OK, consent needed, blocked | Feeds the market rules in §12.6 |
| Mailbox health | Yes (`harvey warmup`) | SPF, DKIM, DMARC, domain age, Spamhaus, SURBL, URIBL, SpamCop | Readiness rule |
| TrulyInbox | Yes | Warm-up status, read only | Optional |
| ERP read | New | Recipes, through the safe query builder | Runs with the agent's permissions |
| ERP draft | New | Leads, Prospects, Opportunities and Quotation drafts | Only through `crm_gateway` and `crm_flow` |
| Excom channels | New | Email drafts and sends, WhatsApp, Instagram and Messenger, notes, ToDos | §11 |
| Documents | New | Quotations, proformas and statements from print formats; reading attachments with `ai_interface` vision | |

**Signals.** Harvey's 29 signal codes were written for selling agency services: search rank, ads, schema markup, startup funding. The observation model is kept, but the seeded codes become trade signals:
- `IMPORTS_CATEGORY` (from Volza), `CARRIES_CATEGORY`;
- `BUYER_TYPE` (importer, distributor, wholesaler, retailer, online store);
- `COUNTRY`, `SIZE_HINT`, `SEASONAL_BUYER`, `WEBSITE_LANGUAGE`, `HAS_ONLINE_STORE`;
- `DECISION_MAKER_TITLE`, `CONTACT_FOUND`, `EMAIL_STATUS`, `PUBLISHED_INBOX`.

As in Harvey, a person confirms which signals the agent may collect before discovery runs.

**Not ported:** LinkedIn automation, job boards and startup signals, Instantly, the Codex provider, Harvey's dashboard and its login, and quota reading from a subscription.

---

## 10. How a task runs

### 10.1 The orchestrator

A scheduler tick every 2 minutes (`excom_ai.engine.orchestrator.tick`) does nothing when the master switch is off. When it is on, for each agent in Shadow or Live it:

1. checks the gates: owner and backup active (§12.3), budget left, office hours for the work in question, channel health (bounce rate, WhatsApp quality rating, mailbox connected), no kill switch;
2. runs the pack's **planner**, which adds tasks for subjects that need work (enrollments due, research backlog, knowledge refresh);
3. enqueues the most urgent tasks, **taking turns between agents**, so a busy agent never starves another one. This fixes Harvey's single global ladder, where one open reply thread stopped prospecting everywhere.

**Event handlers** add tasks straight away: an inbound message on a thread an agent handles (debounced 60 seconds, so a buyer's three quick messages are read together), a bounce, an opt-out, a quotation opened or accepted, an invoice paid, a purchase order received.

**Priorities, for each agent:** 0 inbound replies and events · 1 approved actions to execute, and due sends · 2 checks of drafts · 3 writing · 4 research · 5 discovery · 6 knowledge refresh and coaching.

### 10.2 One task, start to finish

1. **Load.** The agent, its approved brief and playbooks, the knowledge sections the task needs, the subject and its records, and for conversations the whole Excom thread across channels (email, WhatsApp, call notes).
2. **Assemble the prompt** in a fixed order, so the stable part is cached (§10.3):
   - the pack's system prompt and the global rules (identical for every agent of a type);
   - the agent's brief and approved playbook sections (identical for every task of that agent);
   - the knowledge slice and subject context;
   - the task instruction and its output schema;
   - untrusted text (web pages, the buyer's messages) last, fenced and labelled as data.
3. **Call Claude** through `ai_interface`, with structured output against the task's JSON schema. Research and reply tasks may use tools in a bounded loop: at most 6 tool calls and a time and cost budget per task.
4. **Validate.** The output must match the schema. One repair attempt, then the task fails and says why.
5. **Code checks** (§12.5), then the **AI checker** where the pack asks for it.
6. **Outcome.** Write records, create a proposal, or execute directly if the policy for that action is Auto. Usage is counted, and the audit row records the prompt versions, model, tool calls and checks.

Tasks are idempotent: a `dedupe_key` per task and one proposal per (agent, subject, step). A per-subject lock stops two tasks from replying to the same thread at once. Transient errors retry with backoff. After 5 consecutive failures an agent goes On Hold and its owner is told.

### 10.3 Models, effort, caching and structured output

These follow the current Claude API reference (checked 2026-11-08):

- **Model.** `claude-opus-5-5` for every task by default. The model is a setting per task kind, so the owner can move high-volume work (reply classification, extraction) to `claude-sonnet-5-5` or `claude-haiku-5-5`. That should only happen after the evaluation set (§13.4) shows quality holds. Lowering effort on the same model is the first lever to try, because prompt caches belong to one model and a mix of models loses cache reuse.
- **Effort.** On Opus 5.5 thinking is always on, and effort (`output_config.effort`) is the control; its default is `medium`. Use `low` for classification and extraction, `medium` for routine drafts, `high` for briefs, replies to buyers and quotations.
- **Prompt caching.** Each task resends the persona context, which the abstract listed as this design's main cost. Caching removes most of that cost. Mark the end of the pack's system prompt and the end of the agent's brief and playbooks with `cache_control`, using a 1-hour lifetime because an agent's tasks come in bursts. Cache reads cost a twentieth of normal input on Opus 5.5 ($0.20 against $4 per million tokens). Keep timestamps, IDs and anything variable after the cached part, and watch `cache_read_input_tokens` in the usage log to catch silent cache breaks.
- **Structured output.** `output_config.format` with a JSON schema, or `client.messages.parse()`. Opus 5.5 rejects forced tool choice (`tool_choice` any or tool), so the engine never relies on it; tools use `strict: true`.
- **Refusals.** Always check `stop_reason` before reading the content (`refusal`, `max_tokens`). Enable the server-side fallback (`fallbacks: "default"`) so a declined request is retried automatically.
- **Bounded tool loops.** Research and reply tasks use the SDK's Tool Runner (`client.beta.messages.tool_runner`) with our own tools. Most other tasks are a single call; the engine owns the flow.

### 10.4 Night work at half price

Work that is not urgent goes through the **Message Batches API**, which costs half and returns within hours: company research, first drafts for tomorrow's sequence steps, knowledge summaries, the weekly coach. Replies, sends and anything a person is waiting for never wait for a batch.

### 10.5 Considered and not chosen: Managed Agents

Anthropic's Managed Agents run the agent loop and a sandbox on Anthropic's side. They are not the right fit here. The tools, the data, the approvals and the audit all live in ERPNext, and every action must run as the agent's service user under Frappe's permissions. The engine therefore owns the loop and calls the Messages API. This can be revisited for long, open-ended research jobs.

### 10.6 Credentials and providers

- Agents never hold AI credentials and never name a provider. They call `ai_interface` with a `calling_app` (excom), a reference (the Excom Agent Task), an action and the capabilities they need. `ai_interface`'s routing rules pick the provider and model; its budgets and spend caps apply.
- **On Frappe Cloud** the Claude command-line provider cannot run: no `claude` program, no logged-in account. Production therefore routes to an API provider (an Anthropic API key, ideally in its own workspace).
- The **Auth Token** mode (a subscription login sent to the API under Claude Code's identity) should not carry automated production work. Whether it is allowed at all is a question for Anthropic's terms.

---

## 11. Channels

Agents send and receive only through Excom, as their service user, so every message lands on the buyer's conversation.

### 11.1 Email

What already exists (built 2026-11-08):
- **Proposals are mailbox drafts.** An email proposal is an Excom Email Draft, and approving it sends that draft.
- **Send windows** use scheduled send.
- **Threading:** follow-ups thread through In-Reply-To and References (`outbound.reply_headers`).
- **Message-ID:** our own on JMAP; read back on Gmail.

To build in Phase 1B:
- **Inbound trigger.** `mailbox.ingest` must call the `excom_inbound_handlers` hook. Today only the WhatsApp webhook does, so no agent would notice an email reply.
- **Bounce notices.**
  - Detect delivery reports in ingest (`multipart/report`, or messages from MAILER-DAEMON or postmaster).
  - Match them by Message-ID and by the failed address, as Harvey's `bounced_recipient` does.
  - Mark the Lead's `email_status` invalid and stop its sequence.
  - Never create a person or a conversation for a bounce.
- **Unsubscribe.**
  - Add `List-Unsubscribe` and one-click `List-Unsubscribe-Post` headers to cold mail on both providers.
  - Add a personal unsubscribe link that writes the consent ledger through `api/unsubscribe`.
- **Photo cards:** product photos in emails 1 and 2 (a Harvey local change), built from Item images.
- **Pacing:** caps per mailbox, a warm-up ramp (start at 5 a day, add 3 a day, at least 14 days) and jittered sends (at most 8 per run, 4 to 15 seconds apart).

### 11.2 WhatsApp

- **Replies:** inside the 24-hour window, through `send_message`.
- **First contact:** only by template to opted-in contacts, through `send_template_to_thread`; `assert_template_allowed` already guards consent there.
- **Free-text sends:** these do not check the opt-out flags today. The executor checks consent on every channel before anything goes out.

### 11.3 Instagram and Messenger

- **Window:** replies inside the messaging window only.
- **The `HUMAN_AGENT` tag:** the existing `send_text` quietly adds this tag outside the window, and Meta allows it for human agents only. The executor uses a send that refuses outside the window, so an AI reply can never carry it.
- **Meta's AI rule:** HLD-005 notes that Meta's restriction on AI providers targets general-purpose assistants; a business using AI with its own customers is fine.

### 11.4 Voice (Phase 3)

- **Routing:** `routing.build_decision` gets an "AI agent" destination.
- **Audio:** a small service on the VM streams it, using the persona's voice and language.
- **Records:** transcripts and summaries fill the Excom Call fields that already exist and are unused today: `transcript`, `summary`, `sentiment` and `next_action`.
- **Order:** inbound calls first; outbound only to buyers who asked for a call.

### 11.5 Web chat and intake

- **Web chat:** needs the same inbound trigger as email.
- **New enquiries:** after `services/intake.py` sends its auto-acknowledgement, a new enquiry becomes an inbound-qualification task for the Sales agent that owns its desk.

### 11.6 Who owns the conversation

- **The problem:** sending as the service user would hand unowned threads to it, because `_claim_on_talk` and `_auto_claim_thread` claim for the sender.
- **The rule:** the executor sets `handled_by_agent` and keeps `assigned_to` as the agent's owner. Replying still claims the chat and the lead, but for the owner and the owner's desk, as `sales_sop.md` requires.

### 11.7 Assist for people

The same engine replaces the stub behind `get_ai_suggestions` with real suggested replies and summaries for people's own conversations, keeping the response shape the screens already use. It also fills `Omni Identity.ai_profile_summary` and the call summaries. It is the first thing people will notice, and it exercises the whole AI path before any agent writes to a customer.

---

## 12. Autonomy, ownership and safety

### 12.1 Policies per action

| Action | Sales default | Highest allowed |
|---|---|---|
| Create or update Prospect, Lead, Observation | Auto | Auto |
| Draft and check messages (internal) | Auto | Auto |
| First cold email | Review | Auto, after the promotion rule |
| Follow-up email in a sequence | Review | Auto, after the promotion rule |
| Reply to a buyer (email, WhatsApp, Instagram, Messenger) | Review | Auto for safe intents only: out-of-office, thanks, scheduling, sending a public catalogue |
| WhatsApp template (opted-in contacts only) | Review | Review |
| Prices, freight, samples, discounts, payment terms, delivery dates | Prepared for a person | Prepared for a person |
| Create Opportunity | Auto, with an alert to the owner | Auto |
| Quotation | Draft only, a person sends it | Draft only |
| Submit any document, post any entry | Never | Never |
| Payment reminder (Accounts) | Review | Auto, after the promotion rule |
| ToDo, note, comment | Auto | Auto |

**Modes:**
- **Off:** the action is not done.
- **Shadow:** the action is prepared and recorded, nothing leaves the company, and the outcome is compared with what people did.
- **Review:** a person approves each one.
- **Auto:** executed directly; a sample is reviewed afterwards, and daily caps apply.

**Promotion rule** (per agent and action): at least 50 reviewed proposals, a rejection rate of 5% or less over the last 14 days, no failed code check that reached a person, and no complaint. The system suggests the promotion; the owner switches it.

**Automatic demotion** to Review: bounce rate above 5% in a day, any spam complaint, a failed code check on an Auto action, a rejection spike in sampling, or the agent going On Hold.

### 12.2 Hard limits enforced in code

- Daily caps per channel and per agent; warm-up for new mailboxes; random gaps between sends.
- Send windows of 08:00 to 17:00, Monday to Friday, in the recipient's timezone, with no sends on quiet dates.
- One agent owns a company. Existing customers, open opportunities owned by people, do-not-contact addresses and domains, and competitors are excluded from outreach.
- Opt-outs are honoured on every channel at once, through the consent ledger.
- A recipient must be the task's subject. Text in a web page or email can never add one.
- Prices must come from the agent's price lists and tiers; a mismatch blocks the proposal.
- Banned claims block the proposal. Every cold email carries the postal address and the opt-out line.
- No money, legal or delivery commitments. No submitted documents.

### 12.3 Owner and backup

The rule is **authority = the lower of the agent's policy and its owner's status.**

- **Required people.** An agent cannot go past Draft without an owner and a backup who are active users.
- **Automatic detection.**
  - Owner disabled, or employee status Left: the backup takes over at once and the manager is told.
  - Both gone: the agent goes On Hold.
  - Approved leave: the backup covers for those dates.
  - **Dead-man switch:** if neither owner nor backup has approved, reviewed or acknowledged the digest for 2 working days, the agent goes On Hold.
- **On Hold:**
  - Stopped: new outreach, follow-ups, outbound WhatsApp and calls, and all Auto actions.
  - Still running: bounces, opt-outs and out-of-office handling.
  - A buyer who writes gets one polite acknowledgement ("thanks, we'll come back to you shortly"). The message then goes to the escalation user with a deadline.
- **Handover** is one action: a manager picks the new owner, and Excom writes a note for each live buyer: where things stand, what was promised, and what is due.
- **Buyers belong to the company.** The mailbox, numbers, conversations, quotes and call notes are company records, so nothing leaves with an employee.

### 12.4 Kill switches

- **Per agent:** a Stop button on the agent card.
- **Per type** and **global:** in settings.
- **From the phone:** the owner's Stop is one tap on the agent card in the Excom PWA.

Stopping cancels queued tasks and pending sends and leaves drafts in place.

### 12.5 Code checks before anything leaves

- the recipient matches the subject, and is not opted out, on do-not-contact, a customer, or owned by another agent;
- the channel is allowed and healthy, and the send time is inside the window;
- prices and terms match the agent's price lists, tiers and payment terms;
- no banned claims or phrases;
- the language matches the market, and is the same across a whole sequence;
- length limits, and at most one link;
- postal address and opt-out present on cold email; disclosure present where required;
- no unrendered placeholders and no sign-off when the mail server adds a signature.

Each result is stored on the proposal, so a reviewer sees why something passed or failed.

### 12.6 AI identity and law

**The owner's decision (2026-11-09):**
- Agents do not volunteer that they are AI.
- When a buyer asks whether they are talking to a bot, an AI or a real person, the agent does not answer the question. It hands the conversation to its owner with a neutral holding line, for example "Let me bring in my colleague, who can help you with that." This is Harvey's current behaviour.

**What the code enforces regardless of settings:**
- **Never human, never denied.** An agent never states or implies that it is human, and never denies being AI. A draft that does fails the code checks, and the persona prompt says the same.
- **The question always reaches a person.** "Are you a bot / AI / real person" (and its translations) is a fixed escalation intent, detected by rule before the AI classifies the message. The thread moves to "Needs person", and the owner is alerted with the question highlighted. What to answer is the person's call.

**Notes:**
- **Revisit before going live:** some places require AI disclosure, for example California's bot law for sales conversations and the EU AI Act's transparency duties. The agent's markets decide whether this policy can stay. Voice (Phase 3) is the most likely to need disclosure.
- **Market rules:** each market can carry its own rules in the playbook's Compliance section, for example consent rules for commercial email to individuals.
- **Legal review:** have the policy and market rules checked by someone qualified. This document flags the issue; it is not legal advice.

### 12.7 Untrusted text

Web pages, emails, chat messages and attachment text are passed as fenced, labelled data after the instructions. They cannot add recipients, change tools or alter policy, because those come only from records and code. Anything that looks like an instruction inside such text is logged as a prompt-injection attempt.

---

## 13. Monitoring, evaluation and audit

### 13.1 Live view, per agent

- Today's work: tasks done and failed, proposals waiting, approvals and rejections with reasons.
- Channel results: sends, bounces, replies by intent, interested buyers waiting for a person (with a timer against the 4-working-hour target).
- Spend: AI and tool usage against budget.
- Health: mailbox connected, bounce rate, WhatsApp quality rating, and the mode of each action.

### 13.2 Alerts

Sent to the owner and backup, and the manager where needed, by Excom notification, push and WhatsApp to the owner's own number:

- an interested buyer is waiting;
- the reply target was missed;
- the agent went On Hold;
- budget is at 80%;
- a bounce spike;
- an automatic demotion;
- a readiness rule is failing again;
- a channel went unhealthy.

### 13.3 Digest and coaching

- **Morning digest:** yesterday's numbers and today's to-do for each owner.
- **Weekly coach report:** what worked by market, subject line and product. Its suggestions arrive as playbook changes the owner accepts or rejects.

### 13.4 Evaluation sets

Each pack has a golden set, re-run before any prompt, model or effort change goes live. A change that scores worse does not ship.
- **Sales:** Harvey's 41 processed replies with their correct intents, 20 company profiles, the Pool Barn price question.
- **Accounts:** sample payment replies, such as a promise, a dispute or "already paid".

### 13.5 Audit trail

- **Per proposal:** inputs (record references), prompt versions, model and effort, tool calls, check results, approver, edits and the executed record.
- **On the record itself:** a short comment on the subject's timeline, for example "Harish (AI) sent step 2, approved by Ankur".
- **Raw prompts and outputs:** kept for `prompt_retention_days`, then deleted. The audit row stays.

---

## 14. Permissions and service users

### 14.1 Roles

Excom has two roles today, **Excom Admin** and **Excom Agent**, after the two-role patch. Team membership carries the rest (each member of an Excom Team is a Member or a Manager). The plan adds no new human roles:

- **Excom Admin:** settings, credentials, types, budgets, blocker waivers and the global kill switch.
- **Manager of an Excom Team:** creates agents for that team, approves briefs and playbooks, picks owners and promotes policies.
- **Owner and backup:** users with the Excom Agent role who are members of the agent's team.
- **Reviewers:** members of the team the owner lists. They approve proposals.

§20 asks to confirm this.

### 14.2 Service users

Each agent acts as its own Frappe user, for example `harish.ai@<company domain>`:
- it has no password and no desk login;
- it is a member of the agent's team, so Excom's visibility rules apply to it as to a person;
- its permissions come from one role per pack (Excom AI Sales, Purchase, Accounts or Compliance), each granting only what that pack needs: Sales may create Leads, Prospects and Opportunities and draft Quotations, but never submit.

The engine's tool tiers check the same limits in code, so a permission mistake alone cannot widen an agent's reach.

---

## 15. Screens

The Excom app gets an **Agents** item in the left rail for anyone with an agent role, and an Agents entry on the phone's More page.

1. **Agents home.** One card per agent: avatar and persona, type, owner, status, mode, today's numbers (sent, replies, waiting for you), a health dot and a Stop button. A **New agent** button opens the builder.
2. **Builder** (§6.2). Works on a phone as a step-by-step flow.
3. **Agent page** tabs:
   - **Overview:** targets, funnel, spend.
   - **Needs you:** this agent's proposals and alerts.
   - **Activity:** tasks and results.
   - **Brief.**
   - **Playbooks.**
   - **Knowledge:** the snapshot, what changed, and a Refresh button.
   - **Readiness.**
   - **Channels and limits.**
   - **Autonomy:** policies and promotion suggestions.
   - **Usage.**
4. **Needs you** (all agents), grouped by urgency:
   - interested buyers waiting for a person;
   - proposals to approve, with a preview exactly as the recipient will see it, inline edit, approve or reject with a reason, and bulk approval per agent and step;
   - held items and readiness blockers.

   It is fully usable on the phone, so approvals happen on the move.
5. **Inbox integration.**
   - **Thread row:** an "AI" mark when an agent handles the conversation.
   - **Header:** "Handled by Harish (AI) · Take over".
   - **Composer:** its AI notice reads the real `agent_mode`.
   - **Inline proposals:** an agent's proposal appears at the foot of the conversation with Approve, Edit and Discard.
   - **Activity:** shows what the agent did.
6. **Settings** for admins: models, effort, budgets, tools and keys, global rules and type defaults.

---

## 16. Build plan

**Every step:**
- tests with fakes (AI, search, verifiers, mailbox; the JMAP fake from 2026-11-08 is reused), added to the default list in `excom/excom/tests/run.py`;
- migrate and a review pass;
- committed to `main` and pushed once verified;
- shown to the owner before the next step starts.

Sizes are relative: S, M or L.

### Phase 0: foundations (no agent talks to anyone yet)

| Step | Builds | Done when | Size |
|---|---|---|---|
| 0.1 | `ai_interface`: merge `somil-dev`, then the generic additions in Appendix B.2; `ai_interface` installed on the production site (it is not installed on GGIL); an API provider and budgets configured | A structured call with caching, effort and refusal handling passes against the live API and is logged with its reference | M |
| 0.2 | The Excom AI module, laid out like `excom_ads`: settings, the records in §5.1 and §5.2, roles, service-user creation, patches | Records can be created; the master switch is off by default and costs nothing | M |
| 0.3 | The engine: orchestrator, tasks, runner, proposals, executor, budgets, ownership rules, audit, kill switches; the `long` queue or a dedicated one | An "echo" agent runs end to end in Shadow on a test site, with a proposal, usage and audit rows | L |

**As built (2026-11-09, Phase 0.2 and 0.3):** `excom/excom_ai/engine/` holds the registry (task kinds, planners, executors), the tick (every 2 minutes; while the master switch is off it reads one setting and returns), dispatch (turns between agents, each task claimed once by a conditional update, run on the `long` queue as the agent's own user), the runner (gates checked again at start, retries at 2, 4, 8 minutes, hold after failures in a row), proposals under policy (Shadow / Review / Auto with sampling and daily caps; a bad sample sends the action back to Review), ownership (backup takes over, both gone or the dead-man switch puts the agent On Hold; resume re-opens held actions for a person) and prompt assembly with cached system blocks and fenced untrusted text. `excom_ai/api.py` has Stop, Resume, Acknowledge, Decide and Run self-test. The built-in `echo` kind is the self-test; `excom_ai/tests/test_engine.py` runs it end to end through `ai_interface` with a scripted provider.

The second review (same day) also added: team scoping of agents, proposals, sequences and feedback (`excom_ai/permissions.py`); a service user belongs to its agent's team only; editing the brief clears its approval (Live goes back to Shadow); Custom agents work in Review at most, and replies go out alone only when the policy condition names safe intents only; On Hold and Retired always save; one proposal per sequence step and one open enrolment per lead are unique keys in the database (an Expired proposal frees its step); recipes read only an allowlist of business doctypes, with every field, filter, order and grouping checked; AI service users are never rung, notified or listed in reports.
| 0.4 | Excom plumbing: the inbound trigger for email, Instagram, Messenger and web chat; consent checks on free-text sends; the window-only Meta send; thread fields; the ownership rule in §11.6 | Existing tests pass, plus new ones for each gap | M |
| 0.5 | Assist for people (§11.7) | Real suggestions and summaries appear in the inbox | S |

### Phase 1: the Sales agent

| Step | Builds | Done when | Size |
|---|---|---|---|
| 1A | The builder for Sales: glossary, recipes, knowledge pack, brief, readiness rules; the Agents screens | `export, water guns, latin america, spanish` produces §17 on a copy of GGIL | M |
| 1B | Mail for agents (§11.1): bounces, unsubscribe headers and link, photo cards, caps, warm-up, jitter, the mailbox-health check | A test mailbox on Stalwart sends and receives; a bounce and an unsubscribe work end to end. This is where the deferred Stalwart test happens | M |
| 1C | Discovery and research tools (§9) with trade signals; Prospects and Leads created with exclusions | 50 Latin American importers found with honest email statuses and no conflict with existing customers | L |
| 1D | Writer, code checks, AI checker, the Needs-you queue on desktop and phone, bulk approval, the Shadow comparison | Drafts for 10 real buyers pass the checks and the owner's reading | M |
| 1E | Sequencer and sender: enrollments, company-level stop, windows, caps, follow-up order, bounce pause | A dry run matches Harvey's schedule for the same leads | M |
| 1F | Responder, WhatsApp text, inbound qualification; interested buyers become Opportunities through `crm_flow`, classified first; owner alerts | Harvey's 41 processed replies are routed as well or better | M |
| 1G | Deal support: quotation drafts, quote follow-ups, reactivation, order updates, all under Review | A price question gets a correct draft with prices from the price list, like the Pool Barn reply | M |
| 1H | Monitoring: live view, alerts, digest, sampling, evaluation sets, promotion and demotion, dead-man switch, hold, handover | The owner leaving and the owner going quiet are both tested; the evaluation set blocks a worse prompt | M |
| 1I | Harvey import and cut-over (HLD-006 §11): companies, contacts, the 167 approved and 27 pending emails, conversations, opt-outs, do-not-contact; Harish becomes an agent | Counts match; Harvey stays off; follow-ups continue from ERPNext | M |

### Phase 2: the generic builder and other types

| Step | Builds | Done when |
|---|---|---|
| 2A | The builder for every type, Custom agents, and AI-proposed recipes for unknown keywords | A Custom agent is built from keywords and runs in Shadow |
| 2B | Accounts pack (§8.2) | The collections ladder runs in Review for the 90+ day list; the MSME watch list reaches finance weekly |
| 2C | Purchase pack (§8.3) | An RFQ is drafted, sent and its replies parsed into Supplier Quotation drafts |
| 2D | Compliance pack (§8.4) | The due-date calendar and document expiry raise ToDos for named owners |

### Phase 3: voice

The VM voice service (§11.4). Inbound calls first.

### Phase 4: learning loop

Coach suggestions become playbook versions; subject-line tests; cost tuning against the evaluation sets.

### In parallel: the readiness sprint for people

These are data fixes no code can make, listed in §17.4. They can start now, independent of Phase 0.

---

## 17. Walkthrough on GGIL data

A manager presses **Sales** and types `export, water guns, latin america, spanish`. The numbers below were read from the GGIL copy on 2026-11-08.

### 17.1 What the keywords find

| Keyword | Understood as | What the ERP shows |
|---|---|---|
| export | Built-in concept | Only one real export relationship is substantial: Color Blaze Supply (US, USD 196k). There is one more real export customer (Running Imp, UK, GBP 50k), and the sister company GGIL Export (US) nets to about zero.<br>Export invoices: 11 ever; 6 in the last 12 months, worth ₹2.02 Cr, almost all Gulal.<br>Terms: one USD price list, for one customer; no payment terms templates; incoterms used on 2 quotations.<br>Logistics: FedEx is the only carrier.<br>Leads: 46 tagged Export Sales, 37 of them in India |
| water guns | Glossary: item group **Pichkari** (the AI proposes it, a person confirms it once) | 189 items; ₹4.2 Cr sold in 12 months to 153 customers, all in India, none exported. 18 items have photos, 6 have weights, all have an HS code |
| latin america | Region: Mexico, Central America, Colombia, Venezuela, Ecuador, Peru, Bolivia, Chile, Argentina, Uruguay, Paraguay, Brazil | No customers and no leads. Two leads are from Spain |
| spanish | Language `es`, its markets and timezones | No customer has a language set. The builder points out that **Brazil speaks Portuguese**, and asks whether to drop it or add Portuguese |

### 17.2 The draft agent (excerpt)

> **What this agent is.** An export sales agent for Garg Global's water guns in Spanish-speaking Latin America. It finds importers, distributors and wholesalers of toys and seasonal goods, writes to them in Spanish, answers their questions from the approved playbook and the export price list, and hands every price negotiation, sample request and order to its owner.
>
> **Audience.** No customers in these markets yet, so this is not a lookalike: importers of the category found in customs data (Volza) and by Spanish searches, sized as distributors and wholesalers. *Source: market data; AI suggestion, please check.*
>
> **Tone.** Warm, direct, manufacturer to buyer, formal Spanish (*usted*). The export desk has only 13 export conversations, too few to learn from, so this is the type's default plus Harvey's approved Spanish style. *Source: type default, Harvey playbook.*
>
> **Price basis.** None yet: no USD price list for Pichkari. *Blocker, see readiness.*
>
> **Channels.** The export mailbox, "GGIL Export Sales" (exists, not yet connected), and the export WhatsApp number "GGIL Export WB" (templates to opted-in contacts only). *Source: Excom Channel Accounts open to the Export Sales team.*

### 17.3 Readiness result

- **Blockers:** price basis, payment terms, product facts, freight and customs, LUT, playbook (§7.2).
- **Warnings:** incoterm, certificate register, lead hygiene, thread hygiene, no audience to model on.

The agent may run in Shadow to prepare, but cannot go Live.

### 17.4 The readiness sprint (people's work, ToDos created by the builder)

1. An export price list in USD for the Pichkari models to offer, with quantity tiers. Start with the 18 that have photos.
2. A Payment Terms Template for export, for example 30% on booking and 70% on dispatch, as offered to The Pool Barn.
3. Photos, carton size and weight for those models.
4. At least one freight forwarder and one customs agent on record, and reference freight rates to the main Latin American ports.
5. A home for the LUT number and expiry: a field on Company, or the document register.
6. The playbook's Certifications and Banned claims sections. Start from Harvey's `product_knowledge` and `competitive_intel` files, and record only certificates the products actually hold, with their expiry.
7. The export mailbox on Stalwart, with SPF, DKIM and DMARC, warmed for 14 days.
8. Re-tag the 37 Export Sales leads that are in India.

### 17.5 What the agent does first, in Shadow

- builds its exclusion list;
- drafts the FAQ from the 13 export conversations and Harvey's Pool Barn reply;
- researches 50 Latin American importers (Volza and Spanish searches) and finds their contacts;
- writes Spanish sequences that the owner compares with what they would have sent;
- proposes check-ins with the 2 real export customers, as suggestions to their owners only;
- measures its cost per task.

Harish, the English-market persona Harvey runs today, arrives the same way, with his history imported in Phase 1I.

---

## 18. Capacity and cost

### 18.1 Capacity

- **Load:** about 4 agents is light. Each task mostly waits on the AI service, and the queue runs 2 workers in turn. On Frappe Cloud the agents use the `long` queue, or a dedicated queue if the plan allows custom workers.
- **Job length:** a research task is kept short (bounded tools, a time budget) so it never holds up ERPNext's own jobs.

### 18.2 AI cost, estimated, to be measured in Shadow

Assumptions: `claude-opus-5-5` at $4 per million input tokens, $20 per million output tokens and $0.20 per million cached tokens; brief and playbooks cached; thinking billed as output.

| Task | Rough tokens | Rough cost |
|---|---|---|
| Research a company (2 to 3 pages read) | 15k in, 2k out | $0.10 |
| Write a 3-email sequence | 8k cached, 4k in, 3.5k out | $0.09 |
| Classify a reply and draft an answer | 6k cached, 3k in, 1.5k out | $0.04 |
| Quotation draft | 8k cached, 5k in, 2k out | $0.06 |

- **Per new buyer**, all in: about $0.25 to $0.35.
- **Example volume:** 40 new buyers a day for one agent comes to about $10 to $14 a day. The batch discount takes about a third off that, and lower effort or a smaller model (§10.3) takes off more once the evaluations allow it.
- **Harvey's measured figure:** $0.19 per writer call, about $56 for 289 calls in a week.
- **Discovery and verification tools:** metered per search and per address, with monthly budgets per tool.

The Shadow phase replaces these estimates with measured cost per task from the usage log.

---

## 19. Risks

| Risk | Mitigation |
|---|---|
| The sending domain's reputation is damaged (Harvey ran at an 11% bounce rate with its pause switched off) | Verified addresses only, a 5% bounce pause, warm-up, DNS checks in readiness, and a separate sending subdomain for cold email |
| Invented facts or prices | Prices and terms checked by code against the ERP; every brief line sourced; the AI checker; human review until the numbers say otherwise |
| Money, legal or delivery commitments made by an AI | Forbidden by policy and blocked by code checks; drafts only, people send |
| An agent keeps running after its owner leaves | Owner and backup required; dead-man switch; On Hold behaviour (§12.3) |
| Cost overrun | Daily and monthly budgets per agent and overall; caching; batches; effort tuning |
| Prompt injection from web pages or emails | Fenced data, recipients and tools only from records, injection logging |
| Data from one agent leaking into another | Stateless tasks built from one agent's scope; service users bound to teams |
| Cold-contact law and AI disclosure differ by market | Market rules in playbooks, disclosure lines, opt-out on every channel, qualified review of wording |
| WhatsApp number quality falls | Templates only to opted-in contacts, quality monitoring, automatic pause |
| Depending on a subscription login for automation | API key in production (§10.6) |
| Excom grows harder to maintain | A separate module behind a master switch, its own tests, no changes to core flows beyond the hooks listed in §11 |

---

## 20. Decisions

**Decided by the owner on 2026-11-09:**

1. **Mail** goes through Excom's mailboxes (Stalwart over JMAP, or Gmail). This changes HLD-006 decision 1.
2. **AI** goes 100% through `ai_interface`, which grows generically into the AI mediator for every app (Appendix B). This changes HLD-006 decision 2.
3. **AI identity.** Agents do not say they are AI, even when asked. They hand the question to a person instead (§12.6). The code still guarantees an agent never claims to be human or denies being AI.
4. **Data.** Like Harvey, agents keep their own research data (companies, people, locations, signals). Native ERPNext records are used where they fit, with no duplicated data. The full fields are in Appendix C.
5. **Scope.** All of Harvey's features, for sales.

**Open:**

6. **`ai_interface` branch.** Merge Somil's `somil-dev` branch and build on it (recommended), in agreement with Somil (Appendix B.1).
7. **Default model.** `claude-opus-5-5` for every task, effort tuned per task; cheaper models per task only after the evaluations allow it. *Recommended.*
8. **Phase 2 order.** Accounts, Purchase, Compliance, Custom. *Recommended* (§8.2).
9. **Owner leaves or goes quiet.** On Hold, and handover to the backup (§12.3). *Recommended.*
10. **WhatsApp first contact.** Opted-in contacts only. *Recommended.*
11. **Roles.** No new human roles (§14.1). *Recommended.*
12. **Sending domain.** A separate subdomain for cold email. *Recommended.*
13. **Harvey until cut-over.** Stopped since 2026-11-08 14:39, with 167 approved emails waiting.
    - **Keep it stopped**, or
    - **restart it with safe settings:** bounce pause at 5%, no sending to unverifiable addresses.

    *Recommended: restart with safe settings.*
14. **The readiness sprint.** Who owns each item in §17.4.

## Appendix A — Harvey to Excom AI

From a read-only inventory of `~/harvey` on 2026-11-08: the code, the live database and the uncommitted local changes.

| Harvey | Excom AI | Change |
|---|---|---|
| Heartbeat: every 15 minutes, one global ladder (replies, then send, then write, then prospect, then idle) | Orchestrator (§10.1) | Priorities per agent with turns; one busy agent never stops the others |
| Discover: providers `osm`, `serper` (organic only), `scavio_*`, `dataforseo_*`, `tinyfish`, presets; cost estimate and `max_spend` | Discovery tools (§9) | Same providers behind one interface, with budgets per tool and per agent |
| Profile: homepage, robots, sitemap, team and careers pages, job-board feeds; no AI | Website profiler | Job-board feeds dropped |
| Scout: owners first, at most 2 per company, published inbox as fallback; inline AI scoring | `find_contacts`, `verify_contacts` tasks | Scoring prompt versioned; raw port-25 probing dropped |
| Email finder and verifiers: MX type, patterns, Hunter, Reoon, ZeroBounce | Same, through their APIs | Statuses kept: verified, risky, guess, invalid |
| Precleared consent check | Same | Per market, in the code checks |
| Signals (29 codes, aimed at agency selling) and cohorts (a filter the Writer ignored) | Observations with trade signals; the agent's scope and audience drive planning | Cohorts become real targeting |
| Writer: industry batches; emails 2 and 3 written once per batch; `direct_offer` first email; word limits 90, 80, 45 | `write_sequence` per buyer | Fixes a Spanish buyer getting English follow-ups. The first-email style and word limits become agent settings |
| Pre-send gate: recipient, do-not-contact, email status, subject up to 90 characters, body up to 220 words, one link, no HTML, merge tags, banned phrases, postal address | Code checks (§12.5) | All kept; limits per agent |
| Autopilot review: links outside allowed domains, 12 or more words quoted from prompts or skills | Code checks | Kept |
| Sender: claim (`approved` → `sending` → `sent`); "may have gone out" never retried; at most 8 per run with 4 to 15 second gaps; follow-up order; recipient's send window; warm-up ramp; bounce pause after 10 sends | Executor and sequencer | Kept; the bounce pause becomes mandatory (Harvey's local setting had switched it off) |
| Handler: intents `interested`, `question`, `objection`, `negotiate`, `not_interested`, `unsubscribe`, `escalate`, `ooo`, `wrong_person`; keyword rules before the AI; price talk goes to a person; out-of-office restores the queue; a reply stops the whole company | Responder (`handle_reply`) | Same intents and rules. Replies are matched by Excom identity and thread, not only by From address |
| Analyst: funnel and insights to a JSON file | Monitoring and the weekly coach (§13) | In ERPNext reports |
| Trainer: crawls the company's own site into `product_knowledge` and `competitive_intel` | Builder reads Items and price lists; optional website crawl into playbook drafts | Facts come from the ERP |
| Skills: Markdown files concatenated into prompts | Method stays in pack prompts; company facts move to playbooks | Harish's first playbook is built from his two generated skill files |
| Dashboard: Today, Outbox (J, K, A, E, R keys, Approve all, preview with photos), Conversations (hand back), Signals, Discover | Agents screens and Needs you (§15) | Keyboard shortcuts kept |
| Claude command-line program in an isolated subprocess; quota from the subscription | `ai_interface` with an API key; budgets from the usage log | §10.6 |
| LinkedIn, job boards, startup signals, Instantly, Codex | Not ported | |
| Local customisations: photo cards, server-signature stripping, Spanish opt-out line, *usted*, `negotiate` intent, bot-question escalation | Agent and playbook settings | The "never mention AI" rule waits on decision 6 |
| Tables: `companies`, `prospects`, `observations`, `email_patterns`, `campaigns`, `outbox`, `conversations`, `actions`, `usage_events`, `settings` | Prospect, Lead, Excom Agent Observation, Excom Email Domain, Excom Agent Sequence and Enrollment, Proposal or Excom Email Draft or Excom Message, Excom Thread, audit, `ai_interface` call log, agent fields | Imported in Phase 1I (Appendix C) |

**Harvey's own issues, fixed in the port:**
- the global priority stall;
- batch-written follow-ups in the wrong language;
- cohorts ignored when writing;
- replies matched by sender address only;
- the bounce pause switched off;
- unused prompt files (`prompts/scout.md`, `prompts/system.md`);
- skills loaded but never used by the Scout and the Sender.

## Appendix B — `ai_interface` as the AI mediator for every app

**Decision (2026-11-09):** every AI call from every app goes through `ai_interface`. What the agents need is added there in a generic way, named for what it does and not for Excom, so that any app (excom, docpulse, business_needed_solutions, insights) can use it.

**Status (2026-11-09): done.**
- `somil-dev` is merged into `main`, which is now the only branch.
- Everything in B.2 is built on top of it, with tests (169 passing) and handbook sections.
- Commits: 71d8af7, 5a0b22c and 758c048 in `ai-interface`.
- Not yet verified against a live provider: no provider is configured on the local sites.

### B.1 Where to build: the `somil-dev` branch

`ai_interface`'s branches are:

| Branch | State |
|---|---|
| `develop` | The default branch, and what the bench has checked out (08c64e7) |
| `main` | One documentation commit ahead of `develop` |
| `somil-dev` | 15 commits by Somil Vaishya (September 2026), about 8,300 lines, not merged |

`somil-dev` already provides most of the mediator:
- provider configuration as data (AI Provider Type, an OpenAI-compatible adapter);
- **routing** by caller, template and rules, with a **capability filter** (vision, tools, context window) and fallback across providers (retry on rate limit, timeout or 5xx; stop on auth or configuration errors);
- **attribution** of every call by `calling_app`, `reference_doctype`, `reference_name` and `action`;
- **cost** in the provider's currency and the base currency;
- **spend caps** per app, daily and monthly, Warn or Block, checked before anything is spent;
- **provider health** and a connection test;
- an assistant with conversation memory, a cost dashboard, a handbook and a test suite.

Building the agent features anywhere else would duplicate and conflict with it.

**Recommendation:** review and test `somil-dev`, merge it into the branch the bench runs, then add B.2 on top, in agreement with Somil. *(Open question for the owner.)*

### B.2 What to add (generic), in this order

1. **Correct response handling.** Read text from content blocks by type, not `content[0].text`: thinking blocks come first on current models, and that line crashes. Check `stop_reason` (`refusal`, `max_tokens`) before using the output. Stop sending `temperature` before moving to API version `2026-10-21`, which rejects it. Both bugs are present on every branch.
2. **A full request, not one prompt.** A new entry point alongside `generate()`, for example `ai_interface.services.chat.run(...)`, taking:
   - **request:** `system` blocks, a `messages` list, and `effort`;
   - **output:** a JSON `output_schema`, returned validated with one repair attempt;
   - **caching:** cache markers on stable blocks, with a lifetime;
   - **tools:** server tools such as web search and web fetch, passed through.

   The existing `calling_app`, reference, `action`, `needs`, `sync`, routing and budgets work unchanged. Providers that lack a feature are filtered out by the capability filter (`needs=["tools", "json", "cache"]`).
3. **Tools from any app.** Apps register tools in `hooks.py`, for example `ai_tools = {"excom.erp_read": "excom.excom_ai.tools.erp_read.run"}`. Each tool has a JSON schema, a tier (read, draft, communicate, paid) and a permission check. A bounded loop then runs them: a maximum number of steps, a time budget and a cost budget, with every step logged under the parent call. A caller may also run its own loop.
4. **Batches.** Submit many requests through the provider's batch API at its discount; a scheduled job collects the results, and callers get the same realtime completion events.
5. **Budgets per reference.** `somil-dev` caps spending per app. Agents also need caps per reference (an agent, a team, a user), so one agent cannot spend another's budget. The cap is checked before the call.
6. **Call log hygiene.**
   - **Retention:** a period per app after which prompts and outputs are emptied, while the cost row stays.
   - **Redaction:** an option to store prompts without contact details.
   - **Cache visibility:** cached and cache-written token counts, so cache breaks show up.
7. **Command-line provider isolation**, for local use only. Run the way Harvey does:
   - flags: `--tools "" --strict-mcp-config --safe-mode --setting-sources "" --disable-slash-commands --no-session-persistence`;
   - the prompt on stdin, an empty temporary working directory, a timeout;
   - a preflight check that every flag exists.

   It cannot run on Frappe Cloud.
8. **Evaluation runner.** A golden set per app and action (inputs, expected outputs or checks), run on demand and before a prompt template, model or effort change is accepted. The Excom packs use it in §13.4.

Each item gets tests in `ai_interface/tests` and a section in the handbook.

## Appendix C — Sales data model, field by field

### C.1 The rule: research data stays apart until someone is contacted

Harvey's database holds 677 companies. 389 of them never got a single contact, and only 120 were ever emailed. Raw research in ERPNext's Leads would flood the desk's lists, reports and lead-assignment rules (Excom's "Unclassified" rotation hands out every lead with no customer type).

So:

- **Research lives in its own records:** Excom Agent Company and Excom Agent Person, with signals, observations and email-domain facts. It is not CRM. Only agent owners and admins see it.
- **Contacting someone promotes them into native ERPNext records.** This happens when step 1 of a sequence is about to be proposed:
  - the **company** becomes a **Prospect**, with its location as an **Address**;
  - the **person or published inbox** becomes a **Lead**, created through `crm_gateway.create_lead`. That also creates or links the **Omni Identity**, so the Excom conversation, team visibility, claiming and opportunity flow all work as they do for human leads;
  - the Lead is given the agent's `customer_type` (for example Export Importer) and team, so assignment rules never treat it as unclassified.
- **No duplicates.** At promotion, an existing Customer, Lead or Omni Identity with the same email or domain is found and linked, never copied. A match with an existing customer means the company is excluded from cold outreach.
- **No ERPNext Contact at the lead stage.** ERPNext keeps contact details on the Lead until conversion, and Excom's Omni Identity carries the channels. A Contact is created only when a Lead becomes a Customer, as today.
- **One source of truth.** After promotion, the research row keeps only research facts (how the address was found and verified, signals, score) and a link to its Lead or Prospect. Status, conversation and stage live on the Lead, the Enrollment and the Excom Thread.
- **Usage and cost** stay in `ai_interface` (Appendix B).

### C.2 New records

**Excom Agent Company** — every company found, one row per domain across all agents

| Field | Type | Notes / Harvey column |
|---|---|---|
| `company_name` | Data, required | `companies.name` |
| `domain` | Data, unique | `domain`; the dedupe key |
| `external_id` | Data, unique | `external_id` (for example `osm:node/…`, `dataforseo:cid:…`) |
| `website` | Data | `website` |
| `description` | Small Text | `description` |
| `industry` | Data | `industry` (the raw text found; mapped to the Industry Type at promotion) |
| `buyer_type` | Select: Importer, Distributor, Wholesaler, Retailer, Online store, Manufacturer, Other | New |
| `size_band` | Select: 1-10, 11-50, 51-200, 201-1000, 1000+ | `company_size` |
| `country` | Link Country | From `location` |
| `city` | Data | From `location` |
| `location_text` | Data | `location`, as found |
| `phone` | Data | `phone` |
| `language` | Link Language | The website's language |
| `source` | Select: OSM, Serper, Scavio, DataForSEO, TinyFish, Volza, Import, Web search, Manual | `source` |
| `source_url` | Data | `source_url` |
| `found_by_task` | Link Excom Agent Task | Its `runs` row |
| `agent` | Link Excom Agent | The one agent that owns it (empty = unclaimed) |
| `status` | Select: New, Profiled, Contacts found, No contacts, Qualified, Promoted, Excluded | New |
| `exclusion_reason` | Select: Existing customer, Open lead or deal (people), Do not contact, Competitor, Group company, Poor fit, Other | New |
| `fit_score` | Int (0-100) | New; set by the Researcher |
| `research_notes` | Text | `notes`, plus the Researcher's summary |
| `website_facts` | JSON | `tech_stack_json` and profile facts |
| `signals` | JSON (read-only) | `signals_json`: latest value per signal, cached from observations |
| `profile_checked_on` | Datetime | `profile_checked_at` |
| `contacts_checked_on` | Datetime | `prospects_checked_at` |
| `company_stop` | Check | Settings `company_stop:<id>`: someone there replied, so no more cold mail to anyone there |
| `stop_reason` | Small Text | New |
| `prospect` | Link Prospect (read-only) | Set at promotion |
| `customer` | Link Customer (read-only) | Set when matched to an existing customer |

**Excom Agent Person** — a person or published inbox at a company

| Field | Type | Notes / Harvey column |
|---|---|---|
| `company` | Link Excom Agent Company, required | `company_id` |
| `first_name`, `last_name` | Data | Same |
| `full_name` | Data (read-only) | Computed |
| `title` | Data | `title` |
| `seniority` | Select: Owner, Executive, Director, Manager, Staff, Inbox | `seniority` |
| `department` | Data | `department` |
| `is_inbox` | Check | Source `company_inbox` ("General inbox") |
| `email` | Data (Email), unique | `email` |
| `email_status` | Select: Verified, Risky, Guess, Invalid, Unknown | `email_status` |
| `email_source` | Select: Published, Pattern, Hunter, Import | New, from the email finder |
| `verified_by` | Select: Reoon, ZeroBounce, Hunter, None | New |
| `verified_on` | Datetime | New |
| `catch_all` | Check | From `email_patterns.is_catch_all` |
| `consent_verdict` | Select: OK to email, Consent needed, Blocked, Unknown | Precleared, cached 30 days |
| `consent_checked_on` | Datetime | Precleared cache date |
| `phone` | Data | `phone` |
| `phone_verified` | Check | `phone_verified` |
| `linkedin_url` | Data | `linkedin_url` (stored only; no automation) |
| `timezone` | Data | `timezone` |
| `language` | Link Language | New; the language to write in |
| `source`, `source_url` | Select, Data | Same |
| `score` | Int (1-100) | `score` |
| `personalization_notes` | Text | `personalization_notes` |
| `status` | Select: New, Ready, Write failed, Invalid, Promoted, Excluded | Pre-contact states; later states live on the Lead and Enrollment |
| `write_attempts` | Int | Settings `write_attempts:<id>` |
| `lead` | Link Lead (read-only) | Set at promotion |

**Excom Agent Signal Code**: `code` (unique), `label`, `description`, `category`, `value_type` (Number, Text, Flag), `collector`, `cost_note`, `status` (Proposed, Confirmed, Rejected), `confidence_floor`. Seeded with trade signals (§9).

**Excom Agent Observation**: `company`, `person`, `signal` (Link Signal Code; must be Confirmed), `collector`, `value_num`, `value_text`, `confidence`, `evidence_url`, `observed_on`, `task`. The latest row per company and signal is the current value.

**Excom Email Domain**: `domain` (unique), `mx_type` (Google, Microsoft, Gateway, Other, None), `pattern`, `pattern_source`, `pattern_confidence`, `catch_all`, `checked_on`, `hunter_searched_on`. From `email_patterns`.

**Excom Agent Sequence**: `title`, `agent`, `language`, `first_email_style` (Direct offer, Observation), `steps` (table: step, wait in business days, channel, purpose, word limit, photo cards, stop on). From `campaigns.sequence_json`, but written per buyer (Appendix A).

**Excom Agent Enrollment** — one per agent, Lead and sequence

| Field | Type | Notes / Harvey |
|---|---|---|
| `agent` | Link Excom Agent | |
| `sequence` | Link Excom Agent Sequence | `campaign_id` |
| `lead` | Link Lead | `prospect_id`, after promotion |
| `person` | Link Excom Agent Person | |
| `company` | Link Excom Agent Company | Used for the company-level stop |
| `prospect` | Link Prospect | |
| `thread` | Link Excom Thread | `conversation_id` |
| `language` | Link Language | So every step is in the same language |
| `status` | Select: Active, Paused, Replied, Stopped, Completed, Opted out, Bounced | `prospects.status` after contact |
| `stop_reason` | Small Text | `outbox.error` reasons (stop_on_reply, bounced, a colleague replied…) |
| `current_step`, `next_due`, `started_on`, `last_sent_on` | Int, Datetime | |
| `review_next_reply` | Check | Settings `review_next_reply:<id>` |

**Excom Agent Proposal** (§5.2) also carries Harvey's outbox fields for messages:

| Fields | Harvey outbox |
|---|---|
| `enrollment`, `step`, `kind` (Sequence, Reply) | Same |
| `to_email`, `send_at`, `sent_on` | Same |
| `email_draft` (Link Excom Email Draft), `message` (Link Excom Message) | `subject` and `body` live in the draft; once sent, `message_id`, `thread_ref` and `in_reply_to` live on the Excom Message |
| `status`: Pending, Approved, Sending, Sent, Rejected, Cancelled, Failed, Held, Sent by hand | `status`, plus settings `manual_send:<id>` |
| `approved_by`: a user, Autopilot or Earlier | `approved_by` |
| `error`, `hold_reason` | `error` ("held for your review…", "gate: …", "precleared: …", "may have gone out: …", never retried) |

Uniqueness: one Proposal per (enrollment, step), as Harvey's unique index on (campaign, prospect, step).

**Excom Agent Feedback**: `agent`, `scope` (Agent, Company, Lead, Sequence), `reference`, `comment`, `active`. From Harvey's `feedback` table; the Writer reads the active rows for its scope ("owner feedback").

### C.3 Fields added to native records

| Record | Fields (all prefixed `excom_` unless Excom already has them) |
|---|---|
| Prospect | `agent` (Link Excom Agent), `research` (Link Excom Agent Company), `fit_score`, `company_stop`, `stop_reason`. Native fields used: `company_name`, `industry`, `website`, `no_of_employees`, `territory`, `prospect_owner` (the agent's owner), `leads` (Prospect Lead), notes |
| Lead | `agent`, `person` (Link Excom Agent Person), `email_status`, `timezone`, `language`. Native and existing Excom fields used: names, `email_id`, `phone`, `job_title`, `company_name`, `website`, `country`, `city`, `source` (Lead Source "AI Outreach"), `lead_owner` (the agent's owner), `customer_type`, `excom_team`, `omni_identity`, `intake_stage`, `first_touch_channel` |
| Address | Native, linked to the Prospect: `country`, `city`, `address_line1` when known |
| Excom Thread | `handled_by_agent`, `agent_mode` (Off, Assist, Handle, Needs person, Hold), `agent_intent` (Harvey's intents plus "Asked if AI"), `agent_stage` (Initial outreach, Engaged, Qualifying, Presenting, Negotiating, Closing, Closed won, Closed lost) |
| Opportunity | `origin_agent`; created from the Lead or Prospect through `crm_flow` when a buyer is interested |

### C.4 What is not stored again

| Harvey | Where it lives instead |
|---|---|
| `conversations.thread_json` | Excom Messages on the Excom Thread, all channels |
| `processed_replies` | Excom Message's unique `provider_message_id` |
| `runs` | Excom Agent Task: kind discover or profile, provider, records, tool cost, parameters |
| `usage_events`, `usage_log` | `ai_interface`'s call log and budgets |
| `actions` | Tasks, Proposals and timeline comments |
| Settings: `sending_paused` | The agent's or channel's kill switch |
| Settings: `bounce_count` | Counted from Proposals |
| Settings: `geo:*` | Frappe cache |
| Settings: `precleared:*` | `consent_verdict` on the Person |
