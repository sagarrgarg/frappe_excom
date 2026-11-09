import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useFrappePostCall } from "frappe-react-sdk";
import { Bot, Inbox, Plus, Square } from "lucide-react";
import { toast } from "sonner";
import { useFrappeGetCall } from "@/lib/api";
import { PageFrame } from "../shell/PageFrame";
import { Avatar, Badge, Button, EmptyState, SegmentedControl } from "../primitives";
import { toastError } from "../ErrorDialog";
import { parseError } from "../../lib/errors";
import type { AgentCard, AgentType, NeedsYouCount } from "../../types/agents";
import { HealthDot, M, NEEDS_YOU_COUNT_KEY, ReasonDialog, Stat, StatusChip, TypeChip } from "./bits";
import { TemplatesTab } from "./TemplatesTab";

type HomeTab = "agents" | "templates";

/** The New agent button shows only for people who may build one: types() refuses everyone else. */
export function useAgentTypes() {
  const { data, error, isLoading } = useFrappeGetCall<{ message: AgentType[] }>(M.types, undefined, "agent-types", { revalidateOnFocus: false, shouldRetryOnError: false });
  return { types: data?.message ?? [], canBuild: Boolean(data?.message) && !error, isLoading };
}

/**
 * /agents — one card per agent the person may see, with today's numbers (HLD-007 §15.1);
 * /agents?tab=templates — the templates the Template Steward made for them (HLD-008).
 */
export function AgentsHome() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const tab: HomeTab = params.get("tab") === "templates" ? "templates" : "agents";
  const setTab = (t: HomeTab) => setParams(t === "agents" ? {} : { tab: t }, { replace: true });
  const { data, error, isLoading, mutate } = useFrappeGetCall<{ message: AgentCard[] }>(M.agents, undefined, "agents-home", { refreshInterval: 60_000 });
  const { canBuild } = useAgentTypes();
  const agents = data?.message ?? [];
  const newButton = canBuild ? <Button variant="primary" size="sm" onClick={() => navigate("/agents/new")}><Plus />New agent</Button> : null;
  // needs_you.count(): proposals to approve plus buyers handed to a person, for the agents this
  // person decides for. The cards' sum is only a fallback while it loads or if it fails.
  const { data: counted } = useFrappeGetCall<{ message: NeedsYouCount }>(agents.length ? M.count : null, undefined, NEEDS_YOU_COUNT_KEY, { refreshInterval: 60_000, shouldRetryOnError: false });
  const waiting = counted?.message ? counted.message.total : agents.reduce((n, a) => n + (a.waiting || 0), 0);
  const waitingHint = counted?.message ? `${counted.message.proposals} to approve, ${counted.message.waiting} ${counted.message.waiting === 1 ? "buyer" : "buyers"} waiting for a person` : undefined;
  const needsYou = agents.length > 0 ? (
    <Button size="sm" onClick={() => navigate("/agents/needs-you")} title={waitingHint} aria-label={`Needs you${waiting ? `, ${waiting} waiting${waitingHint ? `: ${waitingHint}` : ""}` : ""}`}>
      <Inbox />Needs you{waiting > 0 && <Badge solid accent="amber" count={waiting} />}
    </Button>
  ) : null;
  return (
    <PageFrame title="Agents" icon={<Bot />} actions={(needsYou || newButton) ? <>{needsYou}{newButton}</> : null}>
      {error ? (
        <p role="alert" className="text-sm text-crayon-rose-text p-3">{parseError(error).message}</p>
      ) : isLoading && !data ? (
        <p className="text-sm text-ink-3 p-3">Loading agents…</p>
      ) : agents.length === 0 ? (
        <EmptyState
          icon={<Bot />}
          title="No agents yet"
          hint={canBuild ? "An agent works one job for your team, such as finding export buyers, from a few keywords you give it." : "Agents your team builds will appear here."}
          action={newButton}
        />
      ) : (
        <div className="space-y-3 min-w-0">
          <div className="min-w-0 max-w-full overflow-x-auto border-b border-border">
            <SegmentedControl<HomeTab> value={tab} onChange={setTab} ariaLabel="Agents sections" segments={[{ value: "agents", label: "Agents", count: agents.length }, { value: "templates", label: "Templates" }]} />
          </div>
          {tab === "templates" ? <TemplatesTab /> : (
            <ul className="grid grid-cols-1 tablet:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] wide:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)] gap-2">
              {agents.map((a) => <li key={a.name} className="min-w-0"><AgentCardView agent={a} onChanged={() => mutate()} /></li>)}
            </ul>
          )}
        </div>
      )}
    </PageFrame>
  );
}

function AgentCardView({ agent: a, onChanged }: { agent: AgentCard; onChanged: () => void }) {
  const navigate = useNavigate();
  const [stopping, setStopping] = useState(false);
  const { call: stop } = useFrappePostCall(M.stop);
  const canStop = a.status === "Shadow" || a.status === "Live";
  const to = `/agents/${encodeURIComponent(a.name)}`;
  return (
    <article
      className="rounded-lg border border-border bg-surface p-3 min-w-0 cursor-pointer hover:bg-surface-hover"
      onClick={() => navigate(to)}
      aria-label={`${a.agent_name}, ${a.status}`}
    >
      <div className="flex items-start gap-3 min-w-0">
        <Avatar name={a.agent_name} src={a.avatar || undefined} size={40} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 min-w-0">
            <Link to={to} onClick={(e) => e.stopPropagation()} className="text-base font-medium text-ink-1 truncate hover:underline focus-visible:underline outline-none">{a.agent_name}</Link>
            <HealthDot health={a.health} />
          </div>
          {a.title && <p className="text-xs text-ink-3 truncate">{a.title}</p>}
          <div className="flex items-center gap-1.5 mt-1.5 flex-wrap min-w-0">
            <TypeChip type={a.agent_type} colour={a.colour} />
            <StatusChip status={a.status} />
            <span className="text-xs text-ink-3 truncate min-w-0">Owner: {a.owner_name || a.owner_user}</span>
          </div>
        </div>
      </div>
      <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)] gap-2 mt-3 pt-2 border-t border-border">
        <Stat label="Sent today" value={a.sent_today} />
        <Stat label="Replies today" value={a.replies_today} />
        <Stat label="Waiting for you" value={a.waiting} tone={a.waiting > 0 ? "amber" : undefined} />
      </div>
      {canStop && (
        <div className="flex justify-end mt-2">
          <Button size="sm" variant="danger" onClick={(e) => { e.stopPropagation(); setStopping(true); }} aria-label={`Stop ${a.agent_name}`}><Square />Stop</Button>
        </div>
      )}
      {/* The dialog is portalled, but React still bubbles its clicks through this card. */}
      <div onClick={(e) => e.stopPropagation()}>
        <ReasonDialog
          open={stopping}
          onOpenChange={setStopping}
          title={`Stop ${a.agent_name}`}
          intro="It stops at once: queued work is skipped and approved messages are held. Drafts are kept, and you can resume it later."
          confirmLabel="Stop now"
          noteLabel="Why are you stopping it?"
          danger
          onConfirm={async ({ note }) => {
            try { await stop({ name: a.name, reason: note }); toast.success(`${a.agent_name} is on hold`); onChanged(); return true; }
            catch (e) { toastError(e, "Could not stop the agent"); return false; }
          }}
        />
      </div>
    </article>
  );
}
