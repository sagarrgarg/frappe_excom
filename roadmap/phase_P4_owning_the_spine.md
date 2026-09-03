# Phase P4 — Owning the Spine

**Phase:** P4 of 5 (`PLAN_001_master_phasing.md`)
**Design source:** `design/RES_001_native_crm_lock_and_intake.md` §3, §4
**Duration:** 6–9 days (contingency fork: +16–25 days, only if triggered)
**Depends on:** P3 §3.1 (gateway, compat shim, manifest) existing and being obeyed
**Status (2026-09-03):** 4.2 contract suite (9 cases, native + shadow green), 4.3 manifest enforcement (`services/crm_manifest.py`, in the test suite), 4.4 shadow `Excom Lead` (`services/crm_shadow.py`; one leak found and fixed), 4.5 `design/fork_plan.md` done. 4.1 done at schema level against upstream v16 (`v16_upgrade_runbook.md`); the live scratch-bench run waits for a machine with disk. Finding: v16 adds a native `Customer.customer_type` that collides with ours — rename before upgrading (runbook).

---

## 1. Objective

Make the dependency on ERPNext native CRM **reversible and version-proof**. This phase does not fork anything. It proves we *could* — cheaply, on demand — and it handles the one v16 change that actually affects us.

**Definition of done:** the site upgrades to v16 in a scratch bench with attribution intact and every gateway contract test green; CI fails loudly when a native field we depend on disappears; and a shadow implementation passes the same contract tests as native, proving the seam is real rather than theoretical.

---

## 2. What the research established

| # | Finding | Source |
|---|---|---|
| A | **Native CRM is not deprecated in v16.** `frappe/erpnext@develop` still ships the full `erpnext/crm` module — Lead, Opportunity with `items[]` + `sales_stage` + `probability`, Prospect (identical field list to v15), lost reasons, competitors, Sales Stage | Live fetch of `develop` |
| B | **One change does affect us.** `Lead.source`, `Lead.campaign_name`, `Opportunity.source`, `Opportunity.campaign` are removed and the `Lead Source` doctype is deleted, replaced by `utm_source` / `utm_medium` / `utm_campaign` / `utm_content` targeting frappe-core `UTM Source` / `UTM Medium` / `UTM Campaign` (`frappe/website/doctype/`) | `lead.json`, `opportunity.json` on `develop` |
| C | **The migration is automatic.** `erpnext/patches/v15_0/migrate_to_utm_analytics.py` copies every `Lead Source` into `UTM Source` and every `Campaign` into `UTM Campaign`, then deletes the `Lead Source` doctype | Patch source |
| D | **Direction of travel is real but gradual.** ERPNext v15 already ships a bridge toward Frappe CRM (`erpnext/crm/frappe_crm_api.py`, the "Frappe CRM" section in `CRM Settings` with `allowed_users` + `enable_frappe_crm_data_synchronization`), and v16 hands attribution primitives to core | Installed v15 source |

Conclusion: insurance, sized deliberately. Not panic, not complacency.

---

## 3. Work breakdown

### 4.1 — v16 dry run (2–3 d)

1. Restore the site backup into a scratch bench (never against production).
2. Upgrade `frappe` and `erpnext` to v16 (`develop` at pinning time); excom's `pyproject.toml` already declares `frappe = ">=15.0.0,<17.0.0"` and `erpnext = ">=15.0.0,<17.0.0"`, so installs are not gated.
3. `bench migrate`, then verify:
   - `migrate_to_utm_analytics` ran; every former `Lead Source` exists as a `UTM Source`; every `Campaign` exists as a `UTM Campaign`.
   - Historical Leads/Opportunities retain their attribution (spot-check 20 records across sources).
   - `crm_compat.set_attribution()` now takes the v16 branch and writes `utm_*` — verified by creating a Lead through each intake adapter.
   - Excom fixtures (custom fields from P3 §3.2) apply cleanly; `pipeline_stage`, `gate_flags`, `next_action_at` intact.
   - `api/crm.py` endpoints return the same shapes; `get_field_schema` reflects the new field set.
   - Intake adapters, auto-ack and SLA jobs run end to end.
4. Produce `roadmap/v16_upgrade_runbook.md`: order of operations, expected patch output, verification queries, rollback procedure, and the list of excom fixtures that must be re-applied.

**Acceptance:** the scratch site runs a full P3 exit-gate scenario (E1–E4) on v16 without code changes outside `crm_compat.py`.

### 4.2 — Gateway hardening (1–2 d)

Contract tests for every gateway function, run against **both** schema shapes:

| Test | Assertion |
|---|---|
| `create_lead` | Returns a ref; provenance stamped; identity linked; thread re-pointed |
| `set_attribution` | v15 → `source`/`campaign_name` populated; v16 → `utm_*` populated; both create the target row if missing |
| `advance_stage` | Gate evaluation runs; `stage_entered_at`, mapped `sales_stage`, `probability` written; stage-change log row created |
| `convert` | Identity links added and prior links retained; open threads re-pointed; provenance copied; system message posted |
| `get_record` | Record + identity + threads + documents in one round trip |
| `list_pipeline` | Grouped by `pipeline_stage`, thread-freshness joined, company/territory permissions honoured |
| `promote_thread` | Wraps `make_lead_from_communication`; no duplicate when the identity already has an open record |

The suite runs in CI against the installed version, and in the scratch bench against v16.

### 4.3 — Manifest enforcement (1 d)

`roadmap/design/native_crm_manifest.yaml` (created in P3 §3.1) lists every native doctype, field and helper excom depends on, each with a reason. Two CI checks:

1. **Completeness** — every native fieldname referenced inside `crm_gateway.py` / `crm_compat.py` appears in the manifest. A new dependency cannot be added silently.
2. **Existence** — every manifest entry still exists on the installed version, checked via `frappe.get_meta`. When v16 removes `Lead.source`, this fails with `Lead.source: declared in manifest, absent on installed erpnext 16.x — see RES-001 §3.2` rather than with a runtime `AttributeError` three weeks later.

Current manifest content (from RES-001 §4.2): doctypes `Lead`, `Opportunity`, `Opportunity Item`, `Prospect`, `Customer`, `Contact`, `Party Link`, `Quotation` (read-only), `Company`, `Territory`, `Sales Stage`, `Opportunity Lost Reason`, `Incoterm`; helpers `make_opportunity`, `make_customer`, `make_quotation`, `make_lead_from_communication`; the field reuse list from HLD-003 §2.2 **minus** `source` / `campaign_name`.

### 4.4 — Fork rehearsal (2–3 d)

Build `Excom Lead` as a **shadow doctype**: same conceptual fields, excom-owned, implemented behind the identical gateway interface. Not installed on any real site, not in `fixtures`, not exposed in the UI — it exists to run the contract tests from 4.2 against a non-native backend in CI.

What this proves, concretely: that no caller reaches around the gateway, that the mappers are complete, and that a real fork is a mechanical exercise. What it deliberately does not do: fork `Opportunity` (bigger, includes `items[]` pricing) — the Lead rehearsal is sufficient to validate the seam at a fraction of the cost.

**Acceptance:** the contract suite passes with the gateway pointed at the shadow doctype; a deliberate direct-native call inserted anywhere else makes CI fail.

### 4.5 — Costed fork plan (folded into the above)

`roadmap/design/fork_plan.md` — what a real fork replaces, with the trigger criteria:

| Native capability | Replacement effort |
|---|---|
| Lead / Opportunity / Prospect schema + naming series | 3–4 d |
| The four `make_*` transition mappers | 3–5 d (Quotation/SO field mapping is the fiddly part) |
| `Opportunity Item` + pricing / currency / conversion | 4–6 d, or keep quoting in Desk and link by name |
| Sales Stage / probability / lost reasons | 1–2 d |
| Native funnel and sales reports | 3–5 d, or accept excom analytics |
| Company / Territory User Permissions, `Sales Person` tree | 2–3 d |
| **Total** | **16–25 d** with the gateway; 2–3× without |

**Never forked, in any scenario:** `Quotation`, `Sales Order`, `Delivery Note`, `Sales Invoice`. Those are ERP documents, not CRM. HLD-003 §12 is explicit that Desk's Quotation is good and should not be rebuilt.

**Trigger criteria — pull the fork trigger only when one of these is true:**

1. ERPNext announces removal of the `crm` module from a supported release.
2. A release removes a manifest entry that has no compat path (i.e. worse than the `source` → `utm_source` case, which the shim absorbs).
3. Native CRM becomes read-only or gated behind a paid/hosted-only integration.
4. Multi-company requirements outgrow what native fields express **and** the workaround cost exceeds the fork cost.

Absent a trigger, P4 ends after 4.4 and is revisited at each major ERPNext release.

---

## 4. Ongoing obligations after this phase

| # | Obligation | Cadence |
|---|---|---|
| O1 | Manifest existence check runs in CI | Every build |
| O2 | Gateway contract suite runs against the installed version | Every build |
| O3 | v16 (then v17) dry run repeated on the scratch bench | Each major ERPNext release |
| O4 | `assert_native_crm_only()` guardrail keeps reporting | Daily (from P3) |
| O5 | Review whether any trigger criterion has been met | Each major release, and whenever ERPNext announces CRM roadmap changes |

---

## 5. Exit gates

| # | Gate |
|---|---|
| E1 | Scratch bench upgraded to v16; `migrate` clean; attribution migrated; P3 scenarios E1–E4 pass unchanged |
| E2 | `v16_upgrade_runbook.md` written, including rollback |
| E3 | Gateway contract suite green against v15 and v16 |
| E4 | Manifest completeness + existence checks in CI; a removed field produces a clear, actionable failure |
| E5 | Shadow `Excom Lead` passes the same contract suite; a native call outside the gateway fails CI |
| E6 | `fork_plan.md` committed with costs and trigger criteria |

---

## 6. Risks

| # | Risk | Mitigation |
|---|---|---|
| R1 | P3 leaked native doctype names outside the gateway, so 4.4 fails | The CI grep from P3 E6 catches it during P3, not here; if it does fail, the fix is refactoring, not rescoping |
| R2 | v16 changes something beyond attribution that the dry run misses | The dry run executes full P3 scenarios, not just a migrate; spot-check 20 historical records |
| R3 | The shadow doctype rots as an unused code path | It runs in CI on every build — a rotting shadow breaks the build |
| R4 | "We'll never need this" pressure removes the phase | It is 6–9 days against a 16–25 day contingency and an unknown-probability trigger; the dry run alone justifies it, since v16 is coming regardless |
| R5 | Frappe CRM becomes the only supported CRM UI and pulls native fields with it | O5 review; the gateway means the response is a scoped fork, not a rewrite |

---

## 7. Effort

| Item | Days |
|---|---|
| 4.1 v16 dry run + runbook | 2–3 |
| 4.2 gateway contract tests | 1–2 |
| 4.3 manifest enforcement | 1 |
| 4.4 fork rehearsal (shadow doctype) | 2–3 |
| 4.5 costed fork plan | folded in |
| **Total** | **6–9** |
| *Contingency: execute the fork* | *16–25, only on trigger* |
