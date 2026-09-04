import { useState } from "react";
import { useFrappePostCall } from "frappe-react-sdk";
import { useFrappeGetCall } from "@/lib/api";
import { Search, ArrowRightLeft, Loader2, ExternalLink } from "lucide-react";
import { toast } from "sonner";
import { Input, Chip, Avatar, Button, EmptyState } from "../primitives";
import { DataTable } from "../shell/AdminPage";
import { ReassignDialog } from "./TeamsAdmin";
import { serverMessage } from "./util";

interface U { name: string; full_name: string; user_image?: string; enabled: number; last_active?: string; roles: string[]; teams: { team: string; role: string }[]; open_threads: number; open_leads: number }

/** Users & roles: who can use Excom, who manages it, which teams they are in, and what they own. */
export function UsersAdmin() {
  const [q, setQ] = useState("");
  const [onlyExcom, setOnlyExcom] = useState(true);
  const { data, isLoading, mutate } = useFrappeGetCall<{ message: U[] }>("excom.excom.api.admin.list_users", { q }, `admin-users-${q}`, { revalidateOnFocus: false, keepPreviousData: true });
  const { call: setRoles } = useFrappePostCall("excom.excom.api.admin.set_user_roles");
  const [reassign, setReassign] = useState<U | null>(null);
  const rows = (data?.message ?? []).filter((u) => !onlyExcom || u.roles.length > 0 || u.teams.length > 0 || u.open_threads > 0);
  const toggle = async (u: U, role: string) => {
    const next = u.roles.includes(role) ? u.roles.filter((r) => r !== role) : [...u.roles, role];
    try { await setRoles({ user: u.name, roles: JSON.stringify(next.filter((r) => r !== "System Manager")) }); toast.success(`${u.full_name}: ${role} ${u.roles.includes(role) ? "removed" : "granted"}`); mutate(); } catch (e) { toast.error(serverMessage(e)); }
  };
  const cols = [
    { key: "user", label: "User", primary: true, render: (u: U) => <span className="inline-flex items-center gap-2 min-w-0"><Avatar name={u.full_name} src={u.user_image} size={20} /><span className="truncate"><span className="text-ink-1 font-medium">{u.full_name}</span> <span className="text-xs text-ink-3">{u.name}</span></span>{!u.enabled && <Chip size="sm" accent="amber" label="Disabled" />}</span> },
    { key: "roles", label: "Excom access", render: (u: U) => <span className="inline-flex gap-1">{["Excom User", "Excom Manager"].map((r) => <button key={r} type="button" onClick={() => toggle(u, r)} className={`rounded-full px-2 h-6 text-xs border ${u.roles.includes(r) ? "bg-crayon-blue-tint text-crayon-blue-text border-transparent" : "border-border text-ink-3 hover:text-ink-1"}`} title={`Click to ${u.roles.includes(r) ? "revoke" : "grant"}`}>{r.replace("Excom ", "")}</button>)}{u.roles.includes("System Manager") && <Chip size="sm" accent="violet" label="Sys admin" />}</span> },
    { key: "teams", label: "Teams", render: (u: U) => u.teams.length ? <span className="text-ink-2 text-xs">{u.teams.map((t) => `${t.team}${t.role === "Manager" ? " (M)" : ""}`).join(", ")}</span> : <span className="text-ink-muted">—</span> },
    { key: "work", label: "Owns", align: "right" as const, render: (u: U) => <span className={`text-xs tabular-nums ${!u.enabled && (u.open_threads || u.open_leads) ? "text-crayon-amber-text" : "text-ink-2"}`}>{u.open_threads} chats · {u.open_leads} leads</span> },
    { key: "act", label: "", align: "right" as const, render: (u: U) => <span className="inline-flex items-center gap-1">{(u.open_threads > 0 || u.open_leads > 0) && <Button size="sm" variant={u.enabled ? "ghost" : "default"} onClick={() => setReassign(u)}><ArrowRightLeft />Reassign</Button>}<a href={`/app/user/${encodeURIComponent(u.name)}`} target="_blank" rel="noreferrer" className="text-ink-3 hover:text-ink-1" title="Open user in Desk (password, enable/disable)"><ExternalLink className="size-4" /></a></span> },
  ];
  return (
    <div className="flex flex-col min-h-0 h-full">
      <div className="shrink-0 flex items-center gap-2 px-3 py-2 border-b border-border flex-wrap">
        <div className="relative flex-1 min-w-[160px] max-w-sm"><Search className="absolute left-2 top-1/2 -translate-y-1/2 size-4 text-ink-3" /><Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search users…" className="pl-8" /></div>
        <label className="flex items-center gap-1.5 text-xs text-ink-2"><input type="checkbox" className="size-4" checked={onlyExcom} onChange={(e) => setOnlyExcom(e.target.checked)} />Excom users only</label>
        <span className="text-xs text-ink-3 tabular-nums">{rows.length}</span>
      </div>
      <p className="px-3 py-1.5 text-xs text-ink-3 border-b border-border bg-surface-sunken">Click a role pill to grant or revoke. Disabled users keep their rows so you can move their chats and leads. Enabling, disabling and passwords stay in Desk → User.</p>
      <div className="flex-1 min-h-0 overflow-y-auto">
        {isLoading && !data ? <div className="flex justify-center py-8 text-ink-3"><Loader2 className="size-5 animate-spin" /></div>
          : <DataTable rows={rows} columns={cols} keyOf={(u) => u.name} empty={<EmptyState title="No users" compact />} />}
      </div>
      {reassign && <ReassignDialog user={reassign.name} label={reassign.full_name} onClose={() => setReassign(null)} onDone={() => { setReassign(null); mutate(); }} />}
    </div>
  );
}
