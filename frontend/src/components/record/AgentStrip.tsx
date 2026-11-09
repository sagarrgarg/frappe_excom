import { useState } from "react";
import { Link } from "react-router-dom";
import { useSWRConfig } from "swr";
import { Bot, Check, ChevronRight, Hand, Pencil, Trash2, Undo2, UserRound } from "lucide-react";
import { useFrappeGetCall } from "@/lib/api";
import { Button, Chip } from "../primitives";
import { cn } from "../ui/utils";
import type { ExcomThread, UnifiedContact } from "../../types";
import type { Checks, ProposalPayload, ThreadProposal } from "../../types/agents";
import { ProposalDetails, PricedChip, pricedFrom } from "../agents/ProposalDetails";
import { ChecksSummary, M, PayloadEditor, PROPOSAL_LABEL, checksPassed, failingChecks, parseJson, useAgentAction, useAgentNames, when } from "../agents/bits";

const proposalsKey = (thread: string) => `thread-proposals-${thread}`;

function aiThreads(contact: UnifiedContact): ExcomThread[] {
  return (contact.threads ?? []).filter((t) => t.ai_agent);
}

/**
 * Under the conversation header (HLD-007 §15.5): who has this conversation, an agent or a person,
 * and the one button that moves it. "Needs person" stands out: a buyer is waiting.
 */
export function AgentStrip({ contact, onChanged }: { contact: UnifiedContact; onChanged: () => void }) {
  const t = aiThreads(contact)[0];
  const { names } = useAgentNames(Boolean(t));
  const { mutate } = useSWRConfig();
  const takeOver = useAgentAction(M.takeOver);
  const handBack = useAgentAction(M.handBack);
  if (!t?.ai_agent) return null;
  const agent = names[t.ai_agent] || t.ai_agent;
  const mode = t.ai_mode || "";
  const withYou = mode === "Hold" || mode === "Needs person";
  const needs = mode === "Needs person";
  const busy = takeOver.loading || handBack.loading;
  const after = () => { onChanged(); mutate(proposalsKey(t.name)); };
  const agentLink = <Link to={`/agents/${encodeURIComponent(t.ai_agent)}`} className="font-medium hover:underline">{agent}</Link>;
  return (
    <div role="status" className={cn("shrink-0 flex items-center gap-2 px-3 min-h-9 py-1 text-sm border-b border-border min-w-0 flex-wrap",
      needs ? "bg-crayon-rose-tint text-crayon-rose-text" : withYou ? "bg-surface-sunken text-ink-1" : "bg-crayon-violet-tint text-crayon-violet-text")}>
      {withYou ? <UserRound className="size-4 shrink-0" aria-hidden /> : <Bot className="size-4 shrink-0" aria-hidden />}
      <span className="min-w-0 flex-1 truncate">
        {needs ? <>{agentLink} (AI) handed this to you: the buyer is waiting for a person</>
          : withYou ? <>With you · {agentLink} (AI) stays out</>
          : <>Handled by {agentLink} (AI)</>}
      </span>
      {t.ai_intent && <Chip size="sm" accent={needs ? "rose" : "violet"} label={t.ai_intent} title="What the buyer wants, as the agent read it" />}
      {withYou ? (
        <Button size="sm" disabled={busy} onClick={async () => { if (await handBack.run({ thread: t.name }, `Handed back to ${agent}`, "Could not hand it back")) after(); }}>
          <Undo2 />Hand back to {agent}
        </Button>
      ) : (
        <Button size="sm" variant="primary" disabled={busy}
          onClick={async () => {
            if (!window.confirm(`Take over from ${agent}? It stops writing on this conversation, and its drafts waiting here are cancelled.`)) return;
            if (await takeOver.run({ thread: t.name }, "It is yours now", "Could not take over")) after();
          }}>
          <Hand />Take over
        </Button>
      )}
    </div>
  );
}

/** At the foot of the conversation: an agent's open proposals, with Approve, Edit and Discard. */
export function AgentProposals({ contact }: { contact: UnifiedContact }) {
  const threads = aiThreads(contact);
  if (!threads.length) return null;
  return <>{threads.map((t) => <ThreadProposals key={t.name} thread={t.name} />)}</>;
}

function ThreadProposals({ thread }: { thread: string }) {
  const { data, mutate } = useFrappeGetCall<{ message: ThreadProposal[] }>(M.threadProposals, { thread }, proposalsKey(thread), { revalidateOnFocus: true, shouldRetryOnError: false });
  const { names } = useAgentNames();
  const rows = (data?.message ?? []).filter((p) => p.status === "Pending" || p.status === "Shadow");
  if (!rows.length) return null;
  return (
    <section aria-label="The agent's proposals" className="shrink-0 max-h-[45%] overflow-y-auto border-t border-border bg-surface-sunken px-3 py-2 space-y-2">
      {rows.map((p) => <InlineProposal key={p.name} p={p} priceList={pricedFrom(p, data?.message)} agent={names[p.agent] || p.agent} onDone={() => mutate()} />)}
    </section>
  );
}

function InlineProposal({ p, priceList, agent, onDone }: { p: ThreadProposal; priceList: string | null; agent: string; onDone: () => void }) {
  const decide = useAgentAction(M.decide);
  const payload = parseJson<ProposalPayload>(p.payload) || {};
  const checks = parseJson<Checks>(p.checks);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<ProposalPayload>(payload);
  const shadow = p.status === "Shadow";
  const label = PROPOSAL_LABEL[p.action] || p.action;
  const failing = failingChecks(checks).length;
  const passed = checksPassed(checks, p.checker_verdict);
  const run = async (params: Record<string, unknown>, ok: string, fail: string) => { if (await decide.run({ name: p.name, ...params }, ok, fail)) { setEditing(false); onDone(); } };
  const busy = decide.loading;
  return (
    <article className="rounded-lg border border-border bg-surface min-w-0" aria-label={shadow ? `What ${agent} would have said` : `${agent} proposes: ${label}`}>
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open || editing}
        className="w-full text-left flex items-center gap-2 px-3 min-h-9 py-1.5 min-w-0 hover:bg-surface-hover rounded-t-lg">
        <ChevronRight className={cn("size-4 shrink-0 text-ink-3 transition-transform", (open || editing) && "rotate-90")} aria-hidden />
        <Bot className="size-4 shrink-0 text-crayon-violet-text" aria-hidden />
        <span className="text-sm text-ink-1 truncate min-w-0 flex-1">
          {shadow ? <>What {agent} would have said</> : <>{agent} proposes: {label}</>}
          {payload.subject ? <span className="text-ink-3"> · {payload.subject}</span> : null}
        </span>
        <PricedChip priceList={priceList} />
        {!shadow && checks && <Chip size="sm" accent={passed ? "green" : "rose"} label={passed ? "Checks passed" : failing ? `${failing} failed` : "AI check failed"} />}
        {shadow && <Chip size="sm" accent="violet" label="Practice" title="In Shadow nothing is sent" />}
        <span className="hidden tablet:inline text-xs text-ink-3 shrink-0">{when(p.creation)}</span>
      </button>
      {(open || editing) && (
        <div className="px-3 pb-3 space-y-2 min-w-0">
          {editing ? <PayloadEditor value={draft} onChange={setDraft} idPrefix={`inline-${p.name}`} />
            : <ProposalDetails action={p.action} payload={payload} previewHtml={p.preview_html} title={`${agent}: ${label}`} />}
          {!shadow && <ChecksSummary checks={checks} verdict={p.checker_verdict} compact />}
        </div>
      )}
      {!shadow && (
        <div className="flex gap-1.5 flex-wrap px-3 pb-2">
          {editing ? (
            <>
              <Button size="sm" variant="primary" disabled={busy || !String(draft.body ?? "").trim()} onClick={() => run({ decision: "approve", payload: draft }, "Edited and approved", "Could not approve")}><Check />Approve with my changes</Button>
              <Button size="sm" variant="ghost" disabled={busy} onClick={() => { setEditing(false); setDraft(payload); }}>Cancel</Button>
            </>
          ) : (
            <>
              <Button size="sm" variant="primary" disabled={busy} onClick={() => run({ decision: "approve" }, "Approved", "Could not approve")}><Check />Approve</Button>
              {typeof payload.body === "string" && <Button size="sm" disabled={busy} onClick={() => { setDraft(payload); setEditing(true); }}><Pencil />Edit</Button>}
              <Button size="sm" variant="ghost" disabled={busy} onClick={() => run({ decision: "reject", reason: "Not needed" }, "Discarded", "Could not discard")}><Trash2 />Discard</Button>
            </>
          )}
        </div>
      )}
    </article>
  );
}
