import { useState } from "react";
import { Link } from "react-router-dom";
import { AlertOctagon, AlertTriangle, ChevronRight, ExternalLink, Info, ListTodo, RefreshCw, ShieldOff, Wrench } from "lucide-react";
import { toast } from "sonner";
import { ACCENT_CLASSES, Button, Chip, EmptyState, Field, Modal, type Accent } from "../primitives";
import { LinkField } from "../crm/LinkField";
import { cn } from "../ui/utils";
import { deskUrl } from "../../hooks/useRecordLinks";
import type { AgentDetail, ReadinessGap, ReadinessResult } from "../../types/agents";
import { M, Panel, ReadinessChip, ReasonDialog, Stat, appPath, useAgentAction, when } from "./bits";

const SEVERITY: Record<ReadinessGap["severity"], { title: string; hint: string; accent: Accent; icon: React.ReactNode }> = {
  Blocker: { title: "Blockers", hint: "These keep the agent from going live. In Shadow it may still prepare.", accent: "rose", icon: <AlertOctagon /> },
  Warning: { title: "Warnings", hint: "Worth fixing; they do not stop it.", accent: "amber", icon: <AlertTriangle /> },
  Info: { title: "Notes", hint: "To check by hand.", accent: "blue", icon: <Info /> },
};
const ORDER: ReadinessGap["severity"][] = ["Blocker", "Warning", "Info"];

/** Readiness tab (HLD-007 §7): what must be true before the first customer arrives, and who fixes it. */
export function ReadinessTab({ d, reload }: { d: AgentDetail; reload: () => void }) {
  const a = d.agent;
  const check = useAgentAction(M.runReadiness);
  const open = d.gaps.filter((g) => g.status === "Open");
  const closed = d.gaps.filter((g) => g.status !== "Open");
  const count = (s: string) => open.filter((g) => g.severity === s).length;
  return (
    <div className="space-y-2 max-w-4xl">
      <Panel actions={d.can_change ? (
        <Button size="sm" disabled={check.loading} onClick={async () => {
          const r = (await check.run({ name: a.name }, "", "Could not check readiness")) as ReadinessResult | null;
          if (r) { toast.success(r.state === "Blocked" ? `Checked: ${r.blockers} blockers open` : r.state === "Warnings" ? `Checked: ready, ${r.warnings} warnings` : "Checked: ready"); reload(); }
        }}><RefreshCw className={check.loading ? "animate-spin" : ""} />{check.loading ? "Checking…" : "Check again"}</Button>
      ) : undefined} title="Readiness">
        <div className="flex items-center gap-2 flex-wrap">
          <ReadinessChip state={a.readiness_state} size="md" />
          <span className="text-xs text-ink-3">checked {when(a.readiness_checked_on)} · also every night</span>
        </div>
        <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)] gap-2 mt-3 max-w-md">
          <Stat label="Blockers" value={count("Blocker")} tone={count("Blocker") ? "rose" : undefined} />
          <Stat label="Warnings" value={count("Warning")} tone={count("Warning") ? "amber" : undefined} />
          <Stat label="Notes" value={count("Info")} />
        </div>
      </Panel>

      {open.length === 0 && <EmptyState title="Nothing open" hint="Every check passes, or what failed was waived." compact />}
      {ORDER.map((sev) => {
        const gaps = open.filter((g) => g.severity === sev);
        if (!gaps.length) return null;
        const s = SEVERITY[sev];
        return (
          <section key={sev} aria-labelledby={`sev-${sev}`} className="space-y-1.5">
            <h2 id={`sev-${sev}`} className="flex items-baseline gap-2 pt-2">
              <span className="text-sm font-medium text-ink-1">{s.title} · {gaps.length}</span>
              <span className="text-xs text-ink-3">{s.hint}</span>
            </h2>
            <ul className="space-y-1.5">{gaps.map((g) => <li key={g.name}><GapCard g={g} d={d} reload={reload} /></li>)}</ul>
          </section>
        );
      })}

      {closed.length > 0 && (
        <details className="group pt-2">
          <summary className="text-sm text-ink-2 cursor-pointer select-none inline-flex items-center gap-1 list-none [&::-webkit-details-marker]:hidden">
            <ChevronRight className="size-4 transition-transform group-open:rotate-90" />Fixed or waived · {closed.length}
          </summary>
          <ul className="space-y-1.5 mt-1.5">{closed.map((g) => <li key={g.name}><GapCard g={g} d={d} reload={reload} /></li>)}</ul>
        </details>
      )}
    </div>
  );
}

function FixLink({ route }: { route: string }) {
  const inApp = appPath(route);
  const cls = "inline-flex items-center gap-1 text-sm text-crayon-blue-text hover:underline";
  return inApp
    ? <Link to={inApp} className={cls}><Wrench className="size-3.5" />Fix</Link>
    : <a href={route} target="_blank" rel="noreferrer" className={cls}><Wrench className="size-3.5" />Fix<ExternalLink className="size-3" /></a>;
}

function GapCard({ g, d, reload }: { g: ReadinessGap; d: AgentDetail; reload: () => void }) {
  const s = SEVERITY[g.severity] ?? SEVERITY.Info;
  const todo = useAgentAction(M.gapTodo);
  const waive = useAgentAction(M.waiveGap);
  const [picking, setPicking] = useState(false);
  const [user, setUser] = useState("");
  const [waiving, setWaiving] = useState(false);
  const isOpen = g.status === "Open";
  return (
    <article className={cn("rounded-lg border bg-surface p-3 min-w-0 border-l-[3px]", isOpen ? `border-border ${g.severity === "Blocker" ? "border-l-crayon-rose-base" : g.severity === "Warning" ? "border-l-crayon-amber-base" : "border-l-crayon-blue-base"}` : "border-border border-l-border")}>
      <div className="flex items-center gap-2 flex-wrap min-w-0">
        <span className={cn("[&_svg]:size-4 shrink-0", isOpen ? ACCENT_CLASSES[s.accent].text : "text-ink-3")} aria-hidden>{s.icon}</span>
        <h3 className="text-sm font-medium text-ink-1 min-w-0 truncate">{g.title || g.rule}</h3>
        {!isOpen && <Chip size="sm" accent={g.status === "Fixed" ? "green" : "neutral"} label={g.status} />}
        {g.todo && <a href={deskUrl("ToDo", g.todo)} target="_blank" rel="noreferrer" className="text-xs text-crayon-blue-text hover:underline inline-flex items-center gap-0.5">ToDo made<ExternalLink className="size-3" /></a>}
      </div>
      {g.detail && <p className="text-sm text-ink-2 mt-1 break-words">{g.detail}</p>}
      {g.fix_hint && isOpen && <p className="text-xs text-ink-3 mt-1 break-words">How to fix: {g.fix_hint}</p>}
      {g.status === "Waived" && <p className="text-xs text-ink-3 mt-1 break-words">Waived by {g.waived_by || "someone"}{g.waive_reason ? `: ${g.waive_reason}` : ""}</p>}
      {isOpen && (g.fix_route || d.can_change) && (
        <div className="flex items-center gap-1.5 flex-wrap mt-2">
          {g.fix_route && <FixLink route={g.fix_route} />}
          {d.can_change && <Button size="sm" variant="ghost" onClick={() => { setUser(""); setPicking(true); }}><ListTodo />Make a ToDo</Button>}
          {d.can_change && <Button size="sm" variant="ghost" onClick={() => setWaiving(true)}><ShieldOff />Waive</Button>}
        </div>
      )}
      <Modal open={picking} onOpenChange={setPicking} title="Make a ToDo"
        footer={<><Button variant="ghost" onClick={() => setPicking(false)}>Cancel</Button><Button variant="primary" disabled={!user || todo.loading} onClick={async () => {
          if (await todo.run({ gap: g.name, user }, `ToDo made for ${user}`, "Could not make the ToDo")) { setPicking(false); reload(); }
        }}>{todo.loading ? "Making…" : "Make the ToDo"}</Button></>}>
        <p className="text-sm text-ink-2 mb-3">“{g.title || g.rule}” goes on this person's ToDo list, with how to fix it.</p>
        <Field label="Who fixes it" required>
          <LinkField doctype="User" value={user} onChange={setUser} placeholder="Choose a person" filters={{ enabled: 1, user_type: "System User" }} />
        </Field>
      </Modal>
      <ReasonDialog open={waiving} onOpenChange={setWaiving} title={`Waive “${g.title || g.rule}”`} confirmLabel="Waive" noteLabel="Why is it all right to go without this?"
        intro={g.severity === "Blocker" ? "Only an Excom Admin can waive a blocker. The reason is kept on record." : "The reason is kept on record."}
        onConfirm={async ({ note }) => { const r = await waive.run({ gap: g.name, reason: note }, "Waived", "Could not waive it"); if (r) reload(); return Boolean(r); }} />
    </article>
  );
}
