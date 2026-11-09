import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Archive, Bot, Check, Eye, ExternalLink, FlaskConical, Play, RefreshCw, Rocket, Square, Undo2, X } from "lucide-react";
import { toast } from "sonner";
import { useFrappeGetCall } from "@/lib/api";
import { AdminPage, DataTable } from "../shell/AdminPage";
import { Avatar, Button, Chip, EmptyState, SegmentedControl, type Accent } from "../primitives";
import { cn } from "../ui/utils";
import { parseError } from "../../lib/errors";
import { deskUrl } from "../../hooks/useRecordLinks";
import { currentUser } from "../../lib/ui-flag";
import type { AgentDetail, AgentTask, Proposal } from "../../types/agents";
import { HtmlPreview, M, useAgentAction, Panel, PROPOSAL_LABEL, ReadinessChip, ReasonDialog, REJECT_REASONS, RISK_ACCENT, SectionPanel, Stat, StatusChip, STATUS_HINT, TypeChip, cost, when } from "./bits";
import { BriefTab } from "./BriefTab";
import { ReadinessTab } from "./ReadinessTab";
import { ChannelsTab } from "./ChannelsTab";
import { AutonomyTab } from "./AutonomyTab";
import { ShadowTab } from "./ShadowTab";
import { ResearchTab } from "./ResearchTab";

const TABS = [
  { value: "overview", label: "Overview" },
  { value: "needs-you", label: "Needs you" },
  { value: "activity", label: "Activity" },
  { value: "shadow", label: "Shadow" },
  { value: "research", label: "Research" },
  { value: "brief", label: "Brief" },
  { value: "playbooks", label: "Playbooks" },
  { value: "knowledge", label: "Knowledge" },
  { value: "readiness", label: "Readiness" },
  { value: "channels", label: "Channels and limits" },
  { value: "autonomy", label: "Autonomy" },
  { value: "usage", label: "Usage" },
] as const;
export type AgentTab = (typeof TABS)[number]["value"];

/** /agents/:name/:tab — everything about one agent (HLD-007 §15.3). */
export function AgentPage() {
  const { name = "", tab: raw } = useParams();
  const navigate = useNavigate();
  const { data, error, isLoading, mutate } = useFrappeGetCall<{ message: AgentDetail }>(M.agent, { name }, `agent-${name}`, { revalidateOnFocus: true, shouldRetryOnError: false });
  const d = data?.message;
  const tab: AgentTab = (TABS.find((t) => t.value === raw)?.value ?? "overview") as AgentTab;
  const setTab = (t: AgentTab) => navigate(`/agents/${encodeURIComponent(name)}/${t}`, { replace: true });
  const reload = () => { mutate(); };

  if (!d) {
    return (
      <AdminPage embedded title="Agent" icon={<Bot />} onBack={() => navigate("/agents")}>
        {error ? <p role="alert" className="text-sm text-crayon-rose-text">{parseError(error).message}</p> : isLoading ? <p className="text-sm text-ink-3">Loading…</p> : null}
      </AdminPage>
    );
  }
  const a = d.agent;
  const openBlockers = d.gaps.filter((g) => g.status === "Open" && g.severity === "Blocker").length;
  const counts: Partial<Record<AgentTab, number>> = { "needs-you": d.needs_you.length, readiness: openBlockers };
  return (
    <AdminPage
      embedded
      wide
      onBack={() => navigate("/agents")}
      icon={<Avatar name={a.agent_name} src={a.avatar || undefined} size={24} />}
      title={<span className="flex items-center gap-2 min-w-0"><span className="truncate">{a.agent_name}</span>{a.title && <span className="hidden tablet:inline text-sm font-normal text-ink-3 truncate">{a.title}</span>}</span>}
      actions={<><TypeChip type={a.agent_type} /><StatusChip status={a.status} /></>}
      toolbar={
        <div className="min-w-0 max-w-full overflow-x-auto">
          <SegmentedControl<AgentTab> value={tab} onChange={setTab} ariaLabel="Agent sections" segments={TABS.map((t) => ({ value: t.value, label: t.label, count: counts[t.value] }))} />
        </div>
      }
    >
      {tab === "overview" && <OverviewTab d={d} reload={reload} go={setTab} />}
      {tab === "needs-you" && <NeedsYouTab d={d} reload={reload} />}
      {tab === "activity" && <ActivityTab tasks={d.activity} />}
      {tab === "shadow" && <ShadowTab d={d} />}
      {tab === "research" && <ResearchTab d={d} />}
      {tab === "brief" && <BriefTab d={d} reload={reload} />}
      {tab === "playbooks" && <PlaybooksTab d={d} />}
      {tab === "knowledge" && <KnowledgeTab d={d} reload={reload} />}
      {tab === "readiness" && <ReadinessTab d={d} reload={reload} />}
      {tab === "channels" && <ChannelsTab d={d} reload={reload} />}
      {tab === "autonomy" && <AutonomyTab d={d} reload={reload} />}
      {tab === "usage" && <UsageTab d={d} />}
    </AdminPage>
  );
}

/* ─── Overview ─── */

function Line({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-3 py-1 min-w-0 text-sm">
      <dt className="w-32 shrink-0 text-ink-3">{label}</dt>
      <dd className="min-w-0 flex-1 break-words text-ink-1">{children}</dd>
    </div>
  );
}

function OverviewTab({ d, reload, go }: { d: AgentDetail; reload: () => void; go: (t: AgentTab) => void }) {
  const a = d.agent;
  const status = useAgentAction(M.setStatus);
  const stop = useAgentAction(M.stop);
  const resume = useAgentAction(M.resume);
  const selfTest = useAgentAction(M.selfTest);
  const ack = useAgentAction(M.acknowledge);
  const [stopping, setStopping] = useState(false);
  const busy = status.loading || resume.loading || selfTest.loading;
  const move = async (to: string, ok: string) => { if (await status.run({ name: a.name, status: to }, ok, "Could not change the status")) reload(); };
  const goLiveBlock = a.readiness_state === "Blocked" ? "Readiness has open blockers: fix or waive them first."
    : !a.brief_approved_on ? "Approve the brief first."
    : "";
  const gapCounts = { Blocker: 0, Warning: 0, Info: 0 } as Record<string, number>;
  d.gaps.filter((g) => g.status === "Open").forEach((g) => { gapCounts[g.severity] = (gapCounts[g.severity] || 0) + 1; });
  const me = currentUser();
  const watcher = me === a.owner_user || me === a.backup_user;

  return (
    <div className="grid grid-cols-1 laptop:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-2 items-start">
      <Panel title="Status">
        <div className="flex items-center gap-2 flex-wrap">
          <StatusChip status={a.status} size="md" />
          <span className="text-sm text-ink-2 min-w-0">{STATUS_HINT[a.status]}</span>
        </div>
        {a.status === "On Hold" && a.hold_reason && <p className="text-sm text-crayon-rose-text mt-2 break-words">Why: {a.hold_reason}</p>}
        {d.can_change && (
          <div className="flex flex-wrap gap-1.5 mt-3">
            {(a.status === "Draft" || a.status === "Preparing") && (
              <Button variant="primary" size="sm" disabled={busy} onClick={() => move("Shadow", "Started in Shadow: nothing is sent")}><Eye />Start Shadow</Button>
            )}
            {a.status === "Shadow" && (
              <Button variant="primary" size="sm" disabled={busy || Boolean(goLiveBlock)} title={goLiveBlock || undefined}
                onClick={() => { if (window.confirm(`Put ${a.agent_name} live? It will send what its autonomy settings allow.`)) move("Live", `${a.agent_name} is live`); }}><Rocket />Go live</Button>
            )}
            {a.status === "Live" && <Button size="sm" disabled={busy} onClick={() => move("Shadow", "Back in Shadow: nothing is sent")}><Undo2 />Back to Shadow</Button>}
            {(a.status === "Shadow" || a.status === "Live") && <Button size="sm" variant="danger" disabled={busy} onClick={() => setStopping(true)}><Square />Stop</Button>}
            {a.status === "On Hold" && (
              <Button variant="primary" size="sm" disabled={busy} onClick={async () => { if (await resume.run({ name: a.name }, "Resumed", "Could not resume")) reload(); }}><Play />Resume</Button>
            )}
            <Button size="sm" variant="ghost" disabled={busy} onClick={async () => { if (await selfTest.run({ name: a.name }, "Self-test queued: see Activity", "Could not run the self-test")) reload(); }}><FlaskConical />Run self-test</Button>
            {a.status !== "Retired" && (
              <Button size="sm" variant="ghost" disabled={busy} onClick={() => { if (window.confirm(`Retire ${a.agent_name}? It stops for good; its history is kept.`)) move("Retired", `${a.agent_name} is retired`); }}><Archive />Retire</Button>
            )}
          </div>
        )}
        {d.can_change && a.status === "Shadow" && goLiveBlock && <p className="text-xs text-ink-3 mt-1.5">Going live: {goLiveBlock}</p>}
      </Panel>

      <Panel title="Who is in charge" actions={watcher ? <Button size="sm" variant="ghost" disabled={ack.loading} onClick={async () => { if (await ack.run({ name: a.name }, "Thanks: noted that you are watching", "Could not save that")) reload(); }}><Check />I'm watching it</Button> : undefined}>
        <dl>
          <Line label="Owner">{a.owner_user}</Line>
          {a.acting_owner && a.acting_owner !== a.owner_user && <Line label="In charge now">{a.acting_owner} <span className="text-ink-3">(the owner is away)</span></Line>}
          <Line label="Backup">{a.backup_user || <span className="text-crayon-amber-text">None yet</span>}</Line>
          <Line label="Team">{a.team || "—"}</Line>
          {a.escalation_user && <Line label="Escalate to">{a.escalation_user}</Line>}
          {a.reviewers && <Line label="Also reviews">{a.reviewers}</Line>}
          <Line label="Owner last seen">{when(a.owner_last_seen)}</Line>
        </dl>
      </Panel>

      <Panel title="Readiness" actions={<Button size="sm" variant="link" onClick={() => go("readiness")}>See what to fix</Button>}>
        <div className="flex items-center gap-2 flex-wrap">
          <ReadinessChip state={a.readiness_state} size="md" />
          <span className="text-xs text-ink-3">checked {when(a.readiness_checked_on)}</span>
        </div>
        <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)] gap-2 mt-3">
          <Stat label="Blockers" value={gapCounts.Blocker} tone={gapCounts.Blocker ? "rose" : undefined} />
          <Stat label="Warnings" value={gapCounts.Warning} tone={gapCounts.Warning ? "amber" : undefined} />
          <Stat label="Notes" value={gapCounts.Info} />
        </div>
      </Panel>

      <Panel title="Brief" actions={<Button size="sm" variant="link" onClick={() => go("brief")}>Open the brief</Button>}>
        {a.brief_approved_on
          ? <p className="text-sm text-ink-1">Version {a.brief_version}, approved by {a.brief_approved_by} on {when(a.brief_approved_on)}.</p>
          : <p className="text-sm text-crayon-amber-text">Not approved yet. It cannot go live until it is.</p>}
        {a.what_it_is && <p className="text-sm text-ink-2 mt-2 break-words line-clamp-4">{a.what_it_is}</p>}
      </Panel>

      <Panel title="Usage" actions={<Button size="sm" variant="link" onClick={() => go("usage")}>Details</Button>}>
        <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)] gap-2">
          <Stat label={a.daily_ai_budget ? `AI today, of ${cost(a.daily_ai_budget)}` : "AI today"} value={cost(d.usage.today)} />
          <Stat label={a.monthly_ai_budget ? `This month, of ${cost(a.monthly_ai_budget)}` : "AI this month"} value={cost(d.usage.month)} />
          <Stat label="AI calls this month" value={d.usage.calls_month} />
        </div>
      </Panel>

      <Panel title="Targets">
        {d.kpi_targets.length === 0 ? <p className="text-sm text-ink-3">No targets set.</p> : (
          <ul className="space-y-1">
            {d.kpi_targets.map((k, i) => <li key={i} className="text-sm text-ink-1 flex gap-2 min-w-0"><span className="min-w-0 flex-1 break-words">{k.metric}</span><span className="tabular-nums shrink-0">{k.value} per {k.period.toLowerCase()}</span></li>)}
          </ul>
        )}
        {(d.markets.length > 0 || d.products.length > 0) && (
          <dl className="mt-3 pt-2 border-t border-border">
            {d.markets.length > 0 && <Line label="Markets">{d.markets.map((m) => m.country).join(", ")}</Line>}
            {d.products.length > 0 && <Line label="Products">{d.products.map((p) => p.item_group || p.item).filter(Boolean).join(", ")}{d.products[0]?.price_list ? ` · price list ${d.products[0].price_list}` : ""}</Line>}
            {a.keywords && <Line label="Keywords">{a.keywords}</Line>}
          </dl>
        )}
      </Panel>

      <ReasonDialog open={stopping} onOpenChange={setStopping} title={`Stop ${a.agent_name}`} danger confirmLabel="Stop now" noteLabel="Why are you stopping it?"
        intro="It stops at once: queued work is skipped and approved messages are held. Drafts are kept, and you can resume it later."
        onConfirm={async ({ note }) => { const r = await stop.run({ name: a.name, reason: note }, `${a.agent_name} is on hold`, "Could not stop the agent"); if (r) reload(); return Boolean(r); }} />
    </div>
  );
}

/* ─── Needs you ─── */

function NeedsYouTab({ d, reload }: { d: AgentDetail; reload: () => void }) {
  const all = <Link to="/agents/needs-you" className="text-sm text-crayon-blue-text hover:underline">Open Needs you for all agents, with checks, edits and bulk approval</Link>;
  if (d.needs_you.length === 0) return <EmptyState icon={<Check />} title="Nothing waits for you" hint="Messages and actions this agent wants approved will appear here." action={all} />;
  return (
    <div className="space-y-2 max-w-3xl">
      <p>{all}</p>
      <ul className="space-y-2">{d.needs_you.map((p) => <li key={p.name}><ProposalCard p={p} reload={reload} /></li>)}</ul>
    </div>
  );
}

function ProposalCard({ p, reload }: { p: Proposal; reload: () => void }) {
  const decide = useAgentAction(M.decide);
  const [rejecting, setRejecting] = useState(false);
  return (
    <article className="rounded-lg border border-border bg-surface min-w-0" aria-label={PROPOSAL_LABEL[p.action] || p.action}>
      <header className="flex items-center gap-2 px-3 min-h-10 py-1.5 border-b border-border flex-wrap min-w-0">
        <h3 className="text-sm font-medium text-ink-1 truncate min-w-0">{PROPOSAL_LABEL[p.action] || p.action}</h3>
        {p.risk && <Chip size="sm" accent={RISK_ACCENT[p.risk] ?? "neutral"} label={`${p.risk} risk`} />}
        {p.status === "Held" && <Chip size="sm" accent="amber" label="Held" />}
        <span className="text-xs text-ink-3 ml-auto shrink-0">{when(p.creation)}</span>
      </header>
      <div className="p-3 space-y-2 min-w-0">
        {p.subject_doctype && p.subject_name && (
          <p className="text-sm text-ink-2 min-w-0">For <a className="text-crayon-blue-text hover:underline inline-flex items-center gap-1 max-w-full" href={deskUrl(p.subject_doctype, p.subject_name)} target="_blank" rel="noreferrer"><span className="truncate">{p.subject_doctype} {p.subject_name}</span><ExternalLink className="size-3 shrink-0" /></a></p>
        )}
        {p.hold_reason && <p className="text-sm text-crayon-amber-text break-words">Held: {p.hold_reason}</p>}
        {p.preview_html ? <HtmlPreview html={p.preview_html} title={`Preview: ${PROPOSAL_LABEL[p.action] || p.action}`} /> : <p className="text-sm text-ink-3">No preview.</p>}
        <div className="flex gap-1.5 flex-wrap">
          <Button variant="primary" size="sm" disabled={decide.loading} onClick={async () => { if (await decide.run({ name: p.name, decision: "approve" }, "Approved", "Could not approve")) reload(); }}><Check />Approve</Button>
          <Button variant="danger" size="sm" disabled={decide.loading} onClick={() => setRejecting(true)}><X />Reject</Button>
        </div>
      </div>
      <ReasonDialog open={rejecting} onOpenChange={setRejecting} title="Reject" confirmLabel="Reject" danger choices={REJECT_REASONS} noteLabel="Note for the agent" noteOptional
        intro="The reason teaches the agent what to do differently."
        onConfirm={async ({ choice, note }) => { const r = await decide.run({ name: p.name, decision: "reject", reason: choice, note }, "Rejected", "Could not reject"); if (r) reload(); return Boolean(r); }} />
    </article>
  );
}

/* ─── Activity ─── */

const TASK_ACCENT: Record<string, Accent> = { Queued: "neutral", Running: "blue", Done: "green", Failed: "rose", Skipped: "neutral", Held: "amber", Batched: "violet" };
const TASK_KIND: Record<string, string> = { echo: "Self-test", inbound: "Message received" };
const kindLabel = (k: string) => TASK_KIND[k] || (k ? k.charAt(0).toUpperCase() + k.slice(1).replace(/_/g, " ") : "—");

function ActivityTab({ tasks }: { tasks: AgentTask[] }) {
  return (
    <div className="rounded-lg border border-border">
      <DataTable<AgentTask>
        rows={tasks}
        keyOf={(t) => t.name}
        empty={<EmptyState title="No activity yet" hint="Each piece of work the agent does shows here, with its result." compact />}
        columns={[
          { key: "kind", label: "Work", primary: true, render: (t) => kindLabel(t.kind) },
          { key: "status", label: "Status", render: (t) => <Chip size="sm" accent={TASK_ACCENT[t.status] ?? "neutral"} label={t.status} /> },
          { key: "outcome", label: "Result", className: "max-w-[420px]", render: (t) => t.error ? <span className="text-crayon-rose-text" title={t.error}>{t.error}</span> : <span title={t.outcome || ""}>{t.outcome || "—"}</span> },
          { key: "creation", label: "Started", render: (t) => when(t.creation) },
          { key: "ended", label: "Finished", render: (t) => when(t.ended_on) },
          { key: "cost", label: "AI cost", align: "right", render: (t) => (t.ai_cost ? cost(t.ai_cost) : "—") },
        ]}
      />
    </div>
  );
}

/* ─── Playbooks ─── */

const PLAYBOOK_ACCENT: Record<string, Accent> = { Draft: "neutral", "In Review": "amber", Approved: "green", Retired: "neutral" };

function PlaybooksTab({ d }: { d: AgentDetail }) {
  const books = d.playbooks.filter(Boolean) as NonNullable<AgentDetail["playbooks"][number]>[];
  if (!books.length) return <EmptyState title="No playbook linked" hint="The playbook holds what the agent may say: facts, claims, answers to objections." />;
  return (
    <ul className="rounded-lg border border-border divide-y divide-border max-w-3xl">
      {books.map((p) => (
        <li key={p.name} className="flex items-center gap-2 px-3 min-h-12 py-2 min-w-0 flex-wrap">
          <span className="text-sm text-ink-1 truncate flex-1 min-w-0">{p.title || p.name}</span>
          <Chip size="sm" accent={PLAYBOOK_ACCENT[p.status || ""] ?? "neutral"} label={p.status || "—"} />
          <span className="text-xs text-ink-3 tabular-nums">version {p.version ?? 0}</span>
          <a href={`/app/excom-ai-playbook/${encodeURIComponent(p.name)}`} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-sm text-crayon-blue-text hover:underline">Open<ExternalLink className="size-3.5" /></a>
        </li>
      ))}
    </ul>
  );
}

/* ─── Knowledge ─── */

function KnowledgeTab({ d, reload }: { d: AgentDetail; reload: () => void }) {
  const refresh = useAgentAction(M.refresh);
  const k = d.knowledge;
  const button = d.can_change ? (
    <Button size="sm" disabled={refresh.loading} onClick={async () => {
      const r = await refresh.run({ name: d.agent.name }, "", "Could not refresh");
      if (r) { toast.success((r as { changes?: string }).changes ? "Refreshed: something changed" : "Refreshed: nothing changed"); reload(); }
    }}><RefreshCw className={refresh.loading ? "animate-spin" : ""} />{refresh.loading ? "Refreshing…" : "Refresh"}</Button>
  ) : null;
  if (!k) return <EmptyState title="Nothing gathered yet" hint="Refresh to ask the ERP what this agent should know." action={button} />;
  const changes = (k.changes || "").split("\n").filter(Boolean);
  return (
    <div className="space-y-2">
      <Panel title={`Version ${k.version}`} actions={button}>
        <p className="text-sm text-ink-2">Gathered {when(k.generated_on)}{k.approx_tokens ? ` · size for the AI: about ${k.approx_tokens.toLocaleString()} tokens` : ""}. It is rebuilt every night.</p>
        <h3 className="text-xs text-ink-3 mt-3 mb-1">What changed since the last version</h3>
        {changes.length ? <ul className="list-disc pl-5 text-sm text-ink-1 space-y-0.5">{changes.map((c, i) => <li key={i} className="break-words">{c}</li>)}</ul> : <p className="text-sm text-ink-3">Nothing.</p>}
      </Panel>
      <div className="grid grid-cols-1 laptop:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-2 items-start">
        {k.sections.map((s) => <SectionPanel key={s.section} section={s} />)}
      </div>
    </div>
  );
}

/* ─── Usage ─── */

function Meter({ label, used, budget }: { label: string; used: number; budget?: number | null }) {
  const pct = budget ? Math.min(100, Math.round((used / budget) * 100)) : null;
  return (
    <div className="min-w-0">
      <div className="flex items-baseline gap-2 min-w-0">
        <span className="text-sm text-ink-1 flex-1 min-w-0 truncate">{label}</span>
        <span className="text-sm tabular-nums text-ink-1">{cost(used)}</span>
        <span className="text-xs text-ink-3 tabular-nums">{budget ? `of ${cost(budget)}` : "no budget set"}</span>
      </div>
      {pct !== null && (
        <div className="h-1.5 rounded-full bg-surface-sunken mt-1 overflow-hidden" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} aria-label={`${label}: ${pct}% of the budget`}>
          <div className={cn("h-full rounded-full", pct >= 90 ? "bg-crayon-rose-base" : pct >= 70 ? "bg-crayon-amber-base" : "bg-crayon-green-base")} style={{ width: `${pct}%` }} />
        </div>
      )}
    </div>
  );
}

function UsageTab({ d }: { d: AgentDetail }) {
  const a = d.agent;
  const recent = d.activity.reduce((n, t) => n + Number(t.ai_cost || 0), 0);
  return (
    <div className="grid grid-cols-1 laptop:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-2 items-start">
      <Panel title="AI spend" actions={d.can_change ? <Link to={`/agents/${encodeURIComponent(a.name)}/channels`} className="text-sm text-crayon-blue-text hover:underline">Change budgets</Link> : undefined}>
        <div className="space-y-3">
          <Meter label="Today" used={d.usage.today} budget={a.daily_ai_budget} />
          <Meter label="This month" used={d.usage.month} budget={a.monthly_ai_budget} />
        </div>
        <p className="text-xs text-ink-3 mt-3">From the AI call log, in its base currency.</p>
      </Panel>
      <Panel title="Work">
        <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-2">
          <Stat label="AI calls this month" value={d.usage.calls_month} />
          <Stat label={`AI cost of the last ${d.activity.length} tasks`} value={cost(recent)} />
        </div>
        {a.model && <p className="text-xs text-ink-3 mt-3">Model: {a.model}</p>}
      </Panel>
    </div>
  );
}

