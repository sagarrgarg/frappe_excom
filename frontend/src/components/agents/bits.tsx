import { useEffect, useMemo, useRef, useState } from "react";
import { useFrappePostCall } from "frappe-react-sdk";
import { toast } from "sonner";
import { toastError } from "../ErrorDialog";
import { useNavigate } from "react-router-dom";
import { AlertOctagon, AlertTriangle, ChevronRight, CircleCheck, CircleX, ExternalLink, Info, Sparkles } from "lucide-react";
import { useFrappeGetCall } from "@/lib/api";
import { Button, Chip, Field, Input, Modal, Select, Textarea, type Accent } from "../primitives";
import { cn } from "../ui/utils";
import { deskUrl } from "../../hooks/useRecordLinks";
import { parseFrappeDateTime, formatServerShortDateTime } from "../../utils/datetime";
import type { AgentCard, AgentStatus, CheckerVerdict, Checks, CheckResult, Fact, FactLevel, Health, KnowledgeSection, ProposalPayload, ReadinessState } from "../../types/agents";

/** Whitelisted methods (HLD-007 §15). One list, so a renamed endpoint is one edit. */
const B = "excom.excom_ai.builder.api.";
const P = "excom.excom_ai.api.";
const N = "excom.excom_ai.needs_you.";
export const M = {
  types: `${B}types`,
  resolve: `${B}resolve_keywords`,
  confirmGlossary: `${B}confirm_glossary`,
  preview: `${B}preview`,
  create: `${B}create_agent`,
  agents: `${B}agents`,
  agent: `${B}agent`,
  saveBrief: `${B}save_brief`,
  setPeople: `${B}set_people`,
  setLimits: `${B}set_limits`,
  setPolicy: `${B}set_policy`,
  setStatus: `${B}set_status`,
  refresh: `${B}refresh`,
  runReadiness: `${B}run_readiness`,
  redraft: `${B}redraft`,
  gapTodo: `${B}gap_todo`,
  waiveGap: `${B}waive_gap`,
  stop: `${P}stop_agent`,
  resume: `${P}resume_agent`,
  acknowledge: `${P}acknowledge`,
  decide: `${P}decide_proposal`,
  selfTest: `${P}run_self_test`,
  approveBrief: "excom.excom_ai.doctype.excom_ai_agent.excom_ai_agent.approve_brief",
  promotePerson: `${P}promote_person`,
  researchSummary: `${P}research_summary`,
  importVolza: `${P}import_volza`,
  live: `${P}live`,
  applyPromotion: `${P}apply_promotion`,
  handover: `${P}handover`,
  dryRun: `${P}dry_run`,
  researchPeople: `${P}research_people`,
  requestDraft: `${P}request_draft`,
  queue: `${N}queue`,
  decideMany: `${N}decide_many`,
  approveStep: `${N}approve_step`,
  takeOver: `${N}take_over`,
  handBack: `${N}hand_back`,
  threadProposals: `${N}thread_proposals`,
  shadowComparison: `${N}shadow_comparison`,
  rateShadow: `${N}rate_shadow`,
  count: `${N}count`,
  shadowScores: `${N}shadow_scores`,
  templateNeeds: `${P}template_needs`,
  withdrawTemplate: `${P}withdraw_template`,
  reopenTemplate: `${P}reopen_template_need`,
} as const;

/** SWR keys shared by more than one screen, so they read one cached copy. */
export const liveKey = (name: string) => `agent-live-${name}`;
export const NEEDS_YOU_COUNT_KEY = "needs-you-count";
export const TEMPLATE_NEEDS_KEY = "template-needs";

/* ─── types, statuses, health ─── */

/** The seeded types each have a crayon; a type a business added shows its own colour as a dot. */
const TYPE_ACCENT: Record<string, Accent> = { Sales: "blue", Purchase: "green", Accounts: "amber", Compliance: "rose", Custom: "violet" };

export function TypeChip({ type, colour, size = "sm" }: { type: string; colour?: string | null; size?: "sm" | "md" }) {
  const accent = TYPE_ACCENT[type];
  return <Chip size={size} accent={accent ?? "neutral"} dotColor={accent ? undefined : colour || undefined} label={type} />;
}

export const STATUS_ACCENT: Record<AgentStatus, Accent> = {
  Draft: "neutral", Preparing: "sand", Shadow: "violet", Live: "green", "On Hold": "rose", Retired: "neutral",
};

/** What each status means, for someone who has not read the design. */
export const STATUS_HINT: Record<AgentStatus, string> = {
  Draft: "Being set up. It does nothing yet.",
  Preparing: "Getting ready. Nothing leaves yet.",
  Shadow: "Practising: it prepares and drafts, but nothing is sent.",
  Live: "Working: it sends what its autonomy settings allow.",
  "On Hold": "Stopped. Queued work was skipped, drafts are kept.",
  Retired: "No longer used.",
};

export function StatusChip({ status, size = "sm" }: { status: AgentStatus; size?: "sm" | "md" }) {
  return <Chip size={size} accent={STATUS_ACCENT[status] ?? "neutral"} label={status} title={STATUS_HINT[status]} />;
}

const HEALTH: Record<Health, { cls: string; label: string }> = {
  green: { cls: "bg-crayon-green-base", label: "Healthy" },
  amber: { cls: "bg-crayon-amber-base", label: "Has warnings or has not been checked" },
  red: { cls: "bg-crayon-rose-base", label: "Needs attention: on hold, failing or blocked" },
};

export function HealthDot({ health }: { health: Health }) {
  const h = HEALTH[health] ?? HEALTH.amber;
  return (
    <span className="inline-flex items-center shrink-0" title={h.label}>
      <span aria-hidden className={cn("size-2.5 rounded-full", h.cls)} />
      <span className="sr-only">Health: {h.label}</span>
    </span>
  );
}

export const READINESS_ACCENT: Record<ReadinessState, Accent> = { "Not checked": "neutral", Blocked: "rose", Warnings: "amber", Ready: "green" };
const READINESS_LABEL: Record<ReadinessState, string> = { "Not checked": "Not checked", Blocked: "Blocked", Warnings: "Ready, with warnings", Ready: "Ready" };

export function ReadinessChip({ state, size = "sm" }: { state?: ReadinessState | null; size?: "sm" | "md" }) {
  const s = (state || "Not checked") as ReadinessState;
  return <Chip size={size} accent={READINESS_ACCENT[s] ?? "neutral"} label={READINESS_LABEL[s] ?? s} />;
}

/* ─── plain words for the server's codes ─── */

/** Policy actions (Excom AI Policy.action). */
export const POLICY_LABEL: Record<string, { label: string; hint: string }> = {
  create_record: { label: "Create records", hint: "Leads and contacts it finds" },
  write_draft: { label: "Write drafts", hint: "Drafts kept for a person to send" },
  first_email: { label: "First email", hint: "The first message to someone new" },
  follow_up_email: { label: "Follow-up emails", hint: "Later steps of a sequence" },
  reply: { label: "Replies", hint: "Answers to people who wrote in" },
  whatsapp_template: { label: "WhatsApp templates", hint: "Approved templates to opted-in contacts" },
  prices_and_terms: { label: "Prices and terms", hint: "Any message that names a price or a term" },
  create_opportunity: { label: "Create opportunities", hint: "When a buyer is interested" },
  quotation: { label: "Quotations", hint: "Draft quotations" },
  submit_document: { label: "Submit documents", hint: "Never done by an agent" },
  payment_reminder: { label: "Payment reminders", hint: "Reminders about money owed" },
  todo_note: { label: "ToDos and notes", hint: "Notes and reminders for people" },
};

export const MODE_HINT: Record<string, string> = {
  Off: "Not done",
  Shadow: "Prepared, nothing leaves",
  Review: "A person approves each one",
  Auto: "Done on its own, a sample checked after",
};

/** Proposal actions (Excom AI Proposal.action). */
export const PROPOSAL_LABEL: Record<string, string> = {
  send_email: "Send an email",
  send_whatsapp: "Send a WhatsApp message",
  reply: "Reply",
  create_lead: "Create a lead",
  update_lead: "Classify a lead",
  update_playbook: "Add to the playbook",
  create_opportunity: "Create an opportunity",
  create_quotation: "Draft a quotation",
  payment_reminder: "Payment reminder",
  create_todo: "Create a ToDo",
  note: "Add a note",
  approve_template: "Approve an email template",
};

export const RISK_ACCENT: Record<string, Accent> = { Low: "green", Medium: "amber", High: "rose" };

/** Knowledge sections say what may reach the AI. */
export const PRIVACY_LABEL: Record<string, string> = {
  aggregates: "AI sees totals only",
  names: "AI sees names and totals",
  contacts: "AI sees contact details",
  none: "Never sent to the AI",
};

/* ─── time and money ─── */

export function when(ts?: string | null): string {
  if (!ts) return "—";
  try { return formatServerShortDateTime(parseFrappeDateTime(ts)); } catch { return ts; }
}

export function cost(v?: number | null): string {
  const n = Number(v || 0);
  return n.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: n && n < 1 ? 4 : 2 });
}

/** A link a readiness rule or a fact points at: inside the app when it can be, Desk otherwise. */
export function appPath(route: string): string | null {
  if (!route) return null;
  if (route === "/excom" || route.startsWith("/excom/")) return route.slice("/excom".length) || "/inbox";
  return null;
}

/** Spend against a budget, as a bar that turns amber at 70% and red at 90%. */
export function Meter({ label, used, budget }: { label: string; used: number; budget?: number | null }) {
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

/* ─── facts ─── */

const LEVEL: Record<FactLevel, { icon: React.ReactNode; cls: string; label: string }> = {
  blocker: { icon: <AlertOctagon />, cls: "border-crayon-rose-base bg-crayon-rose-tint", label: "Blocker" },
  warning: { icon: <AlertTriangle />, cls: "border-crayon-amber-base bg-crayon-amber-tint", label: "Warning" },
  info: { icon: <Info />, cls: "border-transparent", label: "" },
};
const LEVEL_TEXT: Record<FactLevel, string> = { blocker: "text-crayon-rose-text", warning: "text-crayon-amber-text", info: "text-ink-3" };

/** Doctypes whose records the builder can leave out (the recipes honour these). */
export const EXCLUDABLE = new Set(["Customer", "Item"]);

/**
 * One fact: the line, its level, where it comes from, and the records behind it.
 * With `onExclude`, records of a doctype the builder can leave out get a tick box.
 */
export function FactLine({ fact, onExclude }: { fact: Fact; onExclude?: (doctype: string, name: string) => void }) {
  const level = (fact.level || "info") as FactLevel;
  const l = LEVEL[level] ?? LEVEL.info;
  const names = fact.names ?? [];
  const canExclude = Boolean(onExclude && fact.doctype && EXCLUDABLE.has(fact.doctype));
  return (
    <li className={cn("border-l-[3px] pl-2 pr-2 py-1.5 rounded-r-md min-w-0", l.cls)}>
      <div className="flex items-start gap-1.5 min-w-0">
        <span className={cn("mt-0.5 shrink-0 [&_svg]:size-3.5", LEVEL_TEXT[level])} aria-hidden>{l.icon}</span>
        <div className="min-w-0 flex-1">
          <p className="text-sm text-ink-1 break-words">
            {l.label && <span className={cn("font-medium mr-1", LEVEL_TEXT[level])}>{l.label}:</span>}
            {fact.text}
          </p>
          {fact.source && <p className="text-xs text-ink-3 break-words mt-0.5">From {fact.source}</p>}
          {names.length > 0 && (
            <details className="mt-1 group">
              <summary className="text-xs text-crayon-blue-text cursor-pointer select-none inline-flex items-center gap-0.5 list-none [&::-webkit-details-marker]:hidden">
                <ChevronRight className="size-3.5 transition-transform group-open:rotate-90" />
                {`Show ${names.length >= 25 ? "the first 25" : `the ${names.length}`} ${names.length === 1 ? "record" : "records"}`}{canExclude ? ", untick to leave one out" : ""}
              </summary>
              <ul className="mt-1 flex flex-wrap gap-1">
                {names.map((n) => (
                  <li key={n} className="min-w-0 max-w-full">
                    {canExclude ? (
                      <label className="inline-flex items-center gap-1 h-6 px-2 rounded-full bg-surface-sunken text-xs text-ink-2 max-w-full cursor-pointer hover:bg-surface-hover">
                        <input type="checkbox" checked onChange={() => onExclude!(fact.doctype!, n)} className="size-3.5 shrink-0" aria-label={`Leave out ${fact.doctype} ${n}`} />
                        <span className="truncate">{n}</span>
                      </label>
                    ) : fact.doctype ? (
                      <a href={deskUrl(fact.doctype, n)} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 h-6 px-2 rounded-full bg-surface-sunken text-xs text-ink-2 max-w-full hover:bg-surface-hover">
                        <span className="truncate">{n}</span><ExternalLink className="size-3 shrink-0" />
                      </a>
                    ) : (
                      <span className="inline-flex items-center h-6 px-2 rounded-full bg-surface-sunken text-xs text-ink-2 max-w-full"><span className="truncate">{n}</span></span>
                    )}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      </div>
    </li>
  );
}

/** One knowledge section as a panel: title, what the AI may see of it, its facts. */
export function SectionPanel({ section, onExclude }: { section: KnowledgeSection; onExclude?: (doctype: string, name: string) => void }) {
  const worst = section.facts.some((f) => f.level === "blocker") ? "rose" : section.facts.some((f) => f.level === "warning") ? "amber" : null;
  return (
    <section className="rounded-lg border border-border bg-surface min-w-0" aria-label={section.title}>
      <header className="flex items-center gap-2 px-3 h-10 border-b border-border min-w-0">
        <h3 className="text-sm font-medium text-ink-1 truncate flex-1 min-w-0">{section.title}</h3>
        {worst && <span aria-hidden className={cn("size-2 rounded-full shrink-0", worst === "rose" ? "bg-crayon-rose-base" : "bg-crayon-amber-base")} />}
        {section.privacy && PRIVACY_LABEL[section.privacy] && <span className="text-xs text-ink-3 truncate shrink-0 max-w-[50%]">{PRIVACY_LABEL[section.privacy]}</span>}
      </header>
      {section.facts.length === 0 ? (
        <p className="px-3 py-2 text-sm text-ink-3">Nothing found.</p>
      ) : (
        <ul className="p-2 space-y-1">{section.facts.map((f, i) => <FactLine key={i} fact={f} onExclude={onExclude} />)}</ul>
      )}
    </section>
  );
}

/* ─── dialogs ─── */

/**
 * Asks why before doing something that needs a reason (stop, waive, reject). Optional choices
 * come first as a select; the note is required unless `noteOptional`.
 */
export function ReasonDialog({
  open, onOpenChange, title, intro, confirmLabel, choices, noteLabel = "Reason", noteOptional, danger, onConfirm,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  title: string;
  intro?: React.ReactNode;
  confirmLabel: string;
  choices?: string[];
  noteLabel?: string;
  noteOptional?: boolean;
  danger?: boolean;
  onConfirm: (v: { choice: string; note: string }) => Promise<boolean | void>;
}) {
  const [choice, setChoice] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (open) { setChoice(""); setNote(""); setBusy(false); } }, [open]);
  const ok = (!choices || Boolean(choice)) && (noteOptional || note.trim().length > 0);
  const submit = async () => {
    if (!ok || busy) return;
    setBusy(true);
    try { const r = await onConfirm({ choice, note: note.trim() }); if (r !== false) onOpenChange(false); } finally { setBusy(false); }
  };
  return (
    <Modal open={open} onOpenChange={onOpenChange} title={title}
      footer={<><Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button><Button variant={danger ? "danger" : "primary"} disabled={!ok || busy} onClick={submit}>{busy ? "Working…" : confirmLabel}</Button></>}>
      <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        {intro && <div className="text-sm text-ink-2">{intro}</div>}
        {choices && (
          <Field label="Why" required htmlFor="reason-choice">
            <Select id="reason-choice" value={choice} onChange={(e) => setChoice(e.target.value)}>
              <option value="">Choose…</option>
              {choices.map((c) => <option key={c} value={c}>{c}</option>)}
            </Select>
          </Field>
        )}
        <Field label={noteLabel} required={!noteOptional} htmlFor="reason-note">
          <Textarea id="reason-note" value={note} onChange={(e) => setNote(e.target.value)} rows={3} autoFocus={!choices} />
        </Field>
      </form>
    </Modal>
  );
}

/* ─── HTML previews ─── */

/**
 * Drops what could run or reach out from a preview: scripts, frames, forms, event handlers and
 * javascript: links. The iframe it goes into also has no script permission; this is the second lock.
 */
export function sanitizeHtml(html: string): string {
  const doc = new DOMParser().parseFromString(html || "", "text/html");
  doc.querySelectorAll("script, iframe, object, embed, form, link, meta, base").forEach((n) => n.remove());
  doc.querySelectorAll("*").forEach((el) => {
    for (const a of Array.from(el.attributes)) {
      const name = a.name.toLowerCase();
      const v = a.value.trim().toLowerCase();
      if (name.startsWith("on") || ((name === "href" || name === "src" || name === "xlink:href") && (v.startsWith("javascript:") || v.startsWith("data:text")))) {
        el.removeAttribute(a.name);
      }
    }
  });
  return doc.body.innerHTML;
}

/** A proposal's preview, as the recipient will see it: sanitised, in a sandbox with no scripts. */
export function HtmlPreview({ html, title = "Preview" }: { html: string; title?: string }) {
  const ref = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(120);
  const clean = useMemo(() => sanitizeHtml(html), [html]);
  const tok = (n: string) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const srcdoc = `<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>html,body{margin:0;padding:10px;background:${tok("--ex-surface")};color:${tok("--ex-ink-1")};font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;font-size:14px;line-height:1.55;word-break:break-word}
img{max-width:100%;height:auto}a{color:${tok("--ex-blue-text")}}table{border-collapse:collapse;max-width:100%}td,th{padding:4px 8px}pre{white-space:pre-wrap}</style></head><body>${clean}</body></html>`;
  return (
    <iframe
      ref={ref}
      srcDoc={srcdoc}
      sandbox="allow-same-origin"
      title={title}
      onLoad={() => { const h = ref.current?.contentDocument?.body?.scrollHeight; if (h) setHeight(Math.min(h + 20, 900)); }}
      className="w-full border border-border rounded-md bg-surface"
      style={{ height }}
    />
  );
}

/** A labelled figure on a card. */
export function Stat({ label, value, hint, tone }: { label: string; value: React.ReactNode; hint?: string; tone?: "amber" | "rose" }) {
  return (
    <div className="min-w-0" title={hint}>
      <div className={cn("text-lg tabular-nums truncate", tone === "amber" ? "text-crayon-amber-text" : tone === "rose" ? "text-crayon-rose-text" : "text-ink-1")}>{value}</div>
      <div className="text-xs text-ink-3 truncate">{label}</div>
    </div>
  );
}

/** A bordered block with a small heading, the agent page's unit of layout. */
export function Panel({ title, actions, children, className }: { title?: React.ReactNode; actions?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <section className={cn("rounded-lg border border-border bg-surface min-w-0", className)}>
      {(title || actions) && (
        <header className="flex items-center gap-2 px-3 min-h-10 py-1.5 border-b border-border min-w-0 flex-wrap">
          {title && <h2 className="text-sm font-medium text-ink-1 truncate flex-1 min-w-0">{title}</h2>}
          {actions && <div className="flex items-center gap-1.5 flex-wrap min-w-0">{actions}</div>}
        </header>
      )}
      <div className="p-3 min-w-0">{children}</div>
    </section>
  );
}

/** A POST that reports itself: a toast on success, the server's own words on failure. */
export function useAgentAction(method: string) {
  const { call, loading } = useFrappePostCall(method);
  const run = async (params: Record<string, unknown>, ok: string, fail: string) => {
    try { const r = await call(params); if (ok) toast.success(ok); return r?.message ?? true; }
    catch (e) { toastError(e, fail); return null; }
  };
  return { run, loading };
}

/* ─── proposals: checks, edits, reasons (Needs you, the agent page, the inbox) ─── */

/** The reasons a reject may give (Excom AI Proposal.reject_reason): they teach the agent. */
export const REJECT_REASONS = ["Wrong facts", "Wrong tone", "Wrong recipient", "Not needed", "Other"];

/** The code checks (excom_ai/checks.py), in plain words. */
export const CHECK_LABEL: Record<string, string> = {
  recipient: "Recipient",
  channel: "Mailbox",
  prices: "Prices",
  phrases: "Words it may not use",
  language: "Language",
  shape: "Length and layout",
  footer: "Footer",
  signature: "Signature",
  leak: "Private information",
};

/** JSON fields arrive parsed from queue() and as text from thread_proposals(). */
export function parseJson<T>(v: unknown): T | null {
  if (v == null || v === "") return null;
  if (typeof v !== "string") return v as T;
  try { return JSON.parse(v) as T; } catch { return null; }
}

/** The code checks that failed, with why. */
export function failingChecks(checks?: Checks | null): { key: string; label: string; why: string }[] {
  if (!checks) return [];
  return Object.entries(checks)
    .filter(([k, v]) => k !== "ok" && typeof v === "object" && v && !(v as CheckResult).ok)
    .map(([k, v]) => ({ key: k, label: CHECK_LABEL[k] || k, why: (v as CheckResult).why || "" }));
}

/** Passed: every code check and the AI checker did not fail (needs_you.py checks_ok). */
export function checksPassed(checks?: Checks | null, verdict?: CheckerVerdict): boolean {
  return Boolean(checks?.ok ?? true) && verdict !== "Fail";
}

const VERDICT: Record<string, { label: string; accent: Accent }> = {
  Pass: { label: "AI check passed", accent: "green" },
  Fixed: { label: "AI check fixed it", accent: "amber" },
  Fail: { label: "AI check failed", accent: "rose" },
};

/** A green tick, or the failing checks with why; then the AI checker's verdict and notes. */
export function ChecksSummary({ checks, verdict, notes, compact }: { checks?: Checks | null; verdict?: CheckerVerdict; notes?: string | null; compact?: boolean }) {
  const failing = failingChecks(checks);
  const v = verdict ? VERDICT[verdict] : null;
  return (
    <div className="space-y-1 min-w-0">
      {!checks ? null : failing.length === 0 ? (
        <p className="flex items-center gap-1.5 text-sm text-crayon-green-text"><CircleCheck className="size-4 shrink-0" aria-hidden />All checks passed</p>
      ) : (
        <div className="min-w-0">
          <p className="flex items-center gap-1.5 text-sm text-crayon-rose-text font-medium"><CircleX className="size-4 shrink-0" aria-hidden />{failing.length === 1 ? "1 check failed" : `${failing.length} checks failed`}</p>
          <ul className="mt-0.5 pl-5 space-y-0.5">
            {failing.map((f) => <li key={f.key} className="text-sm text-ink-1 break-words"><span className="font-medium">{f.label}:</span> {f.why || "failed"}</li>)}
          </ul>
        </div>
      )}
      {v && (
        <div className="flex items-start gap-1.5 min-w-0 flex-wrap">
          <Chip size="sm" accent={v.accent} icon={<Sparkles />} label={v.label} />
          {notes && !compact && <span className="text-sm text-ink-2 min-w-0 break-words flex-1">{notes}</span>}
        </div>
      )}
    </div>
  );
}

/** Subject and body, editable, for "Edit and approve". The rest of the payload is kept. */
export function PayloadEditor({ value, onChange, idPrefix }: { value: ProposalPayload; onChange: (v: ProposalPayload) => void; idPrefix: string }) {
  return (
    <div className="space-y-2 min-w-0">
      {"subject" in value && (
        <Field label="Subject" htmlFor={`${idPrefix}-subject`}>
          <Input id={`${idPrefix}-subject`} value={String(value.subject ?? "")} onChange={(e) => onChange({ ...value, subject: e.target.value })} />
        </Field>
      )}
      <Field label="Message" htmlFor={`${idPrefix}-body`}>
        <Textarea id={`${idPrefix}-body`} rows={8} value={String(value.body ?? "")} onChange={(e) => onChange({ ...value, body: e.target.value })} autoFocus />
      </Field>
    </div>
  );
}

/** Agent names for agent ids, from the agents list the Agents home already loads. */
export function useAgentNames(enabled = true) {
  const { data } = useFrappeGetCall<{ message: AgentCard[] }>(enabled ? M.agents : null, undefined, "agents-home", { revalidateOnFocus: false, shouldRetryOnError: false });
  const rows = data?.message ?? [];
  const names: Record<string, string> = {};
  rows.forEach((a) => { names[a.name] = a.agent_name; });
  return { names, waiting: rows.reduce((n, a) => n + (a.waiting || 0), 0) };
}

/** Opens a conversation (a thread id) in the inbox: the record route is keyed by the contact. */
export function useOpenThread() {
  const navigate = useNavigate();
  const { call } = useFrappePostCall("frappe.client.get_value");
  return async (thread: string) => {
    let identity = "";
    try {
      const r = await call({ doctype: "Excom Thread", filters: thread, fieldname: "omni_identity" });
      identity = r?.message?.omni_identity || "";
    } catch { /* not readable: open by the thread itself */ }
    navigate(identity ? `/t/${encodeURIComponent(identity)}?via=${encodeURIComponent(thread)}` : `/t/${encodeURIComponent(thread)}`);
  };
}
