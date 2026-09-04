# Permission and linkage audit — 2026-09-04

Read-only sweeps against the running site (`erpnextkgopl.local`), not a reading of the source:
43 Excom doctypes, 102 link fields, the effective permission matrix as Frappe computes it, every
dotted path in `hooks.py`, every field name grepped across Python/TS/JSON, and the visibility rules
asked the same question about the same row as the same user through each entry point.

Report: https://claude.ai/code/artifact/5e16603c-aa40-4e1b-b077-3e03f34f4d7a

## Fixed

| id | finding |
|---|---|
| SEM-1 | Thread visibility had **three** implementations that disagreed. Same user, same thread: excom api `True`, has_permission hook `False`, Desk list `False`. All four call sites now delegate to `excom_thread.can_access()`, with the list query as its SQL twin. Regression test `test_thread_visibility.py` asks each entry point separately. |
| SEM-2 | A claimed thread (owner, no team) still read as unclaimed to the document rule, so the whole General inbox saw work someone had taken. Found by the new test. |
| SEM-3 | Two tie-breaks for "which team does this user's work belong to": `sync_thread_owner` took the first membership row, the CRM side takes the deepest. Both now call `team_for_user()`. |
| SEM-4 | `team_for_user` stamped a team for Administrator, because `get_user_teams()` reports every team for Administrator. Now reads real membership rows and refuses Administrator/Guest. |
| PERM-1 | `Excom User` had **read only** on `Excom Thread` and `Excom Message`. Sending worked only because the API bypasses permissions. Now read+write+create. |
| PERM-2 | `Sales Master Manager` had **no permission on Lead at all** in stock ERPNext (it is a master-data role). The top of the lead model could not read a lead. Granted rwcd on Lead and Opportunity. |
| PERM-3 | The Excom roles carried no CRM permission, so all five agents ran on a blanket `Sales Manager` — rwd on every lead in the company. |
| LINK-1 | `Excom Lead.intake_source` pointed at the removed `Excom Intake Source`. The source was right; the database copy was not, because `install()` returned early on "it exists". Added `reconcile()`. |
| LINK-2 | Three stored links pointed at deleted records — all residue from this session's own probes. Production data has no dangling references. |
| PERF-1 | Eight missing indexes, including `Excom Team Member.user`, now read on every permission check. All declared in `on_doctype_update`. |

## Verified clean

- No Excom doctype with zero permissions; none granting write to `All`; no permission naming a non-existent role.
- Writing Custom DocPerms lost no standard role on any of the nine doctypes touched (Frappe copies them first).
- Every `hooks.py` target imports; every doctype named in `doc_events` exists.
- No dead fields: every field on every Excom doctype is referenced somewhere.
- `"System User"` in `api/admin.py`, `api/email.py`, `api/teams.py` is a `user_type` check, not a role — correct.

## Open, needs the owner

1. Four of five agents are in no team (General has 0 members) → empty inbox. Auto-join only covers future grants.
2. Team tree is flat: 3 teams, no `parent_team`, 1 member total. The hierarchy rule has nothing to walk.
3. `enforce_crm_visibility` still off. Blocked on 1 and 2: the backfill can only stamp a team when the owner is in one.
4. `Sales Master Manager` sees every lead and no conversations (not an Excom role).
5. `Excom Manager` sees every thread but only their teams' leads — the mirror image of 4.
6. Naming drift: threads carry `assigned_team`, leads carry `excom_team`.
