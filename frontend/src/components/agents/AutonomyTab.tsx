import { useEffect, useMemo, useState } from "react";
import { Lock, Save, TrendingUp, X } from "lucide-react";
import { toast } from "sonner";
import { Button, Chip, Field, Input, Select } from "../primitives";
import { cn } from "../ui/utils";
import type { AgentDetail, Mode, PolicyRow } from "../../types/agents";
import { M, MODE_HINT, Panel, POLICY_LABEL, useAgentAction } from "./bits";
import { useLive } from "./LiveOverview";

/** Replies go out on their own only for these (excom_ai/constants.py SAFE_REPLY_INTENTS). */
const SAFE_INTENTS = ["ooo", "thanks", "scheduling", "public catalogue"];

/** The ceiling for the condition being typed: the server's, except a reply's, which depends on it. */
function ceilingFor(p: PolicyRow, condition: string, pack: string): Mode {
  if (p.action === "reply" && p.hard_ceiling === "Auto" && pack !== "custom") {
    const wanted = condition.split(",").map((s) => s.trim().toLowerCase()).filter(Boolean);
    return wanted.length > 0 && wanted.every((w) => SAFE_INTENTS.includes(w)) ? "Auto" : "Review";
  }
  return p.ceiling;
}

type Row = { mode: Mode; sample_percent: string; daily_cap: string; condition: string };
const rowOf = (p: PolicyRow): Row => ({ mode: p.mode, sample_percent: String(p.sample_percent ?? 0), daily_cap: String(p.daily_cap ?? 0), condition: p.condition || "" });

/** Autonomy: for each kind of action, how far the agent may go on its own, never past its ceiling. */
export function AutonomyTab({ d, reload }: { d: AgentDetail; reload: () => void }) {
  const edit = d.can_change && d.agent.status !== "Retired";
  const modes = d.modes?.length ? d.modes : (["Off", "Shadow", "Review", "Auto"] as Mode[]);
  const server = useMemo(() => Object.fromEntries(d.policies.map((p) => [p.action, rowOf(p)])) as Record<string, Row>, [d.policies]);
  const key = JSON.stringify(server);
  const [rows, setRows] = useState(server);
  useEffect(() => setRows(server), [key]); // eslint-disable-line react-hooks/exhaustive-deps
  const dirty = d.policies.filter((p) => JSON.stringify(rows[p.action]) !== JSON.stringify(server[p.action]));
  const over = d.policies.filter((p) => rows[p.action] && modes.indexOf(rows[p.action].mode) > modes.indexOf(ceilingFor(p, rows[p.action].condition, d.pack)));
  const save = useAgentAction(M.setPolicy);
  const [saving, setSaving] = useState(false);
  const { live, reload: reloadLive } = useLive(d.agent.name);
  const promotions = Object.fromEntries((live?.promotions ?? []).map((p) => [p.action, p]));
  const promote = useAgentAction(M.applyPromotion);
  const apply = async (action: string) => {
    const s = promotions[action];
    const label = POLICY_LABEL[action]?.label || action;
    if (await promote.run({ name: d.agent.name, action }, `${label}: now ${s?.to ?? "one step up"}`, `Could not move ${label} up`)) { reload(); reloadLive(); }
  };

  const saveAll = async () => {
    setSaving(true);
    let done = 0;
    for (const p of dirty) {
      const r = rows[p.action];
      const ok = await save.run({ name: d.agent.name, action: p.action, mode: r.mode, sample_percent: Number(r.sample_percent || 0), daily_cap: Number(r.daily_cap || 0), condition: r.condition }, "", `Could not save ${POLICY_LABEL[p.action]?.label || p.action}`);
      if (!ok) break;
      done++;
    }
    setSaving(false);
    if (done) { toast.success(done === 1 ? "Autonomy saved" : `${done} settings saved`); reload(); }
  };
  const set = (action: string, patch: Partial<Row>) => setRows((r) => ({ ...r, [action]: { ...r[action], ...patch } }));

  return (
    <div className="space-y-2 max-w-4xl">
      <Panel title="How far it may go on its own" actions={edit && dirty.length ? <>
        <Button size="sm" variant="ghost" onClick={() => setRows(server)}><X />Discard</Button>
        <Button size="sm" variant="primary" disabled={saving || over.length > 0} onClick={saveAll}><Save />{saving ? "Saving…" : `Save ${dirty.length === 1 ? "change" : `${dirty.length} changes`}`}</Button>
      </> : undefined}>
        <ul className="grid grid-cols-1 tablet:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-x-4 gap-y-1 text-xs text-ink-3">
          {modes.map((m) => <li key={m}><span className="text-ink-1 font-medium">{m}</span>: {MODE_HINT[m]}</li>)}
        </ul>
        <p className="text-xs text-ink-3 mt-2">Each action has a ceiling it can never go past, whatever is chosen here. Prices, terms and quotations always wait for a person.</p>
        {live && (
          <p className="text-sm text-ink-2 mt-2 flex items-center gap-1.5">
            <TrendingUp className="size-4 shrink-0 text-ink-3" aria-hidden />
            {live.promotions.length
              ? `${live.promotions.length} ${live.promotions.length === 1 ? "action has" : "actions have"} earned a step up: see the suggestion under ${live.promotions.length === 1 ? "it" : "each"}.`
              : "No suggestions to move up yet: an action earns one after 20 drafts in a row approved unchanged."}
          </p>
        )}
      </Panel>

      {d.policies.length === 0 && <p className="text-sm text-ink-3 p-3">No actions set up for this agent.</p>}
      <ul className="space-y-1.5">
        {d.policies.map((p) => {
          const r = rows[p.action] ?? rowOf(p);
          const ceiling = ceilingFor(p, r.condition, d.pack);
          const allowed = modes.slice(0, modes.indexOf(ceiling) + 1);
          const tooHigh = modes.indexOf(r.mode) > modes.indexOf(ceiling);
          const meta = POLICY_LABEL[p.action] ?? { label: p.action.replace(/_/g, " "), hint: "" };
          const id = `pol-${p.action}`;
          return (
            <li key={p.action} className={cn("rounded-lg border bg-surface p-3 min-w-0", tooHigh ? "border-crayon-rose-base" : "border-border")}>
              <div className="flex items-center gap-2 flex-wrap min-w-0">
                <span className="text-sm font-medium text-ink-1">{meta.label}</span>
                {meta.hint && <span className="text-xs text-ink-3 min-w-0">{meta.hint}</span>}
                <Chip size="sm" className="ml-auto" icon={<Lock />} label={`At most ${ceiling}`} title="The ceiling: it can never be set higher" />
              </div>
              {edit ? (
                <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] tablet:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,2fr)] gap-2 mt-2">
                  <Field label="Mode" htmlFor={`${id}-mode`}>
                    <Select id={`${id}-mode`} value={r.mode} onChange={(e) => set(p.action, { mode: e.target.value as Mode })} disabled={allowed.length <= 1 && !tooHigh}>
                      {allowed.map((m) => <option key={m} value={m}>{m}</option>)}
                      {tooHigh && <option value={r.mode} disabled>{r.mode} (above the ceiling)</option>}
                    </Select>
                  </Field>
                  <Field label="Checked after (%)" htmlFor={`${id}-sample`}>
                    <Input id={`${id}-sample`} type="number" min={0} max={100} inputMode="numeric" value={r.sample_percent} onChange={(e) => set(p.action, { sample_percent: e.target.value })} className="tabular-nums" />
                  </Field>
                  <Field label="Most per day" htmlFor={`${id}-cap`}>
                    <Input id={`${id}-cap`} type="number" min={0} inputMode="numeric" value={r.daily_cap} onChange={(e) => set(p.action, { daily_cap: e.target.value })} className="tabular-nums" placeholder="0 = no cap" />
                  </Field>
                  <Field label="Only when" htmlFor={`${id}-cond`} className="col-span-2 tablet:col-span-1">
                    <Input id={`${id}-cond`} value={r.condition} onChange={(e) => set(p.action, { condition: e.target.value })} placeholder={p.action === "reply" ? "ooo, thanks, scheduling" : "Any case"} />
                  </Field>
                </div>
              ) : (
                <p className="text-sm text-ink-2 mt-1">
                  <span className="text-ink-1 font-medium">{r.mode}</span>: {MODE_HINT[r.mode]}
                  {Number(r.sample_percent) > 0 && ` · ${r.sample_percent}% checked after`}
                  {Number(r.daily_cap) > 0 && ` · at most ${r.daily_cap} a day`}
                  {r.condition && ` · only when: ${r.condition}`}
                </p>
              )}
              {promotions[p.action] && (
                <div className="mt-2 rounded-md border border-crayon-green-base bg-crayon-green-tint px-2.5 py-2 flex items-center gap-2 flex-wrap min-w-0" role="note" aria-label={`Suggestion for ${meta.label}`}>
                  <TrendingUp className="size-4 shrink-0 text-crayon-green-text" aria-hidden />
                  <p className="text-sm text-ink-1 min-w-0 flex-1 break-words">
                    Suggested: move from <span className="font-medium">{promotions[p.action].from}</span> to <span className="font-medium">{promotions[p.action].to}</span>, because {promotions[p.action].why}.
                  </p>
                  {edit && (
                    <Button size="sm" variant="primary" disabled={promote.loading || dirty.some((x) => x.action === p.action)}
                      title={dirty.some((x) => x.action === p.action) ? "Save or discard your change to this row first" : undefined}
                      onClick={() => apply(p.action)}>{promote.loading ? "Applying…" : "Apply"}</Button>
                  )}
                </div>
              )}
              {tooHigh && <p className="text-xs text-crayon-rose-text mt-1.5">{r.mode} is above what this action allows here.{p.action === "reply" ? ` Replies go out on their own only when “Only when” names just: ${SAFE_INTENTS.join(", ")}.` : ""}</p>}
              {edit && p.action === "reply" && p.hard_ceiling === "Auto" && d.pack !== "custom" && !tooHigh && ceiling !== "Auto" && (
                <p className="text-xs text-ink-3 mt-1.5">To let simple replies go out on their own, set “Only when” to some of: {SAFE_INTENTS.join(", ")}.</p>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
