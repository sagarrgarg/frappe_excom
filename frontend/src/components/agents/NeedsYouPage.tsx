import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useSWRConfig } from "swr";
import { AlertOctagon, Check, CheckCheck, Clock, Inbox, PauseCircle, Pencil, RefreshCw, UserRound, X } from "lucide-react";
import { toast } from "sonner";
import { useFrappeGetCall } from "@/lib/api";
import { useHotkeys } from "../../lib/hotkeys";
import { PageFrame } from "../shell/PageFrame";
import { Button, Chip, EmptyState, Kbd } from "../primitives";
import { cn } from "../ui/utils";
import { parseError } from "../../lib/errors";
import type { NeedsYouQueue, ProposalPayload, QueueProposal, WaitingBuyer } from "../../types/agents";
import { ProposalDetails, PricedChip, pricedFrom } from "./ProposalDetails";
import { ChecksSummary, M, NEEDS_YOU_COUNT_KEY, PayloadEditor, PROPOSAL_LABEL, REJECT_REASONS, RISK_ACCENT, ReasonDialog, checksPassed, useAgentAction, useOpenThread, when } from "./bits";

/** How long someone has waited, in the unit a person would say. */
function waited(h?: number | null): string {
  if (h == null) return "—";
  if (h < 1) return `${Math.max(1, Math.round(h * 60))} min`;
  if (h < 48) return `${Math.round(h * 10) / 10} h`;
  return `${Math.round(h / 24)} days`;
}

type Group = { key: string; agent: string; action: string; step: number | null; items: QueueProposal[] };

/** Proposals grouped by agent, then by action and sequence step: the unit bulk approval works on. */
function groupProposals(rows: QueueProposal[], names: Record<string, string>): Group[] {
  const map = new Map<string, Group>();
  for (const p of rows) {
    const step = p.step ? Number(p.step) : null;
    const key = `${p.agent}|${p.action}|${step ?? ""}`;
    if (!map.has(key)) map.set(key, { key, agent: p.agent, action: p.action, step, items: [] });
    map.get(key)!.items.push(p);
  }
  return [...map.values()].sort((a, b) =>
    (names[a.agent] || a.agent).localeCompare(names[b.agent] || b.agent) || a.action.localeCompare(b.action) || (a.step ?? 0) - (b.step ?? 0));
}

function groupLabel(g: Group): string {
  const action = PROPOSAL_LABEL[g.action] || g.action;
  return g.step ? (g.action === "send_email" ? `Email step ${g.step}` : `${action}, step ${g.step}`) : action;
}

/** /agents/needs-you — one queue across every agent the person decides for (HLD-007 §15.4). */
export function NeedsYouPage() {
  const { data, error, isLoading, mutate } = useFrappeGetCall<{ message: NeedsYouQueue }>(M.queue, undefined, "needs-you", { refreshInterval: 60_000, revalidateOnFocus: true });
  const q = data?.message;
  const names = q?.agents ?? {};
  const decide = useAgentAction(M.decide);
  const decideMany = useAgentAction(M.decideMany);
  const approveStep = useAgentAction(M.approveStep);
  const openThread = useOpenThread();

  // Decided cards leave at once; the next refresh confirms it.
  const [gone, setGone] = useState<Set<string>>(new Set());
  const proposals = useMemo(() => (q?.proposals ?? []).filter((p) => !gone.has(p.name)), [q, gone]);
  const groups = useMemo(() => groupProposals(proposals, names), [proposals, names]);
  const order = useMemo(() => groups.flatMap((g) => g.items), [groups]);

  const [cursor, setCursor] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState<ProposalPayload>({});
  const [rejecting, setRejecting] = useState<QueueProposal | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const keyed = useRef(false);

  // ?p=<proposal>: opened from elsewhere (a template in review): select it and bring it into view once loaded.
  const [params] = useSearchParams();
  const wanted = params.get("p");
  const found = useRef<string | null>(null);

  const { mutate: mutateKey } = useSWRConfig();
  const reload = () => { mutate(); mutateKey(NEEDS_YOU_COUNT_KEY); };
  const drop = (ids: string[]) => setGone((s) => { const n = new Set(s); ids.forEach((i) => n.add(i)); return n; });
  const moveAfter = (name: string) => {
    const i = order.findIndex((p) => p.name === name);
    const next = order[i + 1] || order[i - 1];
    setCursor(next ? next.name : null);
  };

  const approve = async (p: QueueProposal, payload?: ProposalPayload) => {
    setBusy(p.name);
    const r = await decide.run({ name: p.name, decision: "approve", ...(payload ? { payload } : {}) }, payload ? "Edited and approved" : "Approved", "Could not approve");
    setBusy(null);
    if (r) { moveAfter(p.name); drop([p.name]); setEditing(null); reload(); }
  };
  const startEdit = (p: QueueProposal) => {
    setCursor(p.name);
    setEditing(p.name);
    setDraft({ ...(p.payload || {}), subject: p.payload?.subject ?? "", body: p.payload?.body ?? "" });
  };

  const approveGroup = async (g: Group) => {
    const passing = g.items.filter((p) => p.checks_ok ?? checksPassed(p.checks, p.checker_verdict));
    if (!passing.length) return;
    if (!window.confirm(`Approve ${passing.length} ${passing.length === 1 ? "proposal" : "proposals"} from ${names[g.agent] || g.agent} that passed every check? Messages go out as the agent's schedule and limits allow.`)) return;
    setBusy(g.key);
    // A step is one bulk call on the server, which also counts what it left out. Without a step,
    // the server would approve every step of that action, so only this group's names are sent.
    const r = g.step
      ? await approveStep.run({ agent: g.agent, step: g.step, action: g.action }, "", "Could not approve them")
      : await decideMany.run({ names: passing.map((p) => p.name), decision: "approve" }, "", "Could not approve them");
    setBusy(null);
    if (!r) return;
    const out = r as { done?: string[]; refused?: { name: string; why: string }[]; skipped_failing_checks?: number };
    const skipped = out.skipped_failing_checks ?? g.items.length - passing.length;
    const done = out.done ?? [];
    const parts = [`Approved ${done.length}`];
    if (skipped) parts.push(`${skipped} left for you to read: ${skipped === 1 ? "its" : "their"} checks failed`);
    if (out.refused?.length) parts.push(`${out.refused.length} could not be approved (${out.refused[0].why})`);
    (out.refused?.length ? toast.warning : toast.success)(parts.join(". "));
    drop(done);
    reload();
  };

  // J/K/A/E/R like Harvey's Outbox. Not while typing, and not while a dialog is open.
  const current = order.find((p) => p.name === cursor) || null;
  useEffect(() => { if (cursor && !order.some((p) => p.name === cursor)) setCursor(order[0]?.name ?? null); }, [order, cursor]);
  useEffect(() => {
    if (!wanted || found.current === wanted || !order.some((p) => p.name === wanted)) return;
    found.current = wanted;
    setCursor(wanted);
    requestAnimationFrame(() => document.querySelector(`[data-proposal="${CSS.escape(wanted)}"]`)?.scrollIntoView({ block: "center" }));
  }, [wanted, order]);
  useEffect(() => {
    if (!cursor || !keyed.current) return;
    document.querySelector(`[data-proposal="${CSS.escape(cursor)}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [cursor]);
  const guard = (fn: () => void) => () => { if (document.querySelector("[role=dialog]")) return; keyed.current = true; fn(); };
  const step = (d: number) => {
    if (!order.length) return;
    const i = order.findIndex((p) => p.name === cursor);
    const n = i < 0 ? 0 : Math.max(0, Math.min(order.length - 1, i + d));
    setCursor(order[n].name);
  };
  useHotkeys({
    j: guard(() => step(1)),
    k: guard(() => step(-1)),
    a: guard(() => { if (current && busy !== current.name) approve(current, editing === current.name ? draft : undefined); }),
    e: guard(() => { if (current && editing !== current.name && typeof current.payload?.body === "string") startEdit(current); }),
    r: guard(() => { if (current) setRejecting(current); }),
  }, [order, cursor, current, editing, draft, busy]);

  const waiting = q?.waiting ?? [];
  const held = q?.held ?? [];
  const blockers = q?.blockers ?? [];
  const nothing = q && !waiting.length && !proposals.length && !held.length && !blockers.length;

  return (
    <PageFrame
      title="Needs you"
      icon={<Inbox />}
      actions={<Button size="sm" variant="ghost" onClick={reload} disabled={isLoading}><RefreshCw className={isLoading ? "animate-spin" : ""} />Refresh</Button>}
    >
      {error && !q ? (
        <p role="alert" className="text-sm text-crayon-rose-text">{parseError(error).message}</p>
      ) : !q ? (
        <p className="text-sm text-ink-3">Loading…</p>
      ) : nothing ? (
        <EmptyState icon={<Check />} title="Nothing waits for you" hint="Buyers an agent hands to you, messages to approve, and anything stuck will appear here." />
      ) : (
        <div className="space-y-5 max-w-3xl mx-auto">
          {waiting.length > 0 && (
            <Section title="Buyers waiting for a person" count={waiting.length} hint={`Reply within ${q.reply_target_hours ?? 4} hours.`}>
              <ul className="rounded-lg border border-border divide-y divide-border bg-surface">
                {waiting.map((w) => <li key={w.name}><WaitingRow w={w} agent={names[w.ai_agent] || w.ai_agent} onOpen={() => openThread(w.name)} /></li>)}
              </ul>
            </Section>
          )}

          {proposals.length > 0 && (
            <Section title="To approve" count={proposals.length} hint="Each one exactly as the buyer will see it."
              extra={
                <p className="hidden laptop:flex items-center gap-1.5 text-xs text-ink-3 flex-wrap" aria-label="Keyboard shortcuts">
                  <Kbd>J</Kbd><Kbd>K</Kbd>move<Kbd>A</Kbd>approve<Kbd>E</Kbd>edit<Kbd>R</Kbd>reject
                </p>
              }>
              <div className="space-y-4">
                {groups.map((g) => {
                  const passing = g.items.filter((p) => p.checks_ok ?? checksPassed(p.checks, p.checker_verdict)).length;
                  return (
                    <section key={g.key} aria-label={`${names[g.agent] || g.agent}: ${groupLabel(g)}`} className="space-y-2 min-w-0">
                      <header className="flex items-center gap-2 flex-wrap min-w-0">
                        <h3 className="text-sm font-medium text-ink-1 min-w-0 truncate">
                          <Link to={`/agents/${encodeURIComponent(g.agent)}`} className="hover:underline">{names[g.agent] || g.agent}</Link>
                          <span className="text-ink-3 font-normal"> · {groupLabel(g)} · {g.items.length}</span>
                        </h3>
                        <Button size="sm" className="ml-auto" disabled={!passing || busy === g.key} onClick={() => approveGroup(g)}
                          title={passing < g.items.length ? `${g.items.length - passing} failed a check and stay for you to read` : undefined}>
                          <CheckCheck />{busy === g.key ? "Approving…" : `Approve all ${passing} that passed`}
                        </Button>
                      </header>
                      <ul className="space-y-2">
                        {g.items.map((p) => (
                          <li key={p.name}>
                            <ReviewCard
                              p={p}
                              priceList={pricedFrom(p, q.proposals)}
                              selected={cursor === p.name}
                              onSelect={() => { keyed.current = false; setCursor(p.name); }}
                              editing={editing === p.name}
                              draft={draft}
                              setDraft={setDraft}
                              busy={busy === p.name}
                              onApprove={() => approve(p)}
                              onEdit={() => startEdit(p)}
                              onSaveEdit={() => approve(p, draft)}
                              onCancelEdit={() => setEditing(null)}
                              onReject={() => { setCursor(p.name); setRejecting(p); }}
                            />
                          </li>
                        ))}
                      </ul>
                    </section>
                  );
                })}
              </div>
            </Section>
          )}

          {(held.length > 0 || blockers.length > 0) && (
            <Section title="Held and blocked" count={held.length + blockers.length} hint="These wait on a fix, not a decision.">
              <ul className="rounded-lg border border-border divide-y divide-border bg-surface">
                {held.map((p) => (
                  <li key={p.name} className="flex items-start gap-2 px-3 py-2.5 min-w-0">
                    <PauseCircle className="size-4 mt-0.5 shrink-0 text-crayon-amber-text" aria-hidden />
                    <div className="min-w-0 flex-1">
                      <p className="text-sm text-ink-1 break-words">{PROPOSAL_LABEL[p.action] || p.action}{p.to_email ? ` to ${p.to_email}` : ""}</p>
                      <p className="text-sm text-crayon-amber-text break-words">Held: {p.hold_reason || "no reason given"}</p>
                    </div>
                    <Link to={`/agents/${encodeURIComponent(p.agent)}/needs-you`} className="text-sm text-crayon-blue-text hover:underline shrink-0">{names[p.agent] || p.agent}</Link>
                  </li>
                ))}
                {blockers.map((b) => (
                  <li key={b.name} className="flex items-start gap-2 px-3 py-2.5 min-w-0">
                    <AlertOctagon className="size-4 mt-0.5 shrink-0 text-crayon-rose-text" aria-hidden />
                    <div className="min-w-0 flex-1">
                      <p className="text-sm text-ink-1 break-words"><span className="font-medium text-crayon-rose-text">Blocker:</span> {b.title || b.rule}</p>
                      {b.detail && <p className="text-sm text-ink-2 break-words">{b.detail}</p>}
                    </div>
                    <Link to={`/agents/${encodeURIComponent(b.agent)}/readiness`} className="text-sm text-crayon-blue-text hover:underline shrink-0">{names[b.agent] || b.agent}</Link>
                  </li>
                ))}
              </ul>
            </Section>
          )}
        </div>
      )}

      <ReasonDialog
        open={Boolean(rejecting)}
        onOpenChange={(o) => { if (!o) setRejecting(null); }}
        title="Reject"
        confirmLabel="Reject"
        danger
        choices={REJECT_REASONS}
        noteLabel="Note for the agent"
        noteOptional
        intro="The reason teaches the agent what to do differently."
        onConfirm={async ({ choice, note }) => {
          const p = rejecting;
          if (!p) return true;
          const r = await decide.run({ name: p.name, decision: "reject", reason: choice, note }, "Rejected", "Could not reject");
          if (r) { moveAfter(p.name); drop([p.name]); reload(); }
          return Boolean(r);
        }}
      />
    </PageFrame>
  );
}

function Section({ title, count, hint, extra, children }: { title: string; count: number; hint?: string; extra?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section aria-label={title} className="space-y-2 min-w-0">
      <header className="flex items-baseline gap-2 flex-wrap min-w-0">
        <h2 className="text-md text-ink-1">{title}</h2>
        <span className="text-sm text-ink-3 tabular-nums">{count}</span>
        {hint && <span className="text-xs text-ink-3 min-w-0">{hint}</span>}
        {extra && <div className="ml-auto">{extra}</div>}
      </header>
      {children}
    </section>
  );
}

function WaitingRow({ w, agent, onOpen }: { w: WaitingBuyer; agent: string; onOpen: () => void }) {
  return (
    <button type="button" onClick={onOpen} className="w-full text-left flex items-start gap-3 px-3 py-2.5 min-w-0 hover:bg-surface-hover focus-visible:bg-surface-hover outline-none">
      <UserRound className="size-5 mt-0.5 shrink-0 text-ink-3" aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 min-w-0 flex-wrap">
          <span className="text-sm font-medium text-ink-1 truncate min-w-0">{w.display_name || w.name}</span>
          {w.ai_intent && <Chip size="sm" accent="violet" label={w.ai_intent} />}
        </div>
        <p className="text-xs text-ink-3 truncate">Handed over by {agent}{w.channel ? ` · ${w.channel}` : ""}</p>
        {w.last_message_preview && <p className="text-sm text-ink-2 line-clamp-2 break-words mt-0.5">{w.last_message_preview}</p>}
      </div>
      <span className={cn("shrink-0 inline-flex items-center gap-1 text-sm tabular-nums", w.late ? "text-crayon-rose-text font-medium" : "text-ink-2")}>
        {w.late ? <span aria-hidden className="size-2 rounded-full bg-crayon-rose-base" /> : <Clock className="size-3.5" aria-hidden />}
        {waited(w.waited_hours)}
        {w.late && <span className="sr-only">, late</span>}
      </span>
    </button>
  );
}

function ReviewCard({ p, priceList, selected, onSelect, editing, draft, setDraft, busy, onApprove, onEdit, onSaveEdit, onCancelEdit, onReject }: {
  p: QueueProposal;
  priceList: string | null;
  selected: boolean;
  onSelect: () => void;
  editing: boolean;
  draft: ProposalPayload;
  setDraft: (v: ProposalPayload) => void;
  busy: boolean;
  onApprove: () => void;
  onEdit: () => void;
  onSaveEdit: () => void;
  onCancelEdit: () => void;
  onReject: () => void;
}) {
  const label = PROPOSAL_LABEL[p.action] || p.action;
  const subject = p.payload?.subject;
  const editable = p.payload && typeof p.payload.body === "string";
  return (
    <article
      data-proposal={p.name}
      onClick={onSelect}
      aria-label={`${label}${p.to_email ? ` to ${p.to_email}` : ""}`}
      aria-current={selected || undefined}
      className={cn("rounded-lg border bg-surface min-w-0", selected ? "border-crayon-blue-base ring-1 ring-crayon-blue-base" : "border-border")}
    >
      <header className="flex items-center gap-2 px-3 min-h-10 py-1.5 border-b border-border flex-wrap min-w-0">
        <h4 className="text-sm font-medium text-ink-1 truncate min-w-0">{label}</h4>
        {p.risk && <Chip size="sm" accent={RISK_ACCENT[p.risk] ?? "neutral"} label={`${p.risk} risk`} />}
        <PricedChip priceList={priceList} />
        <span className="text-xs text-ink-3 ml-auto shrink-0">{when(p.creation)}</span>
      </header>
      <div className="p-3 space-y-2 min-w-0">
        <dl className="text-sm space-y-0.5 min-w-0">
          {(p.to_email || p.subject_name) && (
            <div className="flex gap-2 min-w-0"><dt className="w-16 shrink-0 text-ink-3">To</dt><dd className="min-w-0 flex-1 break-all text-ink-1">{p.to_email || `${p.subject_doctype} ${p.subject_name}`}</dd></div>
          )}
          {subject && !editing && (
            <div className="flex gap-2 min-w-0"><dt className="w-16 shrink-0 text-ink-3">Subject</dt><dd className="min-w-0 flex-1 break-words text-ink-1">{subject}</dd></div>
          )}
          {p.send_at && <div className="flex gap-2 min-w-0"><dt className="w-16 shrink-0 text-ink-3">Sends</dt><dd className="min-w-0 flex-1 text-ink-1">{when(p.send_at)}</dd></div>}
        </dl>
        {editing ? (
          <div onClick={(e) => e.stopPropagation()}>
            <PayloadEditor value={draft} onChange={setDraft} idPrefix={`edit-${p.name}`} />
          </div>
        ) : (
          <ProposalDetails action={p.action} payload={p.payload} previewHtml={p.preview_html} title={`Preview: ${label}${p.to_email ? ` to ${p.to_email}` : ""}`} />
        )}
        <ChecksSummary checks={p.checks} verdict={p.checker_verdict} notes={p.checker_notes} />
        <div className="flex gap-1.5 flex-wrap pt-1" onClick={(e) => e.stopPropagation()}>
          {editing ? (
            <>
              <Button variant="primary" size="sm" disabled={busy || !String(draft.body ?? "").trim()} onClick={onSaveEdit}><Check />{busy ? "Approving…" : "Approve with my changes"}</Button>
              <Button variant="ghost" size="sm" disabled={busy} onClick={onCancelEdit}>Cancel</Button>
            </>
          ) : (
            <>
              <Button variant="primary" size="sm" disabled={busy} onClick={onApprove}><Check />{busy ? "Approving…" : "Approve"}</Button>
              {editable && <Button size="sm" disabled={busy} onClick={onEdit}><Pencil />Edit and approve</Button>}
              <Button variant="danger" size="sm" disabled={busy} onClick={onReject}><X />Reject</Button>
            </>
          )}
        </div>
      </div>
    </article>
  );
}
