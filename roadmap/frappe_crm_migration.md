# Frappe CRM → native CRM migration (runbook)

**Script:** `excom/patches/frappe_crm_migration.py` · idempotent, resumable, marker-based (`source_reference = "CRM Lead:<name>"` / `"CRM Deal:<name>"`).
**Rehearsed:** 2026-09-03 on the dev copy of `erpnextkgopl.local` — 1,981 CRM Leads → 1,981 Leads, 18 CRM Deals → 18 Opportunities, 0 errors after the duplicate-email rule; rollback + re-run verified. ~3.5 min for the full set.

## What it does
| Frappe CRM | Native | Notes |
|---|---|---|
| CRM Lead (same name kept) | Lead | status New→Lead · Contacted→Replied · Nurture/Qualified→Interested · Unqualified/Junk→Do Not Contact; `intake_stage` set; `first_touch_at` = creation; creation/owner preserved |
| CRM Deal | Opportunity (from the migrated Lead, or Customer, or a stub Lead) | Qualification→Open/Qualified · Negotiation→Open/Negotiation · Won→Closed/Won · Lost→Lost; amount, currency, closing date, probability, owner; the Lead becomes Opportunity/Converted |
| Comments + FCRM Notes | Comments on the new record | timeline history survives |
| Lead Source / Territory / Industry / Gender / Salutation | copied only when the native master exists | unknown values dropped, never a broken link |
| duplicate emails (ERPNext allows one Lead per email) | first Lead keeps it, later ones get a comment naming the Lead that has it | 92 such cases on this data |
| Omni Identity | linked automatically by phone/email through the normal Lead hooks | chats show the Lead chip |

Series counters (`CRM-LEAD-.YYYY.-`, `CRM-OPP-.YYYY.-`) are bumped so new native records never collide. Nothing in Frappe CRM is modified or deleted.

## Production steps
```bash
cd /path/to/bench
bench --site <site> backup --with-files                       # 1. backup
bench --site <site> execute excom.patches.frappe_crm_migration.run --kwargs "{'dry_run': 1}"   # 2. counts only
bench --site <site> execute excom.patches.frappe_crm_migration.run --kwargs "{'dry_run': 0, 'limit': 20}"  # 3. rehearse 20
bench --site <site> execute excom.patches.frappe_crm_migration.run --kwargs "{'dry_run': 0}"   # 4. everything (safe to re-run)
bench --site <site> execute excom.patches.frappe_crm_migration.report                           # 5. verify
```
Expect `errors: 0`. Failures (if any) are in Error Log under `CRM migration: …` and re-running picks them up.

Undo everything the script created:
```bash
bench --site <site> execute excom.patches.frappe_crm_migration.rollback_migrated --kwargs "{'confirm': 'yes'}"
```

## After the data is over (manual, your call)
1. Turn off the bridge: Desk → ERPNext CRM Settings → uncheck *Enabled* (stops Frappe CRM from creating ERPNext records).
2. Tell staff to work from Excom → Intake / Pipeline. The Frappe CRM app can stay installed read-only for reference.
3. Uninstall later once nobody opens it: `bench --site <site> uninstall-app crm` (drops its tables — take a backup first).
