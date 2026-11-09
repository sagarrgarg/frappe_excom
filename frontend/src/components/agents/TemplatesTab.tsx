import { useState } from "react";
import { Link } from "react-router-dom";
import { ChevronRight, ExternalLink, FileText, Inbox, RotateCcw, Trash2 } from "lucide-react";
import { useFrappeGetCall } from "@/lib/api";
import { Button, Chip, EmptyState, type Accent } from "../primitives";
import { cn } from "../ui/utils";
import { parseError } from "../../lib/errors";
import { deskUrl } from "../../hooks/useRecordLinks";
import { groupByStatus, languageName, needIsFor, needLabel, templateActions } from "../../lib/agents-logic";
import type { TemplateNeed } from "../../types/agents";
import { M, TEMPLATE_NEEDS_KEY, useAgentAction, useAgentNames, when } from "./bits";
import { TemplateWords } from "./ProposalDetails";

/** Meta's template status (WhatsApp Templates.status), in plain words. */
const META_STATUS: Record<string, { label: string; accent: Accent }> = {
  APPROVED: { label: "Approved by Meta", accent: "green" },
  PENDING: { label: "Meta is reviewing", accent: "amber" },
  IN_APPEAL: { label: "Appealed to Meta", accent: "amber" },
  REJECTED: { label: "Rejected by Meta", accent: "rose" },
  PAUSED: { label: "Paused by Meta", accent: "rose" },
  FLAGGED: { label: "Flagged by Meta", accent: "rose" },
  DISABLED: { label: "Disabled by Meta", accent: "rose" },
  PENDING_DELETION: { label: "Being deleted", accent: "neutral" },
};

/** Meta's quality rating, from how people react to the messages. */
const QUALITY: Record<string, { label: string; accent: Accent }> = {
  GREEN: { label: "Quality high", accent: "green" },
  YELLOW: { label: "Quality medium", accent: "amber" },
  RED: { label: "Quality low", accent: "rose" },
};

const STATUS_ACCENT: Record<string, Accent> = {
  "In review": "amber", Failed: "rose", Submitted: "blue", Drafting: "violet", Open: "neutral", Met: "green", Withdrawn: "neutral",
};

const cap = (s?: string | null) => (s ? s.charAt(0).toUpperCase() + s.slice(1).toLowerCase() : "");

const NOTE = "Excom makes templates for what your agents send again and again. WhatsApp templates go to Meta on their own; you are told each time and can withdraw one. Email templates wait for your approval.";

/**
 * The Template Steward's work (HLD-008): every template it made or is making, grouped by status.
 * With `agent`, only the ones that agent uses (as the first to need it, or sharing it).
 */
export function TemplatesTab({ agent }: { agent?: string }) {
  // One list for the Agents home and every agent page; an agent's page filters it here, so a
  // template two agents share shows on both (the server's agent filter matches the first only).
  const { data, error, isLoading, mutate } = useFrappeGetCall<{ message: TemplateNeed[] }>(M.templateNeeds, undefined, TEMPLATE_NEEDS_KEY, { refreshInterval: 60_000, shouldRetryOnError: false });
  const { names } = useAgentNames(!agent);
  const [open, setOpen] = useState<string | null>(null);
  const all = data?.message ?? [];
  const rows = agent ? all.filter((n) => needIsFor(n, agent)) : all;
  const groups = groupByStatus(rows);
  const note = <p className="text-sm text-ink-2 max-w-3xl">{NOTE}</p>;

  if (error) return <div className="space-y-2">{note}<p role="alert" className="text-sm text-crayon-rose-text">{parseError(error).message}</p></div>;
  if (isLoading && !data) return <div className="space-y-2">{note}<p className="text-sm text-ink-3">Loading templates…</p></div>;
  if (rows.length === 0) return (
    <div className="space-y-2">
      {note}
      <EmptyState icon={<FileText />} title="No templates yet" hint={agent ? "When this agent needs a template for a message it sends often, it shows here." : "When an agent needs a template for a message it sends often, it shows here."} />
    </div>
  );
  return (
    <div className="space-y-3 max-w-4xl">
      {note}
      {groups.map((g) => (
        <section key={g.status} aria-labelledby={`tpl-${g.status.replace(/\W+/g, "-")}`} className="min-w-0">
          <header className="flex items-baseline gap-2 mb-1 min-w-0 flex-wrap">
            <h2 id={`tpl-${g.status.replace(/\W+/g, "-")}`} className="text-sm font-medium text-ink-1">{g.label}</h2>
            <span className="text-xs text-ink-3 tabular-nums">{g.items.length}</span>
            {g.hint && <span className="text-xs text-ink-3 min-w-0 basis-full tablet:basis-auto">{g.hint}</span>}
          </header>
          <ul className="rounded-lg border border-border divide-y divide-border bg-surface">
            {g.items.map((n) => (
              <li key={n.name} className="min-w-0">
                <NeedRow n={n} agentName={agent ? null : names[n.agent] || n.agent} open={open === n.name} onToggle={() => setOpen(open === n.name ? null : n.name)} reload={() => mutate()} />
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

function NeedRow({ n, agentName, open, onToggle, reload }: { n: TemplateNeed; agentName: string | null; open: boolean; onToggle: () => void; reload: () => void }) {
  const label = needLabel(n.need_key) || n.name;
  const meta = n.meta_status ? META_STATUS[n.meta_status.toUpperCase()] ?? { label: cap(n.meta_status.replace(/_/g, " ")), accent: "neutral" as Accent } : null;
  const quality = n.quality ? QUALITY[n.quality.toUpperCase()] : null;
  const id = `tpl-${n.name.replace(/\W+/g, "-")}`;
  return (
    <>
      <button type="button" onClick={onToggle} aria-expanded={open} aria-controls={id}
        className="w-full text-left flex items-center gap-2 px-3 min-h-12 py-2 min-w-0 hover:bg-surface-hover outline-none focus-visible:bg-surface-hover">
        <ChevronRight aria-hidden className={cn("size-4 shrink-0 text-ink-3 transition-transform", open && "rotate-90")} />
        <span className="min-w-0 flex-1">
          <span className="block text-sm text-ink-1 truncate">{label}</span>
          {agentName && <span className="block text-xs text-ink-3 truncate">For {agentName}{n.agents && n.agents.includes(",") ? " and others" : ""}</span>}
        </span>
        <span className="flex items-center gap-1 flex-wrap justify-end min-w-0 max-w-[60%]">
          <Chip size="sm" accent={n.channel === "WhatsApp" ? "green" : "blue"} label={n.channel} />
          {n.language && <Chip size="sm" label={languageName(n.language)} title={n.language} />}
          {n.category && <Chip size="sm" label={cap(n.category)} title="Meta's category: utility is about a transaction, marketing is promotion" />}
          {meta && <Chip size="sm" accent={meta.accent} label={meta.label} />}
          {quality && <Chip size="sm" accent={quality.accent} label={quality.label} title="Meta's rating, from how people react to these messages" />}
        </span>
      </button>
      {open && <NeedDetails id={id} n={n} reload={reload} />}
    </>
  );
}

function Line({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-3 py-0.5 min-w-0 text-sm">
      <dt className="w-28 shrink-0 text-ink-3">{label}</dt>
      <dd className="min-w-0 flex-1 break-words text-ink-1">{children}</dd>
    </div>
  );
}

function NeedDetails({ id, n, reload }: { id: string; n: TemplateNeed; reload: () => void }) {
  const withdraw = useAgentAction(M.withdrawTemplate);
  const reopen = useAgentAction(M.reopenTemplate);
  const can = templateActions(n);
  const busy = withdraw.loading || reopen.loading;
  const problem = n.rejection_reason && n.rejection_reason.toUpperCase() !== "NONE" ? `Meta: ${n.rejection_reason}` : n.last_error;
  const moved = n.meta_category && n.category && n.meta_category.toUpperCase() !== n.category.toUpperCase();
  const source = n.source === "Library" ? "Meta's template library" : n.source === "Written" ? "Written by Excom" : "—";
  const confirmWithdraw = n.channel === "WhatsApp"
    ? "Withdraw this template? It is deleted on WhatsApp (Meta) and your agents stop using it. Messages already sent are not affected. You can try again later; Meta then reviews a new one."
    : "Withdraw this email template? Your agents stop using it. You can try again later.";
  return (
    <div id={id} className="px-3 pb-3 pt-1 pl-9 space-y-3 min-w-0">
      <div className="rounded-md border border-border bg-surface-sunken p-2.5 min-w-0">
        <TemplateWords subject={n.subject} body={n.body} />
      </div>
      {n.back_translation && (
        <div className="min-w-0">
          <h3 className="text-xs text-ink-3 mb-0.5">In English</h3>
          <p className="text-sm text-ink-2 whitespace-pre-wrap break-words">{n.back_translation}</p>
        </div>
      )}
      {problem && <p role="status" className="text-sm text-crayon-rose-text break-words">{n.status === "Failed" ? "Why it failed: " : ""}{problem}</p>}
      {moved && <p className="text-sm text-crayon-amber-text break-words">Meta put it under {cap(n.meta_category)}, not {cap(n.category)}: agents do not use it.</p>}
      <dl className="min-w-0">
        <Line label="Where from">{source}</Line>
        {n.channel_account && <Line label="Account">{n.channel_account}</Line>}
        {Number(n.attempts) > 0 && <Line label="Tries">{n.attempts}</Line>}
        {n.submitted_on && <Line label="Sent to Meta">{when(n.submitted_on)}</Line>}
        {n.met_on && <Line label="Ready since">{when(n.met_on)}</Line>}
        {n.template && (
          <Line label="Template">
            <a href={deskUrl("WhatsApp Templates", n.template)} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-crayon-blue-text hover:underline max-w-full"><span className="truncate">{n.template}</span><ExternalLink className="size-3 shrink-0" /></a>
          </Line>
        )}
      </dl>
      {(can.withdraw || can.tryAgain || can.review) && (
        <div className="flex gap-1.5 flex-wrap">
          {can.review && (
            <Button asChild size="sm" variant="primary"><Link to={`/agents/needs-you?p=${encodeURIComponent(n.proposal!)}`}><Inbox />Open review</Link></Button>
          )}
          {can.tryAgain && (
            <Button size="sm" variant="primary" disabled={busy} onClick={async () => { if (await reopen.run({ need: n.name }, "Excom will try again on its next hourly run", "Could not try again")) reload(); }}><RotateCcw />Try again</Button>
          )}
          {can.withdraw && (
            <Button size="sm" variant="danger" disabled={busy} onClick={async () => { if (window.confirm(confirmWithdraw) && await withdraw.run({ need: n.name }, "Withdrawn", "Could not withdraw it")) reload(); }}><Trash2 />{withdraw.loading ? "Withdrawing…" : "Withdraw"}</Button>
          )}
        </div>
      )}
    </div>
  );
}
