# Lead visibility and team hierarchy

Decided by the owner, 2026-09-04: **a lead nobody has been given to is visible to a Sales Master
Manager and to nobody else.** It reaches anyone else exactly three ways — a Sales Master Manager
hands it over, a sales head places it on their team, or auto-assignment does.

## The rule

`excom/excom/services/crm_visibility.py` is the only place that answers "who may see this record".
A user sees a Lead or Opportunity when any of these holds:

| condition | field |
|---|---|
| they own it | `lead_owner` / `opportunity_owner` |
| they created it | `owner` |
| it is assigned to them | `_assign` (the ToDo) |
| it sits on a team they are in | `excom_team` |
| it sits on a team below one they manage | `excom_team` + `parent_team` walk |

Bypassing the rule entirely: `Administrator`, `System Manager`, `Sales Master Manager`. That list is
deliberately short, because every role on it is another person who sees an unplaced lead.

Enforced through `permission_query_conditions` and `has_permission` on Lead and Opportunity, so the
Desk list, a report, the REST API and the Excom UI all get the same answer. `api/crm.py`
`lead_visibility()` stands down while enforcement is on rather than filtering a second time.

## The switch

`Excom Settings > enforce_crm_visibility`. Off by default, including on a fresh install. Off means
ERPNext's own role permissions decide, exactly as before this existed.

## Two things ERPNext does not do on its own

1. **`Sales Master Manager` has no permission on Lead or Opportunity at all** in stock ERPNext — it
   is a sales master-data role. The model needs somebody at the top, so `setup/crm_permissions.py`
   grants it read/write/create/delete on both.
2. **`Excom User` had no CRM permission either**, so every agent worked through a blanket
   `Sales Manager`, which reads, writes and deletes every lead in the company. The Excom roles now
   carry their own reach and the scope above narrows it.

## Before turning it on

```bash
bench --site <site> execute excom.excom.services.crm_visibility.impact_report
bench --site <site> execute excom.excom.services.crm_visibility.backfill_teams
```

`backfill_teams` puts a team on every record that already has an owner, so people keep the leads
they are already working. Records with no owner are left alone on purpose: those are the ones meant
to go back to the top and be handed out.

Reverting the permission grants: `bench --site <site> execute excom.setup.crm_permissions.revert`.

## Team hierarchy

`Excom Team.parent_team` is the tree. A member sees their own team; a member whose row says
`Manager` also sees every team beneath theirs. `stamp_team()` writes `excom_team` the first time a
record is handed to someone, from any path — the owner field changing, claim-on-talk, a Desk
assignment, or an Assignment Rule (all four land as a ToDo, which is where the hook sits). An
existing team is never overwritten, so a sales head's placement outranks a later claim.
