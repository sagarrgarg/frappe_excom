#!/usr/bin/env bash
# W13 — CI grep gates for the P1 tree. Frozen legacy files (deleted after P2) are excluded.
# Usage: yarn gates
set -u
cd "$(dirname "$0")/.."
FROZEN='src/components/ChannelTabsView.tsx|src/components/LeftSidebar.tsx|src/components/ChatThreadList.tsx|src/components/mobile/|src/LegacyApp.tsx'
BADGE_ALLOW='src/components/primitives/Badge.tsx'
fail=0
files() { grep -rlE --include='*.tsx' --include='*.ts' --include='*.css' "$1" src | grep -vE "$FROZEN" || true; }

echo "gate 1: no bg-gradient-*"
out=$(grep -rnE --include='*.tsx' --include='*.ts' 'bg-gradient-' src | grep -vE "$FROZEN"); [ -n "$out" ] && { echo "$out"; fail=1; }

echo "gate 2: no text-[8|9|10|11px] outside the badge allowlist"
out=$(grep -rnE --include='*.tsx' --include='*.ts' 'text-\[(8|9|10|11)px\]' src | grep -vE "$FROZEN|$BADGE_ALLOW"); [ -n "$out" ] && { echo "$out"; fail=1; }

echo "gate 3: no raw hex in src/components (tokens only)"
out=$(grep -rnE --include='*.tsx' --include='*.ts' '#[0-9a-fA-F]{6}\b' src/components | grep -vE "$FROZEN"); [ -n "$out" ] && { echo "$out"; fail=1; }

echo "gate 4: no imports from components/mobile outside the legacy tree"
out=$(grep -rnE --include='*.tsx' --include='*.ts' "components/mobile|\./mobile/" src | grep -vE "$FROZEN"); [ -n "$out" ] && { echo "$out"; fail=1; }

echo "gate 5: no font-weight 300/700 or letter-spacing in the new tree"
out=$(grep -rnE --include='*.tsx' --include='*.ts' '\b(font-light|font-bold|tracking-tight|tracking-tighter)\b' src | grep -vE "$FROZEN"); [ -n "$out" ] && { echo "$out"; fail=1; }

echo "gate 6: grid columns use minmax(0,1fr) — flag bare grid-cols-N with unbounded children"
out=$(grep -rnE --include='*.tsx' 'grid-cols-[2-9]\b' src/components/record src/components/inbox src/components/shell | grep -v 'minmax' || true); [ -n "$out" ] && { echo "(warn) $out"; }

if [ "$fail" = "1" ]; then echo "UI GATES FAILED"; exit 1; fi
echo "UI GATES OK"
