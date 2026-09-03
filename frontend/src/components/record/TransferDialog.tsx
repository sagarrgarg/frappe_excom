import { useEffect, useState } from "react";
import { useFrappePostCall } from "frappe-react-sdk";
import { toast } from "sonner";
import { Modal, Button, Field, Select, Input } from "../primitives";

/** Transfer conversation — moved to `⋯` (T3); confirm dialog unchanged in behaviour. */
export function TransferDialog({ open, onOpenChange, threadIds, onDone }: { open: boolean; onOpenChange: (v: boolean) => void; threadIds: string[]; onDone: () => void }) {
  const [teams, setTeams] = useState<{ name: string; team_name: string }[]>([]);
  const [members, setMembers] = useState<{ user: string; full_name: string }[]>([]);
  const [team, setTeam] = useState("");
  const [user, setUser] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const { call: fetchTeams } = useFrappePostCall("excom.excom.api.teams.get_all_teams");
  const { call: fetchMembers } = useFrappePostCall("excom.excom.api.teams.get_team_members");
  const { call: transfer } = useFrappePostCall("excom.excom.api.chat.transfer_thread");

  useEffect(() => {
    if (!open) return;
    setTeam(""); setUser(""); setNote(""); setMembers([]);
    fetchTeams({}).then((r) => setTeams((r as any)?.message || [])).catch(() => toast.error("Failed to load teams"));
  }, [open, fetchTeams]);

  useEffect(() => {
    setUser(""); setMembers([]);
    if (!team) return;
    fetchMembers({ team }).then((r) => setMembers((r as any)?.message || [])).catch(() => {});
  }, [team, fetchMembers]);

  const submit = async () => {
    setBusy(true);
    try {
      await Promise.all(threadIds.map((id) => transfer({ thread_id: id, target_team: team, target_user: user, note })));
      toast.success(user ? "Transferred and assigned" : "Transferred to team");
      onOpenChange(false); onDone();
    } catch { toast.error("Transfer failed"); } finally { setBusy(false); }
  };

  return (
    <Modal open={open} onOpenChange={onOpenChange} title="Transfer conversation" description="Move every thread of this contact to another team, or a specific member."
      footer={<><Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button><Button variant="primary" disabled={!team || busy} onClick={submit}>Transfer</Button></>}>
      <div className="space-y-3">
        <p className="text-xs text-ink-3">Moves all {threadIds.length} thread{threadIds.length === 1 ? "" : "s"} of this contact.</p>
        <Field label="Target team" required>
          <Select value={team} onChange={(e) => setTeam(e.target.value)}>
            <option value="">Select team…</option>
            {teams.map((t) => <option key={t.name} value={t.name}>{t.team_name}</option>)}
          </Select>
        </Field>
        {team && members.length > 0 && (
          <Field label="Assign to member" hint="Leave empty for team pickup">
            <Select value={user} onChange={(e) => setUser(e.target.value)}>
              <option value="">Anyone in the team</option>
              {members.map((m) => <option key={m.user} value={m.user}>{m.full_name || m.user}</option>)}
            </Select>
          </Field>
        )}
        <Field label="Note"><Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Reason for transfer…" /></Field>
      </div>
    </Modal>
  );
}
