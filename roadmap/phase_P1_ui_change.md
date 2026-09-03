# Phase P1 — UI Change

**Phase:** P1 of 5 (`PLAN_001_master_phasing.md`)
**Design source:** `design/UX_001_ui_redesign_plan.md`
**Duration:** 16–24 days
**Depends on:** nothing — no backend work required
**Blocks:** P2 (testing), P3 §3.10 (CRM surfaces reuse this shell)
**Status:** Not started

---

## 1. Objective

Replace the current three-to-five-column, gradient-heavy, desktop-only inbox with one responsive component tree that is Gmail-familiar, Slack-shaped, Raven-disciplined, and legible on a 1366×768 @1x panel — **without dropping a single existing feature**.

**Definition of done:** every control that exists in the app today is reachable in the new UI at its assigned tier, the app works from 360 px to 1920 px with no overlap, the legacy tree is still available behind a flag for rollback, and CI blocks regressions on gradients and type size.

---

## 2. Why this phase is first

| Fact (verified in code) | Consequence |
|---|---|
| `ChannelTabsView.tsx` stacks 5 chrome bars — header (`:429`), channel tabs (`:658`), account selector (`:690`), "viewing & replying via" banner (`:724`), pinned (`:755`); messages start at `:785` | On a 768 px-tall laptop most of the screen is chrome |
| `LeftSidebar.tsx` holds 7 filter mechanisms plus navigation to 7 pages | Filtering and navigation are visually indistinguishable |
| 57 `bg-gradient-*` usages across 16 files | The loudest element on screen is "AI Assist", not the reply box |
| 149 sub-12 px text usages (99× `text-[10px]`, 22× `text-[9px]`, 3× `text-[8px]`) | Unreadable at 1× on low-DPI panels |
| Navigation is `useState<AppPage>` in `App.tsx:23–31`; `react-router-dom@6` is installed and **never imported** | No URLs, no shareable views, no working back button on phone |
| One breakpoint at `< 768` (`App.tsx:127`) switching to a separate `components/mobile/` tree | 768–1024 gets the squeezed desktop layout; phone is missing calls, broadcasts, teams, analytics, merge, subscribers |

---

## 3. Scope

### 3.1 In scope

Shell, inbox, conversation surface, record tabs (Chat / Tasks / Notes / Activity), all seven admin pages, mobile parity, tokens, router, keyboard map, and retirement of the legacy tree.

### 3.2 Out of scope — deferred to P3

| Deferred | Why |
|---|---|
| **Details** tab (schema-driven fields) | Needs `crm.get_field_schema` and the custom fields from P3 §3.2 |
| Context strip stage/gate chips | Needs `pipeline_stage`, `gate_flags` |
| `crm-today`, `crm-intake`, `crm-pipeline` pages | Need CRM data |
| Purchase lens beyond a saved-view filter | No purchase pipeline spec exists |

P1 builds the tab bar **with the Details tab present but feature-flagged off**, so P3 is an unhide, not a re-layout.

---

## 4. Work breakdown

### W1 — Design tokens (1–2 d)

**New:** `src/styles/tokens.css`; **modified:** `tailwind.config.js`, `src/index.css`.

CSS custom properties exposed as Tailwind colour aliases so components read `bg-surface-sunken`, `text-ink-2`, `bg-crayon-green-tint`.

Neutrals (chalk): `surface #FFFFFF`, `surface-sunken #F6F7F9`, `surface-hover #EFF1F4`, `surface-active #E7EAEF`, `border #E3E6EA`, `border-strong #CDD3DA`, `ink-1 #1B2129`, `ink-2 #4B5563`, `ink-3 #6B7480`, `ink-muted #98A1AC` (**icons ≥16 px only — fails AA as text**).

Crayon accents as tint/base/text triples, each pre-checked ≥4.5:1:

| Accent | tint | base | text | Meaning |
|---|---|---|---|---|
| blue | `#EDF1FC` | `#6E8FE8` | `#2C4FA8` | Primary, selection, Email |
| green | `#E9F6F0` | `#3FA37A` | `#226B4E` | WhatsApp, success, gate cleared |
| amber | `#FBF2E4` | `#D99A3E` | `#8A5B18` | Pending, SLA at risk |
| rose | `#FBECEB` | `#D9645F` | `#97302D` | Overdue, failed |
| violet | `#F1EFFB` | `#8A7CD8` | `#52459C` | AI / automated |
| teal | `#E8F4F6` | `#3E9AA8` | `#1F5F69` | Accounts lens |
| plum | `#FAEEF5` | `#C86BA0` | `#8A3F6A` | Instagram / Meta |
| sand | `#F5F2E6` | `#9A8A4E` | `#5F5426` | Purchase lens |

Rules: a crayon may fill a chip, a dot, a 3 px left border or a one-line strip — never a panel or page. One accent per row. Channel identity always carries icon + label so colour-blind users lose nothing. No gradients. Shadows capped at `0 1px 2px rgba(27,33,41,.06)`.

Type scale (Inter, weights 400/500/600 only): `2xs` 11/14 tabular **numeric badges only**, `xs` 12/16 meta, `sm` 13/18 default UI, `base` 14/20 message body, `md` 15/20 titles, `lg` 17/24 page titles. Tabular numerals in every counter, timer and table.

**Acceptance:** no component imports a raw hex; `grep -r "#[0-9a-fA-F]\{6\}" src/components` returns nothing.

### W2 — Primitives (2 d)

**New:** `src/components/primitives/` — `Chip`, `Badge`, `Row`, `Toolbar`, `OverflowMenu`, `Sheet`, `Drawer`, `EmptyState`, `Field`, `SegmentedControl`, `Avatar`.

Built on the existing Radix set (`@radix-ui/react-*` already in `package.json`) plus `class-variance-authority`. No new dependency. Every primitive ships a `min-w-0`-safe layout and a coarse-pointer variant (44 px targets).

### W3 — Router (1–2 d)

**Modified:** `App.tsx`; **new:** `src/routes/`.

Adopt the already-installed `react-router-dom@6`. Routes:

```
/inbox/:view?          ?channel= &team= &tag= &from= &to= &q=
/t/:threadId           (record, tab as ?tab=chat|tasks|notes|activity)
/today  /pipeline  /intake            (P3, registered but flagged)
/broadcasts  /analytics  /teams  /merge  /subscribers  /rules  /settings
/dev/stress                            (stress-record harness, dev only)
```

Retires the 17 pieces of ad-hoc state in `App.tsx:54–77` in favour of URL params. **Acceptance:** every filter combination is a copy-pasteable URL; browser back works on phone through list → thread → details.

### W4 — Responsive shell (3 d)

**New:** `AppShell.tsx`, `useBreakpoint.ts`, `Rail.tsx`.

| Name | Range | Layout |
|---|---|---|
| phone | <640 | Single column, drill-in, bottom tabs (Today · Inbox · Pipeline · More), FAB |
| tablet | 640–1023 | Rail 56 + one column; details as sheet |
| laptop | 1024–1439 | Rail 56 + list 320 + record; details as push-drawer (`⌘.`) |
| wide | ≥1440 | Rail + list 360 + record + details persistent |

Rail: 56 px icons, hover-expands to 200 px **as an overlay** (no reflow). Top→bottom: company switcher (hidden with one company), **New**, Today, Inbox, Pipeline, Contacts, divider, Broadcasts, Analytics, divider, avatar menu (Teams, Merge, Subscribers, Rules, Settings, density, sign out).

Chrome above messages fixed at header 48 + context strip 28 + tabs 36 = **112 px**.

### W5 — List column: saved views + search chips (2–3 d)

**New:** `ThreadList.tsx`, `ThreadRow.tsx`, `ViewList.tsx`, `SearchBar.tsx`. **Replaces:** `ChatThreadList.tsx`, the filter stack in `LeftSidebar.tsx`.

Default views: *Unread*, *Assigned to me*, *Unassigned*, *Today's actions*, *SLA risk*, *All*. Ad-hoc filtering becomes search chips (`channel:whatsapp team:export after:2026-08-01`) added by a `+ Filter` button; any chip set can be saved as a view.

Row: avatar 32 · name (600, truncate) · channel dots · time · second line preview + tag dots + SLA pip. Unread = weight + 6 px blue dot, never a full-row tint. Row actions on hover (pointer) / swipe (touch): archive, assign, snooze, mark read.

### W6 — Merged conversation + `Reply via ▾` (4–5 d) — the core change

**New:** `MessageFeed.tsx`, `Composer.tsx`, `ReplyVia.tsx`, `RecordHeader.tsx`, `ContextStrip.tsx`. **Replaces:** `ChannelTabsView.tsx` (1348 lines → five files, none over ~300).

All channels for an identity render in **one chronological feed**, each message badged with channel + account. Channel chips above the feed *filter*; they do not navigate — so unread cannot hide behind an unselected tab.

`Reply via ▾` sits in the composer and absorbs the old account selector *and* the account banner:

| State | UI | Behaviour |
|---|---|---|
| WhatsApp session open | `window 3h 12m` in `ink-3` | Free text allowed |
| WhatsApp session closed | amber `Template required` | Composer swaps to template picker; free text disabled **with a reason**, not a silent failure |
| Email selected | subject + cc/bcc expand in place | — |
| Opted out / spam | rose `Opted out` | Send blocked, reason shown, override logged |
| No access to account | greyed in menu | — |
| Calls (Phase C) | `Call` action | Places call, logs into the same feed |

Defaults to the channel of the last inbound message.

### W7 — Record tabs: Chat / Tasks / Notes / Activity (3–4 d)

All three non-Chat tabs run on primitives that exist in v15 **today** — no CRM fields needed:

| Tab | Backing | Notes |
|---|---|---|
| Tasks | core `ToDo` with `reference_type`/`reference_name` | Inherits Desk assignment + notifications; new hook `useTasks.ts` |
| Notes | core `Comment` on the linked record | Distinct from the in-thread internal note (`api/chat.py:930`), which stays — a note about a *moment* lives in the thread, about a *party* on the record |
| Activity | `Version` + thread system messages | Merged client-side in P1; server endpoint in P3 |
| Details | **flagged off** | P3 |

At ≥1440 the tabs render as a left-pane segmented control with Chat permanently on the right (HLD-003 §3.3.3's two-pane cockpit); below that they collapse to tabs. Same components, different composition.

### W8 — Tier assignment pass (2 d)

Move every existing control to its tier: T1 always visible / T2 hover + `:focus-within` + swipe / T3 `⋯` overflow / T4 ⌘K. Header goes from six competing controls (`ChannelTabsView.tsx:497–560`) to name + record chip + `Reply via ▾`, with Transfer, Tags, AI assist, Open in Desk and Mark spam in `⋯`. Panel toggles disappear — width decides, `⌘.` overrides.

Touch devices (`pointer: coarse`) render T2 as a visible per-row `⋯` instead of hover.

### W9 — Command palette (1–2 d)

`CommandPalette.tsx` — ⌘K over threads, records, saved views, commands, settings. Fuzzy match, recent-first, keyboard-only operable.

### W10 — De-gradient + type floor (1–2 d)

Remove all 57 gradient usages; raise all 149 sub-12 px usages to the W1 scale. Add CI grep gates (W13).

### W11 — Admin pages (3–4 d)

Re-skin onto tokens + primitives and **make responsive** (none are today): `BroadcastPage` (1644 → wizard: Audience → Content → Schedule → Review, each step fitting 768 px), `SubscriberListPage` (827), `SubscriberRulesPage` (662), `AnalyticsPage` (728, `recharts` themed to tokens, ≥2 px strokes, table fallback under 640), `TeamManagementPage` (515), `MergeSuggestionsPage` (228), `SettingsPage` (697).

### W12 — Mobile parity and legacy retirement (2–3 d)

Delete `components/mobile/` (7 files, ~1900 lines) once the checklist passes. Phone gains what it never had: broadcasts, teams, analytics, merge, subscribers, and a real calls surface placeholder tied to Phase C rather than a hardcoded "Coming Soon" (`MobileApp.tsx:78–96`).

Then remove `ChannelTabsView.tsx`, the old `LeftSidebar.tsx`, `ChatThreadList.tsx` and the legacy route — **after** P2 signs off, not before.

### W13 — CI gates and the stress harness (1 d)

- `grep` gates: no `bg-gradient-`, no `text-[8|9|10|11px]` outside the badge allowlist, no raw hex in `src/components`, no imports from `components/mobile` (post-W12).
- `src/fixtures/stress.ts` + `/dev/stress` route rendering every component with the worst case: 48-char company name, +91 15-digit number, 6 tags, 4 channels, ₹1,23,45,678, 3-line preview.

---

## 5. Overlap-safety rules (enforced in review)

1. `min-w-0` on the parent chain, `truncate` on the text leaf.
2. Growing chip rows are `overflow-x-auto` with a fade mask — never `flex-wrap` inside a fixed-height bar.
3. Header action clusters collapse to `⋯` below **container** width 1100, not viewport.
4. Grid columns are `minmax(0, 1fr)`, never `1fr`.
5. No absolutely positioned elements over text; badges are inline-flex siblings.
6. Every list/detail component renders correctly in `/dev/stress`.

---

## 6. Parallel running

One bundle, two trees. Resolution: `?ui=next` → `localStorage.excom_ui` → per-user flag on `Excom Settings` → default `legacy`. Switch link in both avatar menus with a one-line feedback box that captures route + viewport + DPR automatically. Shared `hooks/*` — one data layer, no double maintenance. **Legacy frozen (bug fixes only) from day 1.**

---

## 7. Testing in this phase

Development-time only; the full sweep is P2.

- Per-widget check against `/dev/stress` before review.
- Screenshot sweep at 360 / 640 / 768 / 1024 / 1366 / 1440 for each merged work item.
- Low-DPI eyeball at 1366×768 @1x, 125 % Windows scaling, on every merge that touches type or spacing.

---

## 8. Exit gates

| # | Gate |
|---|---|
| E1 | Control checklist: all 42 controls mapped old → new, none orphaned |
| E2 | Chrome ≤112 px above messages at 1366×768; message area ≥400 px |
| E3 | No overlap / horizontal scroll at 360, 390, 414, 640, 768, 834, 1024, 1280, 1366, 1440, 1920 — DPR 1 and 2, zoom 100/125/150 % |
| E4 | CI gates green (W13) |
| E5 | Back button works on phone: list → thread → details |
| E6 | Keyboard map complete: `⌘K`, `j/k`, `⏎`, `e`, `a`, `/`, `⌘⏎`, `⌘.`, `g` then `i/t/p` |
| E7 | Tasks and Notes tabs create real `ToDo` and `Comment` records against the linked party |

---

## 9. Risks

| # | Risk | Mitigation |
|---|---|---|
| R1 | Merged thread confuses channel-thinking users | Ship a "Group by channel" toggle; measure usage in P2 before removing |
| R2 | `ChannelTabsView` (1348 lines) hides undocumented behaviour | Extract the five regions **in place** first, re-skin second — never rewrite blind |
| R3 | Two UIs double the bug surface | Legacy frozen; shared hooks; hard delete after P2 |
| R4 | Density reads as cramped | Comfortable/Compact toggle changes row heights only, never type size |
| R5 | Admin page re-skin balloons (Broadcasts is 1644 lines) | Wizard split is the only structural change; everything else is token swap |

---

## 10. Effort

| Work item | Days |
|---|---|
| W1 tokens · W2 primitives · W3 router | 4–6 |
| W4 shell · W5 list | 5–6 |
| W6 conversation + Reply via | 4–5 |
| W7 record tabs · W8 tiers · W9 palette | 6–8 |
| W10 de-gradient · W11 admin pages · W12 parity · W13 CI | 7–9 |
| **Total (with overlap)** | **16–24** |
