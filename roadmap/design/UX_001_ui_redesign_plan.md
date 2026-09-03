# UX-001 — Excom UI Redesign Plan

**Document:** UX-001
**Version:** 1.0
**Date:** 2026-09-03
**Status:** Draft for review
**Owner:** Sagar Ratan Garg
**Relates to:** `HLD_003_native_crm_comms_flow.md` (data + CRM flow), `roadmap/phase_C_voice_channel.md` (calls)
**Goal:** every existing feature, less visible chrome, Gmail-familiar, works from 360 px to 1920 px on low-DPI panels.

---

## Table of Contents

1. [Premises and Non-Goals](#part-1--premises-and-non-goals)
2. [Design Language — tokens, palette, density](#part-2--design-language)
3. [Layout System — one tree, four widths](#part-3--layout-system)
4. [Progressive Disclosure — the four tiers](#part-4--progressive-disclosure)
5. [Information Architecture — where every feature lives](#part-5--information-architecture)
6. [The Conversation Surface](#part-6--the-conversation-surface)
7. [Phase U1 — Shell and Inbox](#part-7--phase-u1--shell-and-inbox)
8. [Phase U2 — Record Cockpit and CRM Surfaces](#part-8--phase-u2--record-cockpit-and-crm-surfaces)
9. [Phase U3 — Admin Surfaces, Mobile Parity, Retire Legacy](#part-9--phase-u3--admin-surfaces-mobile-parity-retire-legacy)
10. [Parallel Testing Strategy](#part-10--parallel-testing-strategy)
11. [QA Matrix and Acceptance Gates](#part-11--qa-matrix-and-acceptance-gates)
12. [Risks and Open Decisions](#part-12--risks-and-open-decisions)

---

# Part 1 — Premises and Non-Goals

## 1.1 Premises

| # | Premise | If wrong |
|---|---|---|
| P1 | Users are Gmail-fluent and Desk-tolerant. Familiarity beats novelty | Re-open Part 3 |
| P2 | The data hooks (`useThreads`, `useMessages`, `useRealtime*`, `useLinkedEntities`) are sound; only the presentation layer is rebuilt | Scope grows by ~2 weeks |
| P3 | Old and new UI run side by side until Phase U3 signs off | Big-bang cutover, no rollback |
| P4 | CRM fields (`customer_type`, `pipeline_stage`, `next_action_at`, gates) arrive from HLD-003 phases N1/N2 | U2 renders from a stub schema and ships behind a flag |
| P5 | Reference worst case is a 1366×768 @1x laptop at 125 % Windows scaling | Type scale shifts up one step |

## 1.2 Non-goals

- No dark mode in this plan (`darkMode: "class"` stays configured, tokens are authored to allow it later).
- No new backend doctypes. UI-only, except the CRM endpoints already specified in HLD-003 §3.4.
- No redesign of Desk. Quotation, Sales Order and back-office stay in Desk.
- No animation system. Transitions are ≤150 ms opacity/transform only.

## 1.3 The five rules everything is judged against

1. **One context strip, never five bars.** Chrome above the message list is capped at two rows.
2. **Navigation is not filtering.** The rail navigates; saved views filter; search and ⌘K do the rest.
3. **Nothing loud.** No gradients, no saturated fills larger than a chip, one accent per row.
4. **Nothing hidden that is needed twice a day.** Tier rules in Part 4 are enforced by usage, not taste.
5. **Text never overlaps, at any width.** Every text container is `min-w-0` + truncating; chip rows scroll.

---

# Part 2 — Design Language

## 2.1 Why the current look fails

| Symptom | Evidence | Fix |
|---|---|---|
| Gradients everywhere | 57 usages across 16 files (`App.tsx`, `LeftSidebar`, `ChannelTabsView`, all of `mobile/`) | Delete; flat tints only |
| Text too small for low-DPI | 149 usages under 12 px — 99× `text-[10px]`, 22× `text-[9px]`, 3× `text-[8px]` | Floor of 12 px for anything readable; 11 px only for numeric badges |
| No token layer | Colours hardcoded as `zinc-*` + hex; `tailwind.config.js` extends only `primary` | CSS variables + Tailwind aliases (§2.2) |
| Loudest element is not the important one | `AI Assist` is a blue→purple gradient button in the header | Primary action is *reply*; AI moves to tier 3 |

## 2.2 Colour — light crayon on chalk

Authored as CSS custom properties in `src/styles/tokens.css`, exposed to Tailwind via `theme.extend.colors` so classes read `bg-surface-sunken`, `text-ink-2`, `bg-crayon-green-tint`.

**Neutrals (chalk).** The whole UI is these; accents are garnish.

| Token | Hex | Use |
|---|---|---|
| `surface` | `#FFFFFF` | Message pane, cards, dialogs |
| `surface-sunken` | `#F6F7F9` | Rail, list column, page background |
| `surface-hover` | `#EFF1F4` | Row hover |
| `surface-active` | `#E7EAEF` | Selected row |
| `border` | `#E3E6EA` | All dividers, 1 px |
| `border-strong` | `#CDD3DA` | Inputs, focus-adjacent |
| `ink-1` | `#1B2129` | Primary text (15.2:1) |
| `ink-2` | `#4B5563` | Secondary text (8.1:1) |
| `ink-3` | `#6B7480` | Meta text, 12 px min (4.9:1) |
| `ink-muted` | `#98A1AC` | **Icons ≥16 px only — never text** (2.9:1, fails AA) |

**Crayon accents.** Each is a triple: `tint` (background), `base` (icon/underline/dot), `text` (label on tint). All `text` values clear 4.5:1 on their own tint.

| Accent | tint | base | text | Meaning |
|---|---|---|---|---|
| `blue` | `#EDF1FC` | `#6E8FE8` | `#2C4FA8` | Primary action, selection, Email |
| `green` | `#E9F6F0` | `#3FA37A` | `#226B4E` | WhatsApp, success, gate cleared |
| `amber` | `#FBF2E4` | `#D99A3E` | `#8A5B18` | Pending, SLA at risk, unsent |
| `rose` | `#FBECEB` | `#D9645F` | `#97302D` | Overdue, failed, breached |
| `violet` | `#F1EFFB` | `#8A7CD8` | `#52459C` | AI-generated, automated |
| `teal` | `#E8F4F6` | `#3E9AA8` | `#1F5F69` | Accounts / finance lens |
| `plum` | `#FAEEF5` | `#C86BA0` | `#8A3F6A` | Instagram / Meta |
| `sand` | `#F5F2E6` | `#9A8A4E` | `#5F5426` | Purchase lens |

**Usage rules.**
- A crayon may fill a chip, a dot, a 3 px left border, or a 1-line strip. Never a panel, never a page.
- One accent per row. If a row needs two states, the second becomes text.
- Channel identity is carried by **icon + label**, colour is reinforcement — colour-blind users must not lose the channel.
- No gradient, no shadow above `0 1px 2px rgba(27,33,41,.06)`. Elevation is border + surface, Slack-style.

## 2.3 Typography

Inter, already loaded. Weights **400 / 500 / 600 only** — 300 disappears on low-DPI.

| Step | Size / line-height | Use |
|---|---|---|
| `2xs` | 11 / 14, weight 600, tabular | Numeric badges only (counts, unread) |
| `xs` | 12 / 16 | Timestamps, meta, chip labels |
| `sm` | 13 / 18 | Default UI text, list rows, buttons, labels |
| `base` | 14 / 20 | Message body, note body, field values |
| `md` | 15 / 20, weight 600 | Record title, dialog title |
| `lg` | 17 / 24, weight 600 | Page title (rare) |

Low-DPI protections: no letter-spacing below 0; no italic below 14 px; numerals `font-variant-numeric: tabular-nums` in every table, timer and counter; icon sizes locked to 14/16/20 px (integer grid, no 15 px, no `w-[13px]`).

## 2.4 Spacing and density

4 px base. Tight, but with hard minimums so nothing collides.

| Token | Desktop | Touch (<640 or coarse pointer) |
|---|---|---|
| Row height — list | 56 (two-line) | 68 |
| Row height — dense table | 36 | 44 |
| Hit target minimum | 32×32 | **44×44** |
| Page gutter | 12 | 12 |
| Card padding | 12 | 12 |
| Gap between chips | 6 | 8 |
| Composer padding | 8 12 | 8 12 + safe-area |
| Header height | 48 | 52 |

Density toggle (Gmail's "Comfortable / Compact") persists per user in `localStorage`, switching row heights only — never font sizes, so it stays legible at either setting.

## 2.5 Overlap safety — the six mechanical rules

The current UI collides at 768–1024 px because it never guards. Enforced by lint-able patterns:

1. Every flex child holding text: `min-w-0` on the parent chain, `truncate` on the leaf.
2. Every horizontally growing chip row: `overflow-x-auto` + `-webkit-overflow-scrolling` + fade mask, never `flex-wrap` inside a fixed-height bar.
3. Header action clusters collapse into `⋯` below 1100 px — the code decides by container width, not viewport.
4. Grid columns are `minmax(0, 1fr)`, never `1fr` (which refuses to shrink below content).
5. Absolutely positioned elements are forbidden over text; badges are inline-flex siblings.
6. Every list/detail component is smoke-tested with the **stress record**: 48-char company name, +91 15-digit number, 6 tags, 4 channels, ₹1,23,45,678 amount, 3-line last-message preview.

---

# Part 3 — Layout System

One component tree at all widths. `components/mobile/` is retired in U3 — it is the reason phone parity keeps slipping (calls tab is a "Coming Soon" placeholder; broadcasts, teams, analytics, merge, subscribers do not exist on phone at all).

## 3.1 Breakpoints

| Name | Range | Shell |
|---|---|---|
| `phone` | < 640 | Single column, drill-in, bottom tab bar, FAB |
| `tablet` | 640–1023 | Rail (icons, 56) + one column; list ↔ record is a drill-in, details is a sheet |
| `laptop` | 1024–1439 | Rail (56) + list (320) + record (fluid); details is a push-drawer, `⌘.` toggles |
| `wide` | ≥ 1440 | Rail (56) + list (360) + record (fluid) + details (300, persistent) |

Reference target is `laptop` at 1366×768 — the record pane must be usable at 1366 − 56 − 320 = **990 px**, and the message list must clear 400 px of vertical space after chrome.

## 3.2 The Slack/Gmail hybrid shell

```
┌────┬───────────────────────┬────────────────────────────────┬──────────────┐
│    │  LIST                 │  RECORD                        │  DETAILS     │
│ R  │  ┌─ search ─────────┐ │  ┌─ header (48) ────────────┐  │  (wide only, │
│ A  │  │ saved view ▾     │ │  │ avatar · name · Lead-042 │  │   else       │
│ I  │  └──────────────────┘ │  │ [Reply via ▾]     ⋯      │  │   drawer)    │
│ L  │  ┌──────────────────┐ │  └──────────────────────────┘  │              │
│    │  │ row  56px        │ │  ┌─ context strip (28) ─────┐  │  identity    │
│ 56 │  │ row  (selected)  │ │  │ stage · gate · next act. │  │  channels    │
│    │  │ row              │ │  └──────────────────────────┘  │  linked ERP  │
│    │  │ …                │ │  ┌─ tabs (36) ──────────────┐  │  invoices    │
│    │  └──────────────────┘ │  │ Chat Details Tasks Notes │  │  quick acts  │
│    │                       │  ├──────────────────────────┤  │              │
│    │                       │  │  merged conversation     │  │              │
│    │                       │  │  ┌─ composer ──────────┐ │  │              │
└────┴───────────────────────┴──┴──┴─────────────────────┴─┴──┴──────────────┘
```

Chrome above the messages: **header + context strip + tabs = 112 px**, fixed. Today it is up to five stacked bars (`ChannelTabsView.tsx` lines 429, 658, 690, 724, 755 — messages only begin at 785).

## 3.3 The rail (Slack)

56 px, icons only, hover-expands to 200 px as an overlay (does not reflow the page — reflow-on-hover is the thing everyone hates about hover rails).

Top → bottom: company switcher (ERPNext `Company`, hidden when the user has one), **New** (single primary action, Gmail's Compose), Today, Inbox, Pipeline, Contacts, divider, Broadcasts, Analytics, divider, avatar → menu (Teams, Merge, Subscribers, Rules, Settings, density, sign out).

Admin surfaces move under the avatar menu because reps open them weekly, not hourly.

## 3.4 The list column (Gmail)

- One search field, always visible. Typing shows scoped suggestions; Enter searches messages *and* records.
- **Saved views replace the seven filter widgets** currently stacked in `LeftSidebar.tsx` (search, channel chips, account chips, tags, date, team, broadcast). Default views: *Unread*, *Assigned to me*, *Unassigned*, *Today's actions*, *SLA risk*, *All*. A view is a URL (`/inbox/unassigned?channel=whatsapp`) — shareable, bookmarkable, back-button-able.
- Ad-hoc filters become **search chips** (`channel:whatsapp team:export after:2026-08-01`), added by a `+ Filter` button that writes the chip for you. Any chip combination can be saved as a view.
- Row: avatar 32 · name (600, truncate) · channel dots · time · second line = last message preview + tag dots + SLA pip. Unread = name in 600 + a 6 px blue dot, never a full-row tint.
- Row actions (archive, assign, snooze, mark read) appear on hover on pointer devices, and are swipe actions on touch.

---

# Part 4 — Progressive Disclosure

Raven's discipline, made explicit. Every control in the app is assigned a tier; the tier decides where it renders. This is what removes the clutter without removing the features.

| Tier | Rendering | Contents |
|---|---|---|
| **T1 — Always** | Visible, in place | Composer + send, **Reply via ▾**, back/close, unread state, stage chip, next-action chip, search |
| **T2 — On demand** | Hover/focus reveal (pointer); long-press or swipe (touch); always keyboard-reachable | Message actions (reply, pin, react, copy, forward, note), row actions (archive, assign, snooze), attachment/template/canned/sticker pickers |
| **T3 — Overflow `⋯`** | Menu, grouped, with shortcut hints | Transfer, tags, AI assist, mark spam, block, export, subscribe to broadcast, open in Desk, view raw payload |
| **T4 — ⌘K palette** | Searchable, everything | All of the above + navigation + saved views + record jump + settings. Nothing is *only* in T4 except developer/admin escapes |

**Enforcement rule:** a control used in fewer than one in three sessions may not sit in T1. Measured after U1 ships via a lightweight click log, not by argument.

**Accessibility floor:** T2 hover reveals must also appear on `:focus-within`, so keyboard users never lose an action. Touch devices (`pointer: coarse`) render T2 as a visible `⋯` per row instead of hover.

## 4.1 What this does to today's header

Six competing controls at `ChannelTabsView.tsx:497–560` (TagManager, AI badge, Transfer, gradient "AI Assist", collapse-header, collapse-details) become:

- T1: name + record chip + **Reply via ▾**
- T2: nothing (header has no hover actions)
- T3: `⋯` → Transfer, Tags, AI assist, Open in Desk, Mark spam
- Panel toggles move to `⌘.` and the layout decides by width, so the buttons disappear entirely.

---

# Part 5 — Information Architecture

Every existing feature has a destination. Nothing is dropped.

| Feature (today) | Lives in the new IA | Tier |
|---|---|---|
| Inbox / thread list | List column, saved views | T1 |
| Channel tabs (`ChannelTabsView`) | **Removed** — merged thread + channel filter chips | — |
| Account selector + "Viewing & replying via" banner | **Merged into `Reply via ▾`** (one control, replaces two bars) | T1 |
| Pinned messages bar | Chat tab → pinned strip collapsed to a single line, expands on click | T2 |
| Tags (`TagManager`) | `⋯` menu + Details tab; tag dots on list rows | T3 |
| Transfer conversation | `⋯` menu; confirm dialog unchanged | T3 |
| AI assistant drawer | `⋯` → AI, opens the details drawer's AI tab (no separate 5th column) | T3 |
| Suggested replies | Inline above composer, one line, dismissible | T2 |
| WhatsApp templates / canned / stickers / attachments | Composer `+` menu; template picker is a sheet on phone | T2 |
| Email compose (subject/cc/bcc) | Composer expands in place when Reply-via = Email | T1 |
| Delivery status + 10-min timer | Message meta line, icon + tabular time | T1 |
| Internal note | Composer mode toggle (Message / Note), note-mode tints the composer amber | T1 |
| Identity panel (`OmniIdentityPanel`) | Details drawer, sections: Contact, Channels, Linked ERP, Invoices, Summary, Quick actions | T1 in `wide`, T2 elsewhere |
| Merge suggestions | Avatar menu → page; plus an inline "possible duplicate" chip on the record | T3 |
| Teams | Avatar menu → page | T3 |
| Subscribers + Rules | Avatar menu → page (Broadcasts sub-nav) | T3 |
| Broadcasts | Rail → page | T2 |
| Analytics | Rail → page | T2 |
| Settings | Avatar menu → page | T3 |
| New conversation | Rail **New** button + ⌘K "New conversation" | T1 |
| Calls (Phase C) | Channel in the merged thread + Calls saved view; call button in `Reply via ▾` | T1 |
| Meta comments | Separate saved view "Comments" — list of public comments with inline reply, not DM bubbles | T2 |
| **Tasks** (new) | Record tab, backed by core `ToDo` + `next_action_at` | T1 |
| **Notes** (new) | Record tab, backed by `Comment` on Lead/Opportunity | T1 |
| **Activity** (new) | Record tab, merged `Version` + stage log + system messages | T2 |
| **Intake / Pipeline / Today** (new) | Rail entries, per HLD-003 §3.3 | T1 |

## 5.1 The role lens

Four lenses — Sales, Purchase, Accounts, Compliance — selected once in Settings, defaulted from the user's Frappe role, switchable from the avatar menu. A lens changes **only three things**: the context strip content, the default saved view, and which Details sections are expanded first. It is a *view preset*, never a permission — permissions stay with HLD-003 §11.3.

| Lens | Context strip | Default view | Details priority |
|---|---|---|---|
| Sales | stage · gates · amount · next action | Today's actions | Record, Items, Next action |
| Purchase | relationship · territory · open RFQs | Unassigned | Record, Supplier links |
| Accounts | deal value · outstanding · overdue days | SLA risk | Invoices, Payment terms |
| Compliance | destination · checklist n/m · blocked | Assigned to me | Checklist, Documents |

> **Open:** the Purchase lens has no backing pipeline. HLD-003 covers the sales side only (six customer types on `Lead`/`Opportunity`). The `Supplier` identity hook exists in `hooks.py`, so the spine is there — but the purchase flow needs its own spec before this lens is more than a filter. Decision required before U2.

---

# Part 6 — The Conversation Surface

The single highest-value change, and the reason "things get missed" today.

## 6.1 One thread, not tabs

All channels for one identity render in **one chronological feed**. Each message carries a small channel icon + account name in its meta line. Channel chips above the feed **filter** (`All · WhatsApp 12 · Email 4 · Calls 2`), they do not navigate — the unread math stops being per-tab, so nothing hides behind an unselected tab.

## 6.2 `Reply via ▾` — the one control that replaces three bars

Sits in the composer, not the header. Shows the channel *and* the sending account:

```
┌──────────────────────────────────────────────────────────┐
│ [🟢 WhatsApp · +91 98xxx (GGIL Export) ▾]   window 3h 12m │
│ ┌──────────────────────────────────────────────────────┐ │
│ │ Message…                                    + ⏎ Send │ │
│ └──────────────────────────────────────────────────────┘ │
│ Message ● Note                                            │
└──────────────────────────────────────────────────────────┘
```

Defaults to the channel of the last inbound message. The dropdown lists every channel the identity has, plus every account on that channel, with state inline:

| State | Shown as | Behaviour |
|---|---|---|
| WhatsApp session open | `window 3h 12m` in `ink-3` | Free-text send allowed |
| WhatsApp session closed | amber chip `Template required` | Composer swaps to template picker; free text disabled with an explanation, not a silent failure |
| Email | subject line + cc/bcc appear | Composer grows in place |
| Opted out / spam | rose chip `Opted out` | Send blocked, reason shown, override logged |
| No access to account | `No access` | Account greyed in the menu |
| Calls (Phase C) | `Call` action | Places call, logs `Excom Call` into the same feed |

This is exactly the "switch from email to WhatsApp for the same client" flow: one click in the composer, chronology preserved, no page state changed, no tab hunt.

## 6.3 Record tabs

`Chat · Details · Tasks · Notes · Activity`, with counts on Tasks (open) and Notes.

At `wide`, HLD-003 §3.3.3's two-pane cockpit is honoured differently than by tab-hunting: **Chat stays permanently on the right**, and Details/Tasks/Notes/Activity render in the left pane as a segmented control. Below 1440 they collapse into the five tabs. Same components, different composition — this resolves the doc-vs-mock conflict without picking a loser.

Data sources, and what is missing:

| Tab | Source | Status |
|---|---|---|
| Chat | `Excom Message` / `Excom Thread` | Exists |
| Details | `crm.get_field_schema(doctype, customer_type)` — schema-driven, so a Custom Field appears without a frontend release | **Needs N1+N2** |
| Tasks | Core `ToDo` with `reference_type`/`reference_name` → Lead/Opportunity/Thread, plus `next_action_at`. Inherits Desk assignment, notifications and the §10.3 escalation ladder for free | **Needs N1** |
| Notes | `Comment` on the linked record. Distinct from the existing in-thread internal note (`api/chat.py:930`), which stays — a note about a *moment* lives in the thread, a note about a *party* lives on the record | Partial |
| Activity | Merged `Version` + stage-change log + conversion system messages + call records; one endpoint | **Needs N2** |

---

# Part 7 — Phase U1 — Shell and Inbox

**Duration:** 8–11 days · **Depends on:** nothing · **Ships behind:** `?ui=next` + per-user opt-in

The phase that pays for itself: it fixes the clutter, the breakpoints and the routing, for every user, without waiting on any backend work.

## 7.1 Deliverables

| # | Item | Detail |
|---|---|---|
| 1 | `src/styles/tokens.css` + `tailwind.config.js` extension | Part 2 tokens as CSS variables and Tailwind colour aliases |
| 2 | Primitives: `Chip`, `Badge`, `Row`, `Toolbar`, `OverflowMenu`, `Sheet`, `Drawer`, `EmptyState`, `Field` | Built on the existing Radix set; no new dependency |
| 3 | **Router** | `react-router-dom@6` is already installed and unused. Routes: `/inbox/:view?`, `/t/:threadId`, `/broadcasts`, `/analytics`, `/settings`, `/teams`, `/merge`, `/subscribers`, `/rules`. Replaces the 17 pieces of ad-hoc state in `App.tsx:54–77` and gives phone users a working back button |
| 4 | Responsive shell (`AppShell`) | Rail + list + record + details, per §3.1. `useBreakpoint()` replaces the single `isMobile` check at `App.tsx:127` |
| 5 | Saved views + search chips | Replaces the seven-filter stack in `LeftSidebar.tsx` |
| 6 | Merged conversation feed + channel filter chips | Replaces `ChannelTabsView` channel tabs |
| 7 | `Reply via ▾` composer | Replaces account selector + active-account banner + per-channel composer branches (§6.2) |
| 8 | Tier assignment pass | Every existing control moved to its tier per Part 5 |
| 9 | ⌘K palette | Threads, records, views, commands, settings |
| 10 | De-gradient + type floor | 57 gradient usages removed; 149 sub-12 px usages raised to the §2.3 scale |
| 11 | Phone layout | Bottom tabs (Today · Inbox · Pipeline · More), FAB, swipe actions, safe-area composer, drill-in routes |

## 7.2 Component mapping

| Old | New | Fate |
|---|---|---|
| `App.tsx` (434) | `AppShell` + `routes/` | Rewritten |
| `LeftSidebar.tsx` (605) | `Rail` (~120) + `ViewList` (~150) + `SearchBar` (~120) | Split |
| `ChatThreadList.tsx` (436) | `ThreadList` + `ThreadRow` | Rewritten, denser |
| `ChannelTabsView.tsx` (1348) | `RecordHeader` + `ContextStrip` + `MessageFeed` + `Composer` + `ReplyVia` | **Split into 5 files, none over ~300 lines** |
| `OmniIdentityPanel.tsx` (634) | `DetailsDrawer` + section components | Re-skinned, sections reused |
| `EmailCompose`, `WhatsAppTemplatePicker`, `CannedResponsePopover`, `StickerPicker`, `MessageContextMenu` | Mounted from the composer `+` menu / T2 | Re-skinned only |
| `hooks/*` | Unchanged | **Reused as-is** |
| `components/mobile/*` | Still served to legacy users | Untouched until U3 |

## 7.3 Parallel testing in U1

Both UIs live in one bundle. A user opens `/excom?ui=next` (sticky in `localStorage`) or is opted in by an admin flag; a small "Switch to old UI / Try new UI" link sits in the avatar menu both ways. No second build, no second deployment, instant rollback per user.

## 7.4 Exit criteria

- Every T1/T2 control from the old inbox is reachable in the new one; verified against a written checklist of 42 controls.
- Chrome above messages ≤112 px at 1366×768; message list ≥400 px tall.
- No horizontal scrollbar and no text overlap at 360/390/640/768/834/1024/1280/1366/1440/1920 with the stress record.
- Zero `bg-gradient-*` and zero `text-[8|9|10|11px]` outside numeric badges (grep gate in CI).
- Back button works on phone for list → thread → details.

---

# Part 8 — Phase U2 — Record Cockpit and CRM Surfaces

**Duration:** 8–12 days · **Depends on:** U1, and HLD-003 **N1 + N2** for real data · **Ships behind:** the same flag, plus a `crm` sub-flag

## 8.1 Deliverables

| # | Item | Detail |
|---|---|---|
| 1 | Record tabs: Details / Tasks / Notes / Activity | §6.3; Details renders from `get_field_schema`, so Custom Fields appear without a release |
| 2 | Context strip | Stage · gate chips · next action · amount, lens-aware (§5.1) |
| 3 | `crm-today` | The rep's day: overdue actions, SLA breaches, today's actions, unassigned for my teams |
| 4 | `crm-intake` | S1–S5 queue by `intake_stage`, SLA pip, bulk classify |
| 5 | `crm-pipeline` | Kanban on `pipeline_stage`, one saved board per `customer_type`; **on phone it is a stage-picker list, not a drag board** |
| 6 | Convert / promote actions | `Promote to Lead`, `Convert to Opportunity/Customer`, blocked-gate reasons shown inline |
| 7 | Two-pane cockpit at ≥1440 | Left = record segments, right = permanent chat |
| 8 | Lens presets | Sales / Accounts / Compliance wired; Purchase pending its spec |

## 8.2 Working before the backend lands

`api/crm.py` does not exist yet, and `hooks.py` has no `Opportunity`/`Prospect` events. U2 starts against a **fixture schema** — a JSON file matching `get_field_schema`'s contract — so UI work runs in parallel with N1/N2 rather than behind it. Swapping the fixture for the endpoint is a one-line change in the hook.

## 8.3 Exit criteria

- A rep can run a full day — Today → open record → advance stage → add task → reply — without opening Desk.
- Adding a Custom Field in Desk makes it appear in Details on reload, with no frontend change. This is the test that proves the schema-driven approach.
- Pipeline board usable at 1366×768: 5 columns visible, cards ≤132 px tall.
- Blocked gate drags show the failing gate inline (HLD-003 §3.3.2), never a generic error.

---

# Part 9 — Phase U3 — Admin Surfaces, Mobile Parity, Retire Legacy

**Duration:** 6–9 days · **Depends on:** U1 (U2 optional)

## 9.1 Deliverables

| # | Item |
|---|---|
| 1 | Re-skin `BroadcastPage` (1644), `SubscriberListPage` (827), `SubscriberRulesPage` (662), `AnalyticsPage` (728), `TeamManagementPage` (515), `MergeSuggestionsPage` (228), `SettingsPage` (697) onto tokens + primitives |
| 2 | Wizard pattern for Broadcasts — the 1644-line page becomes Audience → Content → Schedule → Review, each step fitting one screen at 768 px tall |
| 3 | Analytics on the crayon palette: `recharts` themed to tokens, no gradient fills, tabular numerals, legible at 1×; every chart gets a table fallback below 640 |
| 4 | **All admin pages responsive** — today none exist on phone |
| 5 | Delete `components/mobile/` (7 files, ~1900 lines) once parity is signed off |
| 6 | Remove the legacy tree and the `ui=next` flag; delete `ChannelTabsView.tsx` and the old `LeftSidebar` |
| 7 | Accessibility pass: focus rings on tokens, `:focus-within` reveals, `aria-live` for new messages, keyboard map documented |
| 8 | Keyboard map: `⌘K` palette, `j/k` rows, `⏎` open, `e` archive, `a` assign, `/` search, `⌘⏎` send, `⌘.` details, `g then i/t/p` go-to |

## 9.2 Exit criteria

- Every page renders at 360 px with no overlap and no horizontal scroll.
- One component tree; `grep -r "components/mobile"` returns nothing.
- Old UI removed; bundle size no larger than before despite the added surfaces.

---

# Part 10 — Parallel Testing Strategy

The plan is built so that **at every moment there is a working UI to fall back to**, and so that design, frontend and the HLD-003 backend phases advance simultaneously.

## 10.1 Three tracks running side by side

| Track | Owner | Runs during | Blocked by |
|---|---|---|---|
| **A — UI** | Frontend | U1 → U2 → U3 | nothing (U2 uses fixture schema) |
| **B — CRM backend** | Backend | HLD-003 N1 → N2 → N3 | nothing |
| **C — Pilot feedback** | 6 users, 2 per lens | from U1 day 1 of internal release | Track A only |

Track A and B converge once at the start of U2's second week (fixture → real endpoint). If B slips, A ships U1 + U3 and U2 waits — nothing else stalls.

## 10.2 How both UIs run at once

- Single bundle, single deployment. Route `/excom` renders `LegacyApp` or `NextApp` based on: URL `?ui=next|legacy` → `localStorage.excom_ui` → per-user flag on `Excom Settings` → default (`legacy` until U3).
- Shared data layer: both trees import the same `hooks/*`, so there is exactly one source of truth and no double-maintenance of API calls.
- Switch link in both UIs' avatar menus. A user who hits a wall goes back in one click and files the reason via a one-line feedback box that captures the route, viewport, and DPR automatically.
- Legacy files are frozen — bug fixes only — from U1 day 1, so effort does not fork.

## 10.3 Pilot protocol

| Week | Group | Task | Signal collected |
|---|---|---|---|
| U1 w2 | 2 sales reps | Run one full day on `ui=next` inbox | Time-to-first-reply, misses, "where is X?" count |
| U1 w2 | 1 accounts, 1 compliance | Same, with their lens | Whether the lens strip carries their whole job |
| U2 w2 | Same 4 + 2 managers | Pipeline + Today for a week | Stage advances done in Excom vs Desk |
| U3 w1 | All users | Default flipped to `next`, opt-out available | Opt-out rate — target < 10 %, and every opt-out gets a reason |

Instrumentation is deliberately cheap: a click log keyed by control id (feeds the Part 4 tier rule), plus route + viewport + DPR on every feedback submission. No third-party analytics.

## 10.4 Regression safety per phase

- **Control checklist** — a living list of every T1–T3 control with its old and new location; a phase cannot exit with an unmapped row.
- **Grep gates in CI** — no `bg-gradient`, no sub-12 px text classes outside the badge allowlist, no `components/mobile` imports (from U3), no hardcoded hex in `src/components` (tokens only).
- **Screenshot sweep** — 11 widths × 2 DPR × 3 zoom levels, captured for the 6 core screens each phase; diffed by eye, filed as an artefact.
- **Stress record fixture** — checked into `src/fixtures/stress.ts`, rendered in a `/dev/stress` route that shows every component with the worst-case data at once.

---

# Part 11 — QA Matrix and Acceptance Gates

## 11.1 Viewport matrix

| Width | Device class | DPR | Must hold |
|---|---|---|---|
| 360 | small Android | 2 | No overlap; 44 px targets; composer above keyboard |
| 390 | iPhone | 3 | Safe-area insets top and bottom |
| 414 | large phone | 2 | Same as 390 |
| 640 | phone→tablet boundary | 2 | Layout switches cleanly, no flash |
| 768 | iPad portrait | 2 | Rail + one column; **not** the old 4-column squeeze |
| 834 | iPad Air | 2 | Details as sheet |
| 1024 | iPad landscape / netbook | 1 | Three columns fit; list 320 |
| **1366×768** | **reference low-DPI laptop** | **1** | **Record pane ≥990 px; message area ≥400 px; all 13 px text crisp** |
| 1440 | laptop | 1–2 | Details pane becomes persistent |
| 1920 | desktop | 1 | Content max-width caps; no 1200 px line lengths |

Plus: Windows display scaling 100 / 125 / 150 %, browser zoom 90 / 100 / 110 %, and `prefers-reduced-motion`.

## 11.2 Low-DPI acceptance (the 1× checks)

1. No text below 12 px except tabular numeric badges at 11 px.
2. No font-weight 300 anywhere.
3. Borders are exactly 1 px, colour `#E3E6EA` or darker — no `rgba` hairlines that vanish at 1×.
4. Icons at 14/16/20 px only, on integer positions.
5. No text over tinted fills lighter than its `text` token pair (Part 2.2 triples are pre-checked for 4.5:1).
6. Shadows limited to `0 1px 2px rgba(27,33,41,.06)` — larger blurs band visibly at 1×.
7. Charts: minimum 2 px stroke, direct labels rather than a legend where space allows.

## 11.3 Per-phase gates

| Gate | U1 | U2 | U3 |
|---|---|---|---|
| Control checklist fully mapped | ✓ | ✓ | ✓ |
| Viewport matrix clean | ✓ | ✓ | ✓ |
| Low-DPI checks pass | ✓ | ✓ | ✓ |
| Pilot opt-out reasons resolved or accepted | ✓ | ✓ | ✓ |
| Chrome ≤112 px above messages | ✓ | ✓ | ✓ |
| Keyboard map complete | — | — | ✓ |
| Legacy tree deleted | — | — | ✓ |

---

# Part 12 — Risks and Open Decisions

## 12.1 Risks

| # | Risk | Mitigation |
|---|---|---|
| R1 | Merged thread confuses users who think in channels | Channel filter chips are prominent; a "Group by channel" toggle exists for the first release and its usage is measured before removal |
| R2 | `ChannelTabsView` (1348 lines) hides undocumented behaviour that breaks on rewrite | Split, don't rewrite blind: extract the five regions in place first, then re-skin |
| R3 | U2 blocked by N1/N2 slipping | Fixture schema (§8.2) — UI never waits on backend |
| R4 | Two UIs double the bug surface | Legacy frozen from U1 day 1; shared hooks; hard delete in U3 |
| R5 | Tier rules hide something a lens needs daily | Tiers are per-lens overridable; the click log settles arguments with data |
| R6 | Purchase lens has no backing flow | Ship it as a saved-view filter only until a purchase spec exists (§5.1) |
| R7 | Density looks cramped to some users | Comfortable/Compact toggle changes row heights only, never type size |

## 12.2 Open decisions — needed before U1 starts

| # | Decision | Recommendation |
|---|---|---|
| Q1 | Merged thread, or keep channel tabs? | **Merged**, with a temporary group-by-channel toggle |
| Q2 | Two-pane at ≥1440 *and* tabs below, or tabs everywhere? | **Both**, by width (§6.3) |
| Q3 | Lens = preset or permission? | **Preset**, defaulted from the Frappe role |
| Q4 | Tasks on core `ToDo`, or a new doctype? | **`ToDo`** — inherits assignment, notification, escalation |
| Q5 | Is Purchase in scope for U2? | **No** until a purchase flow spec exists |
| Q6 | Default flip to the new UI at U3, or earlier? | **U3**, with per-user opt-in from U1 |
| Q7 | Retire `components/mobile/` or keep two trees? | **Retire** in U3 — it is the source of phone feature gaps |

---

## Appendix A — Effort summary

| Phase | Days | Can start | Parallel with |
|---|---|---|---|
| U1 — Shell and Inbox | 8–11 | immediately | HLD-003 N1, N2, N3 |
| U2 — Cockpit and CRM | 8–12 | after U1 | HLD-003 N3, N5 |
| U3 — Admin, parity, retire | 6–9 | after U1 (U2 optional) | anything |
| **Total** | **22–32** | | |

## Appendix B — Files touched per phase

**U1** — new: `styles/tokens.css`, `components/primitives/*`, `AppShell.tsx`, `routes/*`, `Rail.tsx`, `ViewList.tsx`, `ThreadList.tsx`, `MessageFeed.tsx`, `Composer.tsx`, `ReplyVia.tsx`, `CommandPalette.tsx`, `useBreakpoint.ts`. Modified: `App.tsx`, `tailwind.config.js`, `index.css`. Frozen: `LeftSidebar.tsx`, `ChannelTabsView.tsx`, `ChatThreadList.tsx`, `components/mobile/*`.

**U2** — new: `routes/today.tsx`, `routes/intake.tsx`, `routes/pipeline.tsx`, `RecordTabs.tsx`, `DetailsTab.tsx`, `TasksTab.tsx`, `NotesTab.tsx`, `ActivityTab.tsx`, `ContextStrip.tsx`, `GateChips.tsx`, `hooks/useCrmSchema.ts`, `hooks/useTasks.ts`, `fixtures/crm_schema.json`. Backend (HLD-003): `api/crm.py`, `services/crm_flow.py`, `hooks.py` doc_events.

**U3** — modified: all seven admin pages, `AnalyticsPage` chart theme. Deleted: `components/mobile/*`, `ChannelTabsView.tsx`, `LeftSidebar.tsx`, legacy route.
