import { useState, useEffect, useCallback } from "react";
import { useFrappePostCall } from "frappe-react-sdk";
import { Plus, Search, Users, Shield, UserPlus, Trash2, Crown, Lock } from "lucide-react";
import { toast } from "sonner";
import { Button, Input, Field, Select, Modal, EmptyState, Avatar, Chip, Badge } from "./primitives";
import { AdminPage, DataTable } from "./shell/AdminPage";

interface Team { name: string; team_name: string; description: string; member_count: number }
interface TeamMember { user: string; role: string; full_name: string; user_image: string }
interface UserOption { name: string; full_name: string; user_image: string }

function CreateTeamDialog({ open, onOpenChange, onCreate }: { open: boolean; onOpenChange: (v: boolean) => void; onCreate: (name: string, desc: string) => void }) {
  const [name, setName] = useState(""); const [desc, setDesc] = useState("");
  return (
    <Modal open={open} onOpenChange={onOpenChange} title="Create team" footer={<><Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button><Button variant="primary" disabled={!name.trim()} onClick={() => onCreate(name.trim(), desc.trim())}>Create</Button></>}>
      <div className="space-y-3">
        <Field label="Team name" required><Input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Sales" autoFocus /></Field>
        <Field label="Description"><Input value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="Optional" /></Field>
      </div>
    </Modal>
  );
}

function AddMemberDialog({ open, onOpenChange, teamName, onAdd }: { open: boolean; onOpenChange: (v: boolean) => void; teamName: string; onAdd: (user: string, role: string) => void }) {
  const [search, setSearch] = useState(""); const [role, setRole] = useState("Member");
  const [selected, setSelected] = useState<UserOption | null>(null); const [users, setUsers] = useState<UserOption[]>([]);
  const { call: searchUsers } = useFrappePostCall("excom.excom.api.teams.search_users");
  useEffect(() => {
    if (!open) return;
    const t = setTimeout(async () => { try { const res = await searchUsers({ search, limit: 15 }); setUsers((res as any)?.message || []); } catch { setUsers([]); } }, 200);
    return () => clearTimeout(t);
  }, [search, open, searchUsers]);
  return (
    <Modal open={open} onOpenChange={onOpenChange} title={`Add member to ${teamName}`} footer={<><Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button><Button variant="primary" disabled={!selected} onClick={() => selected && onAdd(selected.name, role)}>Add</Button></>}>
      <div className="space-y-3">
        <Field label="User" required>
          {selected ? (
            <div className="flex items-center gap-2 rounded-md border border-border px-2 h-9 min-w-0"><Avatar name={selected.full_name || selected.name} src={selected.user_image} size={24} /><span className="text-sm text-ink-1 truncate flex-1">{selected.full_name || selected.name}</span><span className="text-xs text-ink-3 truncate">{selected.name}</span><Button variant="ghost" size="icon-sm" aria-label="Change" onClick={() => setSelected(null)}><Search /></Button></div>
          ) : (
            <>
              <div className="relative"><Search className="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-ink-muted" /><Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search by name or email" className="pl-8" autoFocus /></div>
              <div className="mt-1 max-h-52 overflow-y-auto rounded-md border border-border divide-y divide-border">
                {users.length === 0 ? <p className="px-3 py-2 text-xs text-ink-3 text-center">{search ? "No users found" : "Type to search"}</p> : users.map((u) => (
                  <button key={u.name} type="button" onClick={() => setSelected(u)} className="w-full flex items-center gap-2 px-2 h-10 hover:bg-surface-hover text-left min-w-0"><Avatar name={u.full_name || u.name} src={u.user_image} size={24} /><span className="min-w-0"><span className="block text-sm text-ink-1 truncate">{u.full_name || u.name}</span><span className="block text-xs text-ink-3 truncate">{u.name}</span></span></button>
                ))}
              </div>
            </>
          )}
        </Field>
        <Field label="Role"><Select value={role} onChange={(e) => setRole(e.target.value)}><option value="Member">Member</option><option value="Manager">Manager</option></Select></Field>
      </div>
    </Modal>
  );
}

function TeamDetailView({ team, onBack, embedded }: { team: Team; onBack: () => void; embedded?: boolean }) {
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [showAdd, setShowAdd] = useState(false);
  const { call: fetchMembers } = useFrappePostCall("excom.excom.api.teams.get_team_members");
  const { call: addMember } = useFrappePostCall("excom.excom.api.teams.add_team_member");
  const { call: removeMember } = useFrappePostCall("excom.excom.api.teams.remove_team_member");
  const load = useCallback(async () => { try { const res = await fetchMembers({ team: team.name }); setMembers((res as any)?.message || []); } catch { toast.error("Failed to load members"); } }, [fetchMembers, team.name]);
  useEffect(() => { load(); }, [load]);

  return (
    <AdminPage title={team.team_name} icon={<Shield />} onBack={onBack} embedded={embedded} bleed actions={<><Badge count={members.length} accent="neutral" /><Button variant="primary" size="sm" onClick={() => setShowAdd(true)}><UserPlus />Add</Button></>}>
      {team.description && <p className="text-xs text-ink-3 px-3 pt-2">{team.description}</p>}
      <DataTable
        rows={members}
        keyOf={(m) => m.user}
        empty={<EmptyState icon={<Users />} title="No members yet" compact />}
        columns={[
          { key: "user", label: "User", primary: true, render: (m) => <span className="inline-flex items-center gap-2 min-w-0"><Avatar name={m.full_name || m.user} src={m.user_image} size={24} /><span className="min-w-0"><span className="block text-sm text-ink-1 truncate">{m.full_name || m.user}</span><span className="block text-xs text-ink-3 truncate">{m.user}</span></span></span> },
          { key: "role", label: "Role", render: (m) => <Chip size="sm" accent={m.role === "Manager" ? "amber" : "neutral"} icon={m.role === "Manager" ? <Crown /> : undefined} label={m.role} /> },
          { key: "actions", label: "", align: "right", render: (m) => <Button variant="ghost" size="icon-sm" aria-label="Remove" title="Remove" onClick={async (e) => { e.stopPropagation(); await removeMember({ team: team.name, user: m.user }); toast.success("Member removed"); load(); }}><Trash2 /></Button> },
        ]}
      />
      <AddMemberDialog open={showAdd} onOpenChange={setShowAdd} teamName={team.team_name} onAdd={async (user, role) => { try { await addMember({ team: team.name, user, role }); toast.success("Member added"); setShowAdd(false); load(); } catch { toast.error("Failed to add member"); } }} />
    </AdminPage>
  );
}

/** Teams — avatar menu page (T3). Card grid with minmax columns. */
export function TeamManagementPage({ onNavigateBack, embedded }: { onNavigateBack: () => void; embedded?: boolean }) {
  const [teams, setTeams] = useState<Team[]>([]);
  const [selected, setSelected] = useState<Team | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const { call: fetchTeams } = useFrappePostCall("excom.excom.api.teams.get_all_teams");
  const { call: createTeam } = useFrappePostCall("excom.excom.api.teams.create_team");
  const load = useCallback(async () => { try { const res = await fetchTeams({}); setTeams((res as any)?.message || []); } catch { toast.error("Failed to load teams"); } }, [fetchTeams]);
  useEffect(() => { load(); }, [load]);

  if (selected) return <TeamDetailView team={selected} embedded={embedded} onBack={() => { setSelected(null); load(); }} />;

  return (
    <AdminPage title="Teams" icon={<Shield />} onBack={onNavigateBack} embedded={embedded} actions={<Button variant="primary" size="sm" onClick={() => setShowCreate(true)}><Plus />New team</Button>}>
      {teams.length === 0 ? <EmptyState icon={<Shield />} title="No teams yet" hint="Teams decide who sees which conversations." /> : (
        <div className="grid gap-3 [grid-template-columns:repeat(auto-fill,minmax(min(100%,260px),1fr))]">
          {teams.map((t) => {
            const general = t.name === "General";
            return (
              <button key={t.name} type="button" onClick={() => setSelected(t)} className="rounded-lg border border-border p-3 text-left hover:bg-surface-hover min-w-0">
                <div className="flex items-center gap-2 min-w-0"><h3 className="text-sm font-medium text-ink-1 truncate flex-1">{t.team_name}</h3>{general && <Lock className="size-3.5 text-crayon-amber-text shrink-0" aria-label="System team" />}<Badge accent="blue" count={t.member_count} /></div>
                {t.description && <p className="text-xs text-ink-3 line-clamp-2 mt-1">{t.description}</p>}
              </button>
            );
          })}
        </div>
      )}
      <CreateTeamDialog open={showCreate} onOpenChange={setShowCreate} onCreate={async (name, desc) => { try { await createTeam({ team_name: name, description: desc }); toast.success("Team created"); setShowCreate(false); load(); } catch { toast.error("Failed to create team"); } }} />
    </AdminPage>
  );
}
