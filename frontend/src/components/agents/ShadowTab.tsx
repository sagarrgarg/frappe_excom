import { useState } from "react";
import { Eye, MessageSquare } from "lucide-react";
import { useFrappeGetCall } from "@/lib/api";
import { Button, EmptyState, Textarea } from "../primitives";
import { cn } from "../ui/utils";
import { parseError } from "../../lib/errors";
import type { AgentDetail, ShadowRow, ShadowScores, ShadowVerdict } from "../../types/agents";
import { HtmlPreview, M, Panel, PROPOSAL_LABEL, Stat, useAgentAction, useOpenThread, when } from "./bits";

const VERDICTS: { value: ShadowVerdict; label: string; active: string }[] = [
  { value: "Agent better", label: "Agent better", active: "bg-crayon-green-tint text-crayon-green-text border-crayon-green-base" },
  { value: "About the same", label: "About the same", active: "bg-crayon-blue-tint text-crayon-blue-text border-crayon-blue-base" },
  { value: "Person better", label: "Person better", active: "bg-crayon-rose-tint text-crayon-rose-text border-crayon-rose-base" },
];

/**
 * The Shadow comparison (HLD-007 §15.4): what the agent would have sent, next to what a person sent
 * on the same conversation. The owner's verdicts are the evidence for letting it go Live.
 */
export function ShadowTab({ d }: { d: AgentDetail }) {
  const name = d.agent.name;
  const { data, error, isLoading, mutate } = useFrappeGetCall<{ message: ShadowRow[] }>(M.shadowComparison, { agent: name }, `shadow-${name}`, { revalidateOnFocus: false });
  // Every verdict so far, not just the drafts loaded below.
  const { data: scored, mutate: rescore } = useFrappeGetCall<{ message: ShadowScores }>(M.shadowScores, { agent: name }, `shadow-scores-${name}`, { revalidateOnFocus: false, shouldRetryOnError: false });
  const rows = data?.message ?? [];
  if (error) return <p role="alert" className="text-sm text-crayon-rose-text">{parseError(error).message}</p>;
  if (isLoading && !data) return <p className="text-sm text-ink-3">Loading…</p>;
  if (!rows.length) return <EmptyState icon={<Eye />} title="No practice drafts yet" hint="In Shadow the agent drafts what it would send, and nothing leaves. Its drafts appear here next to what a person sent." />;

  const local = { "Agent better": 0, "About the same": 0, "Person better": 0 } as Record<ShadowVerdict, number>;
  rows.forEach((r) => { if (r.shadow_verdict) local[r.shadow_verdict as ShadowVerdict] += 1; });
  const s = scored?.message;
  const counts = s ?? { ...local, rated: local["Agent better"] + local["About the same"] + local["Person better"] };
  const unjudged = rows.filter((r) => !r.shadow_verdict).length;
  const share = (v: ShadowVerdict) => (counts.rated ? ` (${Math.round((100 * counts[v]) / counts.rated)}%)` : "");
  return (
    <div className="space-y-2 max-w-5xl">
      <Panel title={s ? `Your verdicts so far: ${counts.rated} judged` : `Your verdicts on the last ${rows.length} ${rows.length === 1 ? "draft" : "drafts"}`}>
        <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] tablet:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)] gap-2">
          <Stat label={`Agent better${share("Agent better")}`} value={counts["Agent better"]} />
          <Stat label={`About the same${share("About the same")}`} value={counts["About the same"]} />
          <Stat label={`Person better${share("Person better")}`} value={counts["Person better"]} tone={counts["Person better"] ? "rose" : undefined} />
          <Stat label={`Not judged, of the ${rows.length} below`} value={unjudged} tone={unjudged ? "amber" : undefined} />
        </div>
        <p className="text-xs text-ink-3 mt-2">Judge a few drafts before putting the agent live: if the person is usually better, fix the brief or the playbook first.</p>
      </Panel>
      <ul className="space-y-2">{rows.map((r) => <li key={r.name}><ShadowCard r={r} onSaved={() => { mutate(); rescore(); }} /></li>)}</ul>
    </div>
  );
}

function ShadowCard({ r, onSaved }: { r: ShadowRow; onSaved: () => void }) {
  const rate = useAgentAction(M.rateShadow);
  const openThread = useOpenThread();
  const [note, setNote] = useState(r.shadow_note || "");
  const [verdict, setVerdict] = useState<ShadowVerdict | "">((r.shadow_verdict as ShadowVerdict) || "");
  const save = async (v: ShadowVerdict) => {
    const ok = await rate.run({ proposal: r.name, verdict: v, note }, "Saved", "Could not save your verdict");
    if (ok) { setVerdict(v); onSaved(); }
  };
  const human = r.person_sent;
  return (
    <article className="rounded-lg border border-border bg-surface min-w-0" aria-label={`Draft to ${r.to_email || "a buyer"}`}>
      <header className="flex items-center gap-2 px-3 min-h-10 py-1.5 border-b border-border flex-wrap min-w-0">
        <h3 className="text-sm font-medium text-ink-1 truncate min-w-0">{PROPOSAL_LABEL[r.action] || r.action}{r.to_email ? ` to ${r.to_email}` : ""}</h3>
        <span className="text-xs text-ink-3 shrink-0">{when(r.creation)}</span>
        {r.thread && <Button size="sm" variant="link" className="ml-auto" onClick={() => openThread(r.thread!)}><MessageSquare />Open the conversation</Button>}
      </header>
      <div className="p-3 grid grid-cols-1 laptop:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-3 min-w-0">
        <div className="min-w-0 space-y-1">
          <h4 className="text-xs text-ink-3">What the agent would have sent</h4>
          {r.payload?.subject && <p className="text-sm text-ink-1 break-words"><span className="text-ink-3">Subject:</span> {r.payload.subject}</p>}
          {r.preview_html ? <HtmlPreview html={r.preview_html} title={`Agent draft to ${r.to_email || "the buyer"}`} />
            : <p className="text-sm text-ink-1 whitespace-pre-wrap break-words">{r.payload?.body || "No preview."}</p>}
        </div>
        <div className="min-w-0 space-y-1">
          <h4 className="text-xs text-ink-3">What a person sent{human?.created_by_user ? ` (${human.created_by_user}, ${when(human.creation)})` : ""}</h4>
          {human ? (
            <p className="text-sm text-ink-1 whitespace-pre-wrap break-words rounded-md border border-border p-2.5">{human.content_text || "(no text)"}</p>
          ) : (
            <p className="text-sm text-ink-3 rounded-md border border-dashed border-border p-2.5">Nobody has replied yet.</p>
          )}
        </div>
      </div>
      <div className="px-3 pb-3 space-y-2 min-w-0">
        <label htmlFor={`shadow-note-${r.name}`} className="sr-only">Note (optional)</label>
        <Textarea id={`shadow-note-${r.name}`} rows={2} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note (optional): what was better, and why" />
        <div className="flex gap-1.5 flex-wrap" role="group" aria-label="Which was better?">
          {VERDICTS.map((v) => (
            <button key={v.value} type="button" aria-pressed={verdict === v.value} disabled={rate.loading} onClick={() => save(v.value)}
              className={cn("h-8 px-3 rounded-md border text-sm font-medium disabled:opacity-50", verdict === v.value ? v.active : "border-border-strong text-ink-1 hover:bg-surface-hover")}>
              {v.label}
            </button>
          ))}
        </div>
      </div>
    </article>
  );
}
