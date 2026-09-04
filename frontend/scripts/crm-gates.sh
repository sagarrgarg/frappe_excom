#!/usr/bin/env bash
# P3 F3/G7/E6/E8 — native doctype strings only in the gateway; no Frappe CRM references; attribution via the shim.
set -u
cd "$(dirname "$0")/../.."
fail=0
# Pre-P3 identity code (identity_hooks/identity_sync/subscriber lists) is grandfathered; new code must go through the gateway.
ALLOW='excom/excom/demo/|excom/excom/services/crm_gateway.py|excom/excom/services/crm_compat.py|excom/excom/services/crm_shadow.py|excom/excom/services/crm_manifest.py|excom/excom/tasks/guardrails.py|excom/setup/crm_schema.py|excom/patches/|excom_subscriber_list.py'
echo "gate F3: \"Lead\"/\"Opportunity\"/\"Prospect\"/\"Quotation\" doctype strings outside the gateway"
out=$(grep -rnE --include='*.py' '"(Lead|Opportunity|Prospect|Quotation)"' excom | grep -vE "$ALLOW|/test_|identity_hooks.py|identity_sync.py|api/chat.py|services/broadcast|subscribers.py|subscriber_rules|api/analytics|hooks.py|doctype/omni_identity/" ); [ -n "$out" ] && { echo "$out"; fail=1; }
echo "gate G7: no Frappe CRM references"
out=$(grep -rnE --include='*.py' --include='*.ts' --include='*.tsx' --include='*.json' 'CRM Lead|CRM Deal|fcrm|from crm[ .]|import crm( |$|\.)' excom frontend/src | grep -vE 'guardrails.py|excom/patches/frappe_crm_migration.py'); [ -n "$out" ] && { echo "$out"; fail=1; }
echo "gate E8: attribution only through set_attribution"
out=$(grep -rnE --include='*.py' '\.(source|campaign_name|utm_source|utm_campaign|utm_medium) *= ' excom | grep -vE 'crm_compat.py'); [ -n "$out" ] && { echo "$out"; fail=1; }
[ "$fail" = 1 ] && { echo "CRM GATES FAILED"; exit 1; }
echo "CRM GATES OK"
