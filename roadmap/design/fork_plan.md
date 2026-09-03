# Fork plan — when ERPNext's native CRM stops being good enough

**Status:** contingency, not scheduled. Rehearsed 2026-09-03: the gateway contract suite (9 cases) passes unchanged against native `Lead` and against the excom-owned shadow `Excom Lead` (`services/crm_shadow.py`). One leak was found and fixed during the rehearsal (classification lived in a Lead-only doc hook; it now lives in the gateway).

## Trigger criteria — pull the trigger only when one is true
1. ERPNext announces removal of the `crm` module from a supported release.
2. A release removes a manifest entry with no compat path (worse than `source → utm_source`, which `crm_compat` absorbs).
3. Native CRM becomes read-only or gated behind a paid / hosted-only integration.
4. Multi-company needs outgrow native fields **and** the workaround costs more than the fork.

Reviewed at every major ERPNext release (O5). Until then: ERPNext is the spine, excom is the UI and the workflow.

## What a fork replaces
| Native capability | Replacement | Effort |
|---|---|---|
| `Lead` schema + naming series | `Excom Lead` (shadow already defines the field list; promote it from Custom DocType to an app doctype, add naming series `CRM-LEAD-.YYYY.-` compat) | 1–2 d |
| `Opportunity` + `pipeline_stage` / gates / `sales_stage` / `probability` | `Excom Opportunity` without `items[]`; quoting stays in Desk, linked by name | 3–4 d |
| `Prospect` | drop — the Omni Identity already is the account container | 0 |
| `make_opportunity`, `make_customer` | gateway `convert` already carries the overlay fields; re-implement the two mappers (≈60 lines each) | 1–2 d |
| `make_quotation` (Lead / Opportunity → Quotation) | keep native: Quotation is an ERP document. Create with `party_name` = Customer, `quotation_to = Customer`; before a Customer exists, quote from Desk with the Excom record linked by `source_reference` | 2–3 d |
| `Opportunity Item` + pricing | not forked — pricing lives on Quotation | 0 |
| Sales Stage / probability / lost reasons | Select fields on `Excom Opportunity`; keep `Opportunity Lost Reason` as a master | 1 d |
| Native funnel / sales reports | excom analytics (funnel per customer type, stage durations from `Excom Stage Change Log`) | 3–5 d |
| Company / Territory user permissions, Sales Person tree | User Permissions apply to any doctype with a `company` / `territory` Link — unchanged | 0–1 d |
| Doc hooks (`on_lead_created/updated`, `on_opportunity_*`) | re-register in `hooks.py` for the excom doctypes | 0.5 d |
| Migration of live records | same shape as `patches/frappe_crm_migration.py` (marker-based, resumable) | 1–2 d |
| **Total** | | **13–20 d** with the gateway (vs 2–3× without) |

**Never forked:** Quotation, Sales Order, Delivery Note, Sales Invoice, Customer, Contact.

## Guardrails that make the fork mechanical
- `frontend/scripts/crm-gates.sh` — only `crm_gateway.py`, `crm_compat.py`, `crm_shadow.py`, `crm_manifest.py`, `guardrails.py`, `crm_schema.py`, `patches/` may name the native doctypes.
- `services/crm_manifest.py` — completeness (gateway field names are declared) + existence (declared names exist on the installed version, or on an upstream schema directory for a dry run).
- `tests/test_gateway_contract.py` — same assertions for both backends; `run.run(backend="shadow")` on a scratch site.

## Sequence, if triggered
1. Promote the shadow doctype to `excom/excom/doctype/excom_lead` (+ `excom_opportunity`), add hooks, naming series, permissions.
2. Point `crm_gateway.LEAD / OPPORTUNITY` at them permanently; delete `crm_shadow.use()`.
3. Re-run the contract suite; migrate live records with the marker pattern; flip `crm_doctypes()`.
4. Rebuild the two reports; keep quoting in Desk.
