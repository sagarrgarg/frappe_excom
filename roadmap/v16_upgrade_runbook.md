# ERPNext v16 upgrade runbook (P4 §4.1)

**Dry run done 2026-09-03 — schema level.** The dev box has 4 GB free and the site is 4 GB, so the live scratch-bench restore was not possible there. Instead every manifest dependency was checked field-by-field against `frappe/erpnext@version-16` (16.34.1) and `frappe/frappe@version-16`:

```
bench --site <site> execute excom.excom.services.crm_manifest.check --kwargs "{'schema_dir': '<dir with the v16 *.json>'}"
→ MANIFEST OK against v16
```

## What v16 changes for us (verified against the upstream JSON)
| Doctype | Removed | Added | Excom impact |
|---|---|---|---|
| Lead | `source`, `campaign_name` | `utm_source`, `utm_medium`, `utm_campaign`, `utm_content` | none — `crm_compat.set_attribution()` branches on `meta.has_field("utm_source")` |
| Opportunity | `source`, `campaign` | same `utm_*` set | none — same shim |
| Prospect | — | — | none |
| Customer | layout only (`salutation` gone) | `customer_type` (**native field with the same name as our custom field**), `alias`, supplier numbers | see step 4 below |
| Lead Source (doctype) | deleted by `erpnext/patches/v15_0/migrate_to_utm_analytics.py` | rows copied to `UTM Source`; Campaigns copied to `UTM Campaign` | manifest marks it `until: v16` |
| helpers `make_opportunity / make_customer / make_quotation / make_lead_from_communication` | — | still present in `lead.py` / `opportunity.py` | none |

Everything else excom reads or writes on Lead, Opportunity, Prospect, Customer, Contact, Sales Stage, Assignment Rule, ToDo exists unchanged.

## Customer.customer_type — already handled
ERPNext v15 *and* v16 ship a native `Customer.customer_type` (Company / Individual / Partnership). P3 had created a custom field of the same name, which replaced the native definition and broke Customer validation; patch `excom.patches.v1_0.fix_customer_type_clobber` (2026-09-03) removes it and excom's field is `excom_customer_type`. `crm_schema.apply()` now refuses to shadow any native field, so v16's new fields cannot collide either.

## Live scratch-bench procedure (needs ~15 GB free, never production)
```bash
bench init scratch16 --frappe-branch version-16 && cd scratch16
bench get-app erpnext --branch version-16
bench get-app /path/to/apps/excom            # or the git URL, branch main
bench new-site scratch.local --db-root-password … --admin-password …
bench --site scratch.local restore /path/to/<site>-database.sql.gz --with-public-files … --with-private-files …
bench --site scratch.local install-app erpnext excom   # if the backup predates excom
bench --site scratch.local migrate                    # runs migrate_to_utm_analytics + excom p3_crm_schema
```
Verify:
```bash
bench --site scratch.local execute excom.excom.services.crm_manifest.check          # MANIFEST OK against installed erpnext 16.x
bench --site scratch.local execute excom.excom.tests.run.run                         # 22 tests: core flows + gateway contract
bench --site scratch.local execute excom.excom.tests.run.run --kwargs "{'module':'excom.excom.tests.test_gateway_contract','backend':'shadow'}"
bench --site scratch.local execute frappe.db.sql --args "[\"SELECT COUNT(*) FROM \`tabUTM Source\`\"]"   # = former Lead Sources
bench --site scratch.local execute frappe.db.sql --args "[\"SELECT name, utm_source FROM \`tabLead\` WHERE utm_source IS NOT NULL LIMIT 20\"]"  # attribution survived
```
Then click through: Intake → Classify → Convert → Pipeline stage change → Close, and submit one enquiry through `api/intake.submit_enquiry` to see `utm_source` written by the shim.

## Rollback
The scratch bench is disposable. For production: `bench --site <site> restore <pre-upgrade backup>` in the v15 bench; nothing in excom writes to a v16-only field while running on v15.

## Excom fixtures to confirm after `migrate`
`crm_schema.apply()` runs on `after_migrate`; check `Lead.pipeline_stage`-family fields, `Opportunity.gate_flags`, `next_action_at`, `Excom Thread.closure_*` exist (`crm_manifest.check` covers the CRM ones).
