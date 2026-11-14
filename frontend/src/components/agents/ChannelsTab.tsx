import { useEffect, useMemo, useState } from "react";
import { ArrowRightLeft, Plus, Save, Trash2, X } from "lucide-react";
import { toast } from "sonner";
import { Button, Chip, Field, Input, Modal, Select } from "../primitives";
import { LinkField } from "../crm/LinkField";
import { cn } from "../ui/utils";
import type { AgentDetail, ChannelRow } from "../../types/agents";
import { M, Panel, cost, useAgentAction } from "./bits";

const PURPOSES = ["Outreach and replies", "Outreach", "Replies"];
const SIGNATURES = ["Server adds it", "Agent adds it"];
const OFFICE_HOURS = [
  { value: "Recipient's office hours", label: "The recipient's office hours", hint: "Messages go out in the working day of the person written to." },
  { value: "Company office hours", label: "Our office hours", hint: "Messages go out in your company's working day." },
];
const USER_FILTERS = { enabled: 1, user_type: "System User" };
const PEOPLE_FINDER_HINT = "Cold emails go only to a person's own address found by Apollo or Prospeo: never info@, sales@ or another shared inbox, never a guessed or website address. Replies to people who wrote in are not affected.";

type People = { owner_user: string; backup_user: string; team: string; escalation_user: string; reviewers: string };
type Limits = { daily_ai_budget: string; monthly_ai_budget: string; office_hours: string; people_finder_only: boolean; channels: ChannelRow[] };

function pickChannel(r: ChannelRow): ChannelRow {
  return { channel_account: r.channel_account || "", purpose: r.purpose || PURPOSES[0], daily_cap: r.daily_cap ?? 0, warmup_start: r.warmup_start || "", signature_mode: r.signature_mode || SIGNATURES[0], paused: r.paused ? 1 : 0 };
}

/** Channels and limits: who looks after the agent, what it may spend, and where it may write. */
export function ChannelsTab({ d, reload }: { d: AgentDetail; reload: () => void }) {
  const a = d.agent;
  const edit = d.can_change && a.status !== "Retired";
  const editPeople = Boolean(d.can_change_people ?? d.can_change) && a.status !== "Retired";

  /* people */
  const serverPeople: People = useMemo(() => ({ owner_user: a.owner_user || "", backup_user: a.backup_user || "", team: a.team || "", escalation_user: a.escalation_user || "", reviewers: a.reviewers || "" }), [a]);
  const [people, setPeople] = useState(serverPeople);
  const peopleKey = JSON.stringify(serverPeople);
  useEffect(() => setPeople(serverPeople), [peopleKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const peopleDirty = JSON.stringify(people) !== peopleKey;
  const savePeople = useAgentAction(M.setPeople);

  /* limits and channels */
  const serverLimits: Limits = useMemo(() => ({
    daily_ai_budget: a.daily_ai_budget != null ? String(a.daily_ai_budget) : "",
    monthly_ai_budget: a.monthly_ai_budget != null ? String(a.monthly_ai_budget) : "",
    office_hours: a.office_hours || OFFICE_HOURS[0].value,
    people_finder_only: Boolean(a.people_finder_only),
    channels: d.channels.map(pickChannel),
  }), [a, d.channels]);
  const [limits, setLimits] = useState(serverLimits);
  const limitsKey = JSON.stringify(serverLimits);
  useEffect(() => setLimits(serverLimits), [limitsKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const limitsDirty = JSON.stringify(limits) !== limitsKey;
  const saveLimits = useAgentAction(M.setLimits);
  const setRow = (i: number, patch: Partial<ChannelRow>) => setLimits((l) => ({ ...l, channels: l.channels.map((r, j) => (j === i ? { ...r, ...patch } : r)) }));
  const missingAccount = limits.channels.some((r) => !r.channel_account);

  const pp = (k: keyof People, label: string, doctype: string, hint?: string) => (
    <Field label={label} hint={hint}>
      {editPeople ? <LinkField doctype={doctype} value={people[k]} onChange={(v) => setPeople((p) => ({ ...p, [k]: v }))} placeholder={doctype === "User" ? "Choose a person" : "Choose a team"} filters={doctype === "User" ? USER_FILTERS : undefined} />
        : <p className="text-sm text-ink-1 min-h-8 flex items-center">{people[k] || "—"}</p>}
    </Field>
  );

  return (
    <div className="space-y-2 max-w-4xl">
      <Panel title="People" actions={editPeople && peopleDirty ? <>
        <Button size="sm" variant="ghost" onClick={() => setPeople(serverPeople)}><X />Discard</Button>
        <Button size="sm" variant="primary" disabled={savePeople.loading || !people.owner_user} onClick={async () => {
          const { owner_user, ...rest } = people;
          if (await savePeople.run({ name: a.name, ...(owner_user ? { owner_user } : {}), ...rest }, "People saved", "Could not save the people")) reload();
        }}><Save />{savePeople.loading ? "Saving…" : "Save people"}</Button></> : edit ? <HandoverButton d={d} reload={reload} /> : undefined}>
        <div className="grid grid-cols-1 tablet:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-3">
          {pp("owner_user", "Owner", "User", editPeople ? "Only the team's manager or an Excom Admin changes the owner. Everyone named here must be in the agent's team." : undefined)}
          {pp("backup_user", "Backup", "User", "Takes over when the owner is away.")}
          {pp("team", "Team", "Excom Team", "Its numbers and mailboxes are the ones the agent may use.")}
          {pp("escalation_user", "Escalate to", "User", "Who hears when something goes wrong and nobody answers.")}
          <Field label="Also reviews" className="tablet:col-span-2" htmlFor="reviewers" hint="Other people who may approve its messages: their login emails, separated by commas.">
            {editPeople ? <Input id="reviewers" value={people.reviewers} onChange={(e) => setPeople((p) => ({ ...p, reviewers: e.target.value }))} placeholder="name@company.com, other@company.com" />
              : <p className="text-sm text-ink-1 break-words">{people.reviewers || "—"}</p>}
          </Field>
        </div>
      </Panel>

      <Panel title="Limits" actions={edit && limitsDirty ? <>
        <Button size="sm" variant="ghost" onClick={() => setLimits(serverLimits)}><X />Discard</Button>
        <Button size="sm" variant="primary" disabled={saveLimits.loading || missingAccount} title={missingAccount ? "Choose an account for every channel" : undefined} onClick={async () => {
          const ok = await saveLimits.run({
            name: a.name,
            daily_ai_budget: limits.daily_ai_budget === "" ? 0 : Number(limits.daily_ai_budget),
            monthly_ai_budget: limits.monthly_ai_budget === "" ? 0 : Number(limits.monthly_ai_budget),
            office_hours: limits.office_hours,
            people_finder_only: limits.people_finder_only ? 1 : 0,
            channels: JSON.stringify(limits.channels.map((r) => ({ ...r, daily_cap: Number(r.daily_cap || 0), warmup_start: r.warmup_start || null }))),
          }, "Limits saved", "Could not save the limits");
          if (ok) reload();
        }}><Save />{saveLimits.loading ? "Saving…" : "Save limits"}</Button></> : undefined}>
        <div className="grid grid-cols-1 tablet:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-3">
          <Field label="AI budget per day" htmlFor="daily" hint="Empty keeps the current limit. Only an Excom Admin can set 0 (no limit of its own).">
            {edit ? <Input id="daily" type="number" min={0} step="0.01" inputMode="decimal" value={limits.daily_ai_budget} onChange={(e) => setLimits((l) => ({ ...l, daily_ai_budget: e.target.value }))} className="tabular-nums" />
              : <p className="text-sm text-ink-1 tabular-nums">{a.daily_ai_budget ? cost(a.daily_ai_budget) : "No limit"}</p>}
          </Field>
          <Field label="AI budget per month" htmlFor="monthly" hint="Empty keeps the current limit. Only an Excom Admin can set 0 (no limit of its own).">
            {edit ? <Input id="monthly" type="number" min={0} step="0.01" inputMode="decimal" value={limits.monthly_ai_budget} onChange={(e) => setLimits((l) => ({ ...l, monthly_ai_budget: e.target.value }))} className="tabular-nums" />
              : <p className="text-sm text-ink-1 tabular-nums">{a.monthly_ai_budget ? cost(a.monthly_ai_budget) : "No limit"}</p>}
          </Field>
          <Field label="When it sends" htmlFor="hours" className="tablet:col-span-2" hint={OFFICE_HOURS.find((o) => o.value === limits.office_hours)?.hint}>
            {edit ? <Select id="hours" value={limits.office_hours} onChange={(e) => setLimits((l) => ({ ...l, office_hours: e.target.value }))}>{OFFICE_HOURS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}</Select>
              : <p className="text-sm text-ink-1">{OFFICE_HOURS.find((o) => o.value === limits.office_hours)?.label || limits.office_hours}</p>}
          </Field>
          <div className="tablet:col-span-2">
            <label className={cn("inline-flex items-start gap-2 text-sm text-ink-1", edit && "cursor-pointer")}>
              <input type="checkbox" className="size-4 mt-0.5 shrink-0" checked={limits.people_finder_only} disabled={!edit}
                onChange={(e) => setLimits((l) => ({ ...l, people_finder_only: e.target.checked }))} />
              <span>Email only named people from the People Finder</span>
            </label>
            <p className="text-xs text-ink-3 mt-1 ml-6">{PEOPLE_FINDER_HINT}</p>
          </div>
        </div>

        <h3 className="text-sm font-medium text-ink-1 mt-4 mb-1">Channels it may use</h3>
        <p className="text-xs text-ink-3 mb-2">A new mailbox or number starts slowly while it warms up; the daily cap is the most it sends in a day from that account.</p>
        {limits.channels.length === 0 && <p className="text-sm text-ink-3 mb-2">No channels yet: it cannot write to anyone.</p>}
        <ul className="space-y-2">
          {limits.channels.map((r, i) => {
            const server = d.channels.find((c) => c.channel_account === r.channel_account);
            return (
              <li key={i} className={cn("rounded-md border border-border p-2.5 min-w-0", r.paused && "bg-surface-sunken")}>
                {edit ? (
                  <div className="grid grid-cols-1 tablet:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)] gap-2 items-end">
                    <Field label="Account" required><LinkField doctype="Excom Channel Account" value={r.channel_account} onChange={(v) => setRow(i, { channel_account: v })} placeholder="Choose a mailbox or number" /></Field>
                    <Field label="Used for" htmlFor={`purpose-${i}`}><Select id={`purpose-${i}`} value={r.purpose || PURPOSES[0]} onChange={(e) => setRow(i, { purpose: e.target.value })}>{PURPOSES.map((p) => <option key={p}>{p}</option>)}</Select></Field>
                    <Field label="Daily cap" htmlFor={`cap-${i}`}><Input id={`cap-${i}`} type="number" min={0} inputMode="numeric" value={r.daily_cap ?? 0} onChange={(e) => setRow(i, { daily_cap: e.target.value === "" ? 0 : Number(e.target.value) })} className="tabular-nums" /></Field>
                    <Field label="Warm-up started" htmlFor={`warm-${i}`}><Input id={`warm-${i}`} type="date" value={r.warmup_start || ""} onChange={(e) => setRow(i, { warmup_start: e.target.value })} /></Field>
                    <Field label="Signature" htmlFor={`sig-${i}`}><Select id={`sig-${i}`} value={r.signature_mode || SIGNATURES[0]} onChange={(e) => setRow(i, { signature_mode: e.target.value })}>{SIGNATURES.map((s) => <option key={s}>{s}</option>)}</Select></Field>
                    <div className="flex items-center gap-2 h-8">
                      <label className="inline-flex items-center gap-1.5 text-sm text-ink-1 cursor-pointer"><input type="checkbox" className="size-4" checked={Boolean(r.paused)} onChange={(e) => setRow(i, { paused: e.target.checked ? 1 : 0 })} />Paused</label>
                      <Button size="icon-sm" variant="ghost" className="ml-auto" aria-label={`Remove ${r.channel_account || "this channel"}`} onClick={() => setLimits((l) => ({ ...l, channels: l.channels.filter((_, j) => j !== i) }))}><Trash2 /></Button>
                    </div>
                  </div>
                ) : (
                  <div className="flex items-center gap-2 flex-wrap min-w-0 text-sm">
                    <span className="text-ink-1 truncate min-w-0">{r.channel_account}</span>
                    {server?.channel && <Chip size="sm" label={server.channel} />}
                    <span className="text-ink-3">{r.purpose}</span>
                    <span className="text-ink-3 tabular-nums">{r.daily_cap ? `${r.daily_cap} a day` : "no cap"}</span>
                    {r.warmup_start && <span className="text-ink-3">warm-up from {r.warmup_start}</span>}
                    {r.paused ? <Chip size="sm" accent="amber" label="Paused" /> : null}
                  </div>
                )}
                {server?.pause_reason && r.paused ? <p className="text-xs text-crayon-amber-text mt-1 break-words">Paused: {server.pause_reason}</p> : null}
              </li>
            );
          })}
        </ul>
        {edit && <Button size="sm" variant="ghost" className="mt-2" onClick={() => setLimits((l) => ({ ...l, channels: [...l.channels, pickChannel({ channel_account: "" })] }))}><Plus />Add a channel</Button>}
      </Panel>
    </div>
  );
}

/**
 * The owner is leaving: the agent, its open conversations and its leads go to someone else.
 * Only an Excom Admin or the team's manager may; the server says so to anyone else.
 */
export function HandoverButton({ d, reload }: { d: AgentDetail; reload: () => void }) {
  const a = d.agent;
  const [open, setOpen] = useState(false);
  const [owner, setOwner] = useState("");
  const [backup, setBackup] = useState("");
  const handover = useAgentAction(M.handover);
  useEffect(() => { if (open) { setOwner(""); setBackup(""); } }, [open]);
  const same = owner === a.owner_user;
  const submit = async () => {
    if (!owner || same) return;
    const r = await handover.run({ name: a.name, new_owner: owner, ...(backup ? { new_backup: backup } : {}) }, "", "Could not hand it over");
    if (!r) return;
    const out = r as { threads?: number; leads?: number };
    const n = (v: number | undefined, one: string, many: string) => `${v ?? 0} ${(v ?? 0) === 1 ? one : many}`;
    toast.success(`Handed over to ${owner}: ${n(out.threads, "conversation", "conversations")} and ${n(out.leads, "lead", "leads")} moved.`);
    setOpen(false);
    reload();
  };
  return (
    <>
      <Button size="sm" onClick={() => setOpen(true)} title="Only an Excom Admin or the team's manager can hand an agent over"><ArrowRightLeft />Hand over</Button>
      <Modal open={open} onOpenChange={setOpen} title={`Hand over ${a.agent_name}`}
        footer={<><Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button><Button variant="primary" disabled={!owner || same || handover.loading} onClick={submit}>{handover.loading ? "Handing over…" : "Hand over"}</Button></>}>
        <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); submit(); }}>
          <p className="text-sm text-ink-2">For when {a.owner_user} is leaving or moving on. The new owner takes the agent, and the open conversations and leads that are {a.owner_user}'s move with it. Only an Excom Admin or the team's manager can do this.</p>
          <Field label="New owner" required hint={same ? "That is the owner already." : undefined}>
            <LinkField doctype="User" value={owner} onChange={setOwner} placeholder="Choose a person" filters={USER_FILTERS} />
          </Field>
          <Field label="New backup" hint={`Optional. Leave empty to keep ${a.backup_user || "no backup"}.`}>
            <LinkField doctype="User" value={backup} onChange={setBackup} placeholder="Choose a person" filters={USER_FILTERS} />
          </Field>
        </form>
      </Modal>
    </>
  );
}
