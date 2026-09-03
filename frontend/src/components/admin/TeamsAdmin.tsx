import { useMemo, useState } from "react";
import { useFrappeGetCall, useFrappePostCall } from "frappe-react-sdk";
import { Plus, Trash2, UserPlus, UserMinus, Shield, AlertTriangle, ArrowRightLeft, Save, Loader2, Search } from "lucide-react";
import { toast } from "sonner";
import { Button, Input, Textarea, Field, Sheet, Modal, EmptyState, Chip, Avatar, Select } from "../primitives";
import { cn } from "../ui/utils";
import { serverMessage } from "./util";

interface Member { team: string; user: string; role: "Manager" | "Member"; full_name?: string; user_image?: string; enabled: number }
interface Team { name: string; team_name: string; description?: string; parent_team?: string; members: Member[]; accounts: { name: string; account_name: string; channel: string }[]; open_threads: number }
interface Account { name: string; account_name: string; channel: string; status?: string }

export function useTeamsOverview() {
  return useFrappeGetCall<{ message: Team[] }>("excom.excom.api.admin.get_teams_overview", undefined, "admin-teams", { revalidateOnFocus: false });
}

/** Teams: create, rename, parent, members + roles, channel-account access, disabled members, delete. */
export function TeamsAdmin() {
  const { data, isLoading, mutate } = useTeamsOverview();
  const { data: accRaw } = useFrappeGetCall<{ message: Account[] }>("excom.excom.api.chat.get_channel_accounts", undefined, "admin-accounts", { revalidateOnFocus: false });
  const teams = data?.message ?? [];
  const accounts = accRaw?.message ?? [];
  const [sel, setSel] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const team = teams.find((t) => t.name === sel) || null;
  if (isLoading && !data) return <div className="flex justify-center py-8 text-ink-3"><Loader2 className="size-5 animate-spin" /></div>;
  return (
    <div className="flex h-full min-h-0">
      <div className={cn("flex flex-col min-h-0 border-r border-border w-full laptop:w-[320px] shrink-0", sel && "hidden laptop:flex")}>
        <div className="shrink-0 flex items-center gap-2 px-3 py-2 border-b border-border">
          <span className="text-xs text-ink-3 tabular-nums flex-1">{teams.length} teams</span>
          <Button size="sm" variant="primary" onClick={() => setCreating(true)}><Plus />New team</Button>
        </div>
        <ul className="flex-1 min-h-0 overflow-y-auto divide-y divide-border">
          {teams.length === 0 && <EmptyState title="No teams yet" hint="Teams decide who sees which chats and channel accounts." compact />}
          {teams.map((t) => {
            const disabled = t.members.filter((m) => !m.enabled).length;
            return (
              <li key={t.name}>
                <button type="button" onClick={() => setSel(t.name)} className={cn("w-full text-left px-3 py-2 hover:bg-surface-hover min-w-0", sel === t.name && "bg-surface-active")}>
                  <div className="flex items-center gap-2 min-w-0"><Shield className="size-4 text-ink-3 shrink-0" /><span className="text-sm font-medium text-ink-1 truncate flex-1">{t.team_name}</span>{disabled > 0 && <Chip size="sm" accent="amber" label={`${disabled} disabled`} />}</div>
                  <div className="text-xs text-ink-3 truncate mt-0.5">{t.members.length} members · {t.open_threads} open chats · {t.accounts.length} accounts{t.parent_team ? ` · under ${t.parent_team}` : ""}</div>
                </button>
              </li>
            );
          })}
        </ul>
      </div>
      <div className={cn("flex-1 min-w-0 min-h-0 flex flex-col", !sel && "hidden laptop:flex")}>
        {team ? <TeamDetail key={team.name} team={team} teams={teams} accounts={accounts} onBack={() => setSel(null)} onChanged={(n) => { mutate(); if (n !== undefined) setSel(n); }} />
          : <EmptyState icon={<Shield />} title="Pick a team" hint="Members, roles and channel-account access live here." />}
      </div>
      <CreateTeam open={creating} onClose={() => setCreating(false)} onCreated={(n) => { mutate(); setSel(n); }} />
    </div>
  );
}

function CreateTeam({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (n: string) => void }) {
  const [name, setName] = useState(""); const [desc, setDesc] = useState("");
  const { call, loading } = useFrappePostCall("excom.excom.api.teams.create_team");
  return (
    <Modal open={open} onOpenChange={(o) => !o && onClose()} title="New team" footer={<><Button variant="default" onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!name.trim() || loading} onClick={async () => { try { const r = await call({ team_name: name.trim(), description: desc }); toast.success("Team created"); setName(""); setDesc(""); onClose(); onCreated(r.message.name); } catch (e) { toast.error(serverMessage(e)); } }}>Create</Button></>}>
      <div className="p-3 space-y-3">
        <Field label="Team name" required><Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Sales, Compliance, GST, IT…" autoFocus /></Field>
        <Field label="Description"><Textarea rows={2} value={desc} onChange={(e) => setDesc(e.target.value)} /></Field>
      </div>
    </Modal>
  );
}

function TeamDetail({ team, teams, accounts, onBack, onChanged }: { team: Team; teams: Team[]; accounts: Account[]; onBack: () => void; onChanged: (newName?: string) => void }) {
  const [name, setName] = useState(team.team_name);
  const [desc, setDesc] = useState(team.description || "");
  const [parent, setParent] = useState(team.parent_team || "");
  const [addOpen, setAddOpen] = useState(false);
  const [reassign, setReassign] = useState<Member | null>(null);
  const [delOpen, setDelOpen] = useState(false);
  const { call: update, loading: saving } = useFrappePostCall("excom.excom.api.admin.update_team");
  const { call: setRole } = useFrappePostCall("excom.excom.api.admin.set_member_role");
  const { call: remove } = useFrappePostCall("excom.excom.api.teams.remove_team_member");
  const { call: setAccounts } = useFrappePostCall("excom.excom.api.admin.set_team_accounts");
  const dirty = name !== team.team_name || desc !== (team.description || "") || parent !== (team.parent_team || "");
  const has = new Set(team.accounts.map((a) => a.name));
  const wrap = async (fn: () => Promise<unknown>, ok: string) => { try { await fn(); toast.success(ok); onChanged(); } catch (e) { toast.error(serverMessage(e)); } };
  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="shrink-0 flex items-center gap-2 px-3 py-2 border-b border-border bg-surface-sunken min-w-0">
        <Button variant="ghost" size="sm" className="laptop:hidden" onClick={onBack}>Back</Button>
        <span className="text-sm text-ink-1 font-medium truncate flex-1">{team.team_name}</span>
        <Button size="sm" variant="ghost" className="text-crayon-rose-text" onClick={() => setDelOpen(true)}><Trash2 />Delete</Button>
        <Button size="sm" variant="primary" disabled={!dirty || saving} onClick={() => wrap(async () => { const r = await update({ team: team.name, team_name: name.trim(), description: desc, parent_team: parent }); onChanged(r.message.name); }, "Team saved")}><Save />Save</Button>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto p-3 space-y-5">
        <section className="grid grid-cols-1 laptop:grid-cols-3 gap-3">
          <Field label="Team name" required><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
          <Field label="Parent team" hint="Managers of the parent see this team's chats too."><Select value={parent} onChange={(e) => setParent(e.target.value)}><option value="">— none —</option>{teams.filter((t) => t.name !== team.name).map((t) => <option key={t.name} value={t.name}>{t.team_name}</option>)}</Select></Field>
          <Field label="Description"><Input value={desc} onChange={(e) => setDesc(e.target.value)} /></Field>
        </section>

        <section>
          <div className="flex items-center gap-2 mb-2"><h4 className="text-xs font-medium text-ink-3 uppercase tracking-wide flex-1">Members · {team.members.length}</h4><Button size="sm" variant="default" onClick={() => setAddOpen(true)}><UserPlus />Add member</Button></div>
          {team.members.length === 0 ? <p className="text-xs text-ink-3">Nobody yet. Members see this team's chats; Managers can also transfer and assign.</p> : (
            <ul className="rounded-md border border-border divide-y divide-border">
              {team.members.map((m) => (
                <li key={m.user} className="flex items-center gap-2 px-2 h-11 min-w-0">
                  <Avatar name={m.full_name || m.user} src={m.user_image} size={24} />
                  <div className="flex-1 min-w-0"><div className="text-sm text-ink-1 truncate">{m.full_name || m.user}</div><div className="text-xs text-ink-3 truncate">{m.user}</div></div>
                  {!m.enabled && <button type="button" onClick={() => setReassign(m)} className="inline-flex items-center gap-1 text-xs text-crayon-amber-text hover:underline" title="This user is disabled — move their chats"><AlertTriangle className="size-3.5" />Disabled · reassign chats</button>}
                  <Select value={m.role} onChange={(e) => wrap(() => setRole({ team: team.name, user: m.user, role: e.target.value }), `${m.full_name || m.user} is now ${e.target.value}`)} className="w-28 h-8 text-xs"><option>Member</option><option>Manager</option></Select>
                  <Button size="icon" variant="ghost" aria-label="Remove from team" onClick={() => wrap(() => remove({ team: team.name, user: m.user }), "Removed")}><UserMinus /></Button>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section>
          <h4 className="text-xs font-medium text-ink-3 uppercase tracking-wide mb-2">Channel accounts this team can use</h4>
          {accounts.length === 0 ? <p className="text-xs text-ink-3">No channel accounts configured.</p> : (
            <div className="grid grid-cols-1 tablet:grid-cols-2 gap-1.5">
              {accounts.map((a) => (
                <label key={a.name} className="flex items-center gap-2 rounded-md border border-border px-2 h-10 text-sm cursor-pointer hover:bg-surface-hover">
                  <input type="checkbox" className="size-4" checked={has.has(a.name)} onChange={(e) => { const next = new Set(has); e.target.checked ? next.add(a.name) : next.delete(a.name); wrap(() => setAccounts({ team: team.name, accounts: JSON.stringify([...next]) }), "Access updated"); }} />
                  <span className="truncate flex-1">{a.account_name || a.name}</span><Chip size="sm" accent="neutral" label={a.channel} />
                </label>
              ))}
            </div>
          )}
          <p className="text-xs text-ink-3 mt-1.5">An account with no teams ticked anywhere is visible to everyone.</p>
        </section>
      </div>
      <AddMember open={addOpen} team={team.name} onClose={() => setAddOpen(false)} onAdded={() => onChanged()} />
      {reassign && <ReassignDialog user={reassign.user} label={reassign.full_name || reassign.user} onClose={() => setReassign(null)} onDone={() => { setReassign(null); onChanged(); }} />}
      <Modal open={delOpen} onOpenChange={(o) => !o && setDelOpen(false)} title={`Delete ${team.team_name}?`} footer={<><Button variant="default" onClick={() => setDelOpen(false)}>Cancel</Button><DeleteTeamButton team={team} teams={teams} onDone={() => { setDelOpen(false); onChanged(null as unknown as string); }} /></>}>
        <p className="p-3 text-sm text-ink-2">{team.open_threads} open chats are on this team. Choose where they go below, then confirm.</p>
      </Modal>
    </div>
  );
}

function DeleteTeamButton({ team, teams, onDone }: { team: Team; teams: Team[]; onDone: () => void }) {
  const [to, setTo] = useState("");
  const { call, loading } = useFrappePostCall("excom.excom.api.admin.delete_team");
  return (
    <div className="flex items-center gap-2">
      <Select value={to} onChange={(e) => setTo(e.target.value)} className="h-8 text-xs w-44"><option value="">Chats → no team</option>{teams.filter((t) => t.name !== team.name).map((t) => <option key={t.name} value={t.name}>Chats → {t.team_name}</option>)}</Select>
      <Button variant="danger" disabled={loading} onClick={async () => { try { await call({ team: team.name, move_threads_to: to }); toast.success("Team deleted"); onDone(); } catch (e) { toast.error(serverMessage(e)); } }}>Delete team</Button>
    </div>
  );
}

function AddMember({ open, team, onClose, onAdded }: { open: boolean; team: string; onClose: () => void; onAdded: () => void }) {
  const [q, setQ] = useState(""); const [role, setRole] = useState("Member");
  const { data } = useFrappeGetCall<{ message: { name: string; full_name: string; user_image?: string }[] }>(open ? "excom.excom.api.teams.search_users" : (null as unknown as string), { search: q, limit: 20 }, undefined, { keepPreviousData: true });
  const { call, loading } = useFrappePostCall("excom.excom.api.teams.add_team_member");
  return (
    <Sheet open={open} onOpenChange={(o) => !o && onClose()} title="Add member" width="w-[400px]">
      <div className="p-3 space-y-3">
        <div className="flex gap-2"><div className="relative flex-1"><Search className="absolute left-2 top-1/2 -translate-y-1/2 size-4 text-ink-3" /><Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search users…" className="pl-8" autoFocus /></div><Select value={role} onChange={(e) => setRole(e.target.value)} className="w-28"><option>Member</option><option>Manager</option></Select></div>
        <ul className="rounded-md border border-border divide-y divide-border">
          {(data?.message ?? []).map((u) => (
            <li key={u.name}><button type="button" disabled={loading} onClick={async () => { try { const r = await call({ team, user: u.name, role }); if (r.message?.success === false) toast.error(r.message.message); else { toast.success(`${u.full_name} added`); onAdded(); } } catch (e) { toast.error(serverMessage(e)); } }} className="w-full flex items-center gap-2 px-2 h-10 text-left hover:bg-surface-hover"><Avatar name={u.full_name} src={u.user_image} size={20} /><span className="text-sm text-ink-1 truncate flex-1">{u.full_name}</span><span className="text-xs text-ink-3 truncate max-w-[45%]">{u.name}</span></button></li>
          ))}
          {(data?.message ?? []).length === 0 && <li className="px-2 py-3 text-xs text-ink-3">No enabled users match.</li>}
        </ul>
      </div>
    </Sheet>
  );
}

/** Move all open chats (and leads) from one user to another or to nobody. Shared with the Users page. */
export function ReassignDialog({ user, label, onClose, onDone }: { user: string; label: string; onClose: () => void; onDone: () => void }) {
  const [q, setQ] = useState(""); const [to, setTo] = useState(""); const [leads, setLeads] = useState(true);
  const { data } = useFrappeGetCall<{ message: { name: string; full_name: string }[] }>("excom.excom.api.teams.search_users", { search: q, limit: 15 }, undefined, { keepPreviousData: true });
  const { call, loading } = useFrappePostCall("excom.excom.api.admin.reassign_user_work");
  const options = useMemo(() => (data?.message ?? []).filter((u) => u.name !== user), [data, user]);
  return (
    <Modal open onOpenChange={(o) => !o && onClose()} title={`Reassign ${label}'s work`} footer={<><Button variant="default" onClick={onClose}>Cancel</Button><Button variant="primary" disabled={loading} onClick={async () => { try { const r = await call({ from_user: user, to_user: to, include_leads: leads ? 1 : 0 }); toast.success(`${r.message.threads} chats${leads ? `, ${r.message.leads} leads` : ""} moved`); onDone(); } catch (e) { toast.error(serverMessage(e)); } }}><ArrowRightLeft />{to ? "Move" : "Unassign"}</Button></>}>
      <div className="p-3 space-y-3">
        <Field label="Move to" hint="Leave empty to make them unassigned (anyone who replies will claim them)."><div className="relative"><Search className="absolute left-2 top-1/2 -translate-y-1/2 size-4 text-ink-3" /><Input value={q} onChange={(e) => { setQ(e.target.value); setTo(""); }} placeholder="Search a user, or leave empty" className="pl-8" /></div></Field>
        {q && <ul className="rounded-md border border-border divide-y divide-border max-h-44 overflow-y-auto">{options.map((u) => <li key={u.name}><button type="button" onClick={() => { setTo(u.name); setQ(u.full_name); }} className={cn("w-full text-left px-2 h-9 text-sm hover:bg-surface-hover", to === u.name && "bg-surface-active")}>{u.full_name} <span className="text-xs text-ink-3">{u.name}</span></button></li>)}</ul>}
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" className="size-4" checked={leads} onChange={(e) => setLeads(e.target.checked)} />Also move their open Leads (lead owner + ToDo)</label>
      </div>
    </Modal>
  );
}
