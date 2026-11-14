import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useFrappePostCall } from "frappe-react-sdk";
import { Check, ChevronDown, Copy, ExternalLink, KeyRound, Loader2, Plug, Power, RefreshCw, Settings2, Stethoscope, Wrench } from "lucide-react";
import { toast } from "sonner";
import { useFrappeGetCall } from "@/lib/api";
import { ACCENT_CLASSES, Button, Chip, Modal, Sheet, buttonVariants } from "../primitives";
import { cn } from "../ui/utils";
import { DocForm, useSchema } from "./DocAdmin";
import { serverMessage } from "./util";
import {
  FIELDS, SETUP_METHODS as S, SETUP_STATUS_KEY, firstOpenStep, fixAction, itemMark, progress, stepAccent, stepLabel,
  type SetupAgent, type SetupItem, type SetupStatus, type SetupStep, type ToolCard,
} from "../../lib/ai-setup";

const SETTINGS = "Excom AI Settings";
const TOOL = "Excom AI Tool";
const INSTALL_CMD = "sudo ./deploy/install.sh";
const SIGNALS_URL = "/app/excom-ai-signal-code?status=Proposed";

/**
 * /admin/ai-setup — everything the AI agents need, in order (HLD-010). The status is computed by the
 * server from the real records on every load, so this page never stores "done"; each step shows what
 * is missing and the fix: a small form, a check button, or a link to where the fix lives.
 */
export function AISetupAdmin() {
  const { data, error, isLoading, mutate } = useFrappeGetCall<{ message: SetupStatus }>(S.status, undefined, SETUP_STATUS_KEY, { revalidateOnFocus: false, shouldRetryOnError: false });
  const status = data?.message;
  const [open, setOpen] = useState<Set<string> | null>(null);
  useEffect(() => {
    if (status && open === null) { const first = firstOpenStep(status.steps); setOpen(new Set(first ? [first] : [])); }
  }, [status, open]);
  const refresh = () => { void mutate(); };
  const toggle = (id: string) => setOpen((o) => { const n = new Set(o ?? []); if (n.has(id)) n.delete(id); else n.add(id); return n; });

  return (
    <div className="flex-1 min-h-0 overflow-y-auto">
      <div className="px-4 py-3 space-y-3 max-w-3xl min-w-0">
        {error ? <p role="alert" className="text-sm text-crayon-rose-text">{serverMessage(error)}</p>
          : isLoading && !status ? <div className="flex justify-center py-8 text-ink-3"><Loader2 className="size-5 animate-spin" /></div>
          : status ? (
            <>
              <Header status={status} onChanged={refresh} />
              <ol className="space-y-2">
                {status.steps.map((s, i) => (
                  <StepPanel key={s.id} n={i + 1} step={s} status={status} open={open?.has(s.id) ?? false} onToggle={() => toggle(s.id)} onChanged={refresh} />
                ))}
              </ol>
            </>
          ) : null}
      </div>
    </div>
  );
}

/* ─── header: progress and the master switch ─── */

function Header({ status, onChanged }: { status: SetupStatus; onChanged: () => void }) {
  const { done, total } = progress(status.steps);
  const { call: goLive, loading } = useFrappePostCall(S.goLive);
  const [missing, setMissing] = useState<string[] | null>(null);
  const [confirmOff, setConfirmOff] = useState(false);
  const on = Boolean(status.enabled);
  const turnOn = async (confirm: 0 | 1) => {
    try {
      const r = (await goLive({ enable: 1, confirm })).message as { enabled: number; missing: string[] };
      if (!r.enabled && r.missing?.length) { setMissing(r.missing); return; }
      setMissing(null); toast.success("The AI agents are on."); onChanged();
    } catch (e) { toast.error(serverMessage(e)); }
  };
  const turnOff = async () => {
    try { await goLive({ enable: 0, confirm: 0 }); setConfirmOff(false); toast.success("The AI agents are off."); onChanged(); }
    catch (e) { toast.error(serverMessage(e)); }
  };
  const pct = total ? Math.round((done / total) * 100) : 0;
  return (
    <section className="rounded-lg border border-border p-3 space-y-2 min-w-0">
      <p className="text-sm text-ink-2">Everything the AI agents need, in order, with the fix on the page.</p>
      <div className="flex items-center gap-2 flex-wrap min-w-0">
        <span className="text-md text-ink-1 tabular-nums">{done} of {total} steps done</span>
        <Chip size="sm" accent={on ? "green" : "neutral"} icon={<Power />} label={on ? "AI agents on" : "AI agents off"} />
        <span className="flex-1" />
        <Button size="sm" variant="ghost" onClick={onChanged} aria-label="Check again"><RefreshCw /></Button>
        {on ? <Button size="sm" variant="danger" disabled={loading} onClick={() => setConfirmOff(true)}><Power />Turn off</Button>
          : <Button size="sm" variant="primary" disabled={loading} onClick={() => turnOn(0)}><Power />{loading ? "Working…" : "Go live"}</Button>}
      </div>
      <div className="h-1.5 rounded-full bg-surface-sunken overflow-hidden" role="progressbar" aria-valuemin={0} aria-valuemax={total} aria-valuenow={done} aria-label="Setup progress">
        <div className={cn("h-full rounded-full", pct === 100 ? "bg-crayon-green-base" : "bg-crayon-blue-base")} style={{ width: `${pct}%` }} />
      </div>
      {!on && status.missing.length > 0 && <p className="text-xs text-ink-3">Still missing before going live: {status.missing.join(", ")}.</p>}

      <Modal open={missing !== null} onOpenChange={(o) => !o && setMissing(null)} title="Go live with steps missing?"
        footer={<><Button variant="ghost" onClick={() => setMissing(null)}>Cancel</Button><Button variant="danger" disabled={loading} onClick={() => turnOn(1)}>Go live anyway</Button></>}>
        <div className="space-y-2 text-sm text-ink-2">
          <p>These steps are not started yet:</p>
          <ul className="list-disc pl-5 text-ink-1">{(missing ?? []).map((m) => <li key={m}>{m}</li>)}</ul>
          <p>Agents that need them will wait or fail until they are done.</p>
        </div>
      </Modal>
      <Modal open={confirmOff} onOpenChange={setConfirmOff} title="Turn the AI agents off?"
        footer={<><Button variant="ghost" onClick={() => setConfirmOff(false)}>Cancel</Button><Button variant="danger" disabled={loading} onClick={turnOff}>Turn off</Button></>}>
        <p className="text-sm text-ink-2">Every agent stops working until you turn this on again. Nothing is deleted.</p>
      </Modal>
    </section>
  );
}

/* ─── one step ─── */

function StepPanel({ n, step, status, open, onToggle, onChanged }: { n: number; step: SetupStep; status: SetupStatus; open: boolean; onToggle: () => void; onChanged: () => void }) {
  const accent = stepAccent(step.state);
  const bodyId = `ai-setup-${step.id}`;
  return (
    <li className="rounded-lg border border-border min-w-0">
      <button type="button" onClick={onToggle} aria-expanded={open} aria-controls={bodyId} className="w-full flex items-center gap-2 px-3 min-h-11 py-2 text-left hover:bg-surface-hover rounded-lg min-w-0">
        <span className={cn("size-6 shrink-0 rounded-full grid place-items-center text-xs font-medium tabular-nums", ACCENT_CLASSES[accent].tint, ACCENT_CLASSES[accent].text)}>{step.state === "done" ? <Check className="size-3.5" /> : n}</span>
        <span className="text-sm text-ink-1 font-medium truncate flex-1 min-w-0">{step.title}</span>
        <Chip size="sm" accent={accent} label={stepLabel(step.state)} />
        <ChevronDown className={cn("size-4 shrink-0 text-ink-3 transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <div id={bodyId} className="border-t border-border px-3 py-3 space-y-3 min-w-0">
          <StepBody step={step} status={status} onChanged={onChanged} />
        </div>
      )}
    </li>
  );
}

function StepBody({ step, status, onChanged }: { step: SetupStep; status: SetupStatus; onChanged: () => void }) {
  const items = step.items.length > 0 ? <Items items={step.items} onChanged={onChanged} /> : null;
  switch (step.id) {
    case "provider": return <>{items}<ProviderExtras isSystemManager={status.is_system_manager} onChanged={onChanged} /></>;
    case "budgets": return <>{items}<SettingsForm fields={FIELDS.budgets} onSaved={onChanged} /></>;
    case "basics": return <>{items}<SettingsForm fields={FIELDS.basics} onSaved={onChanged} /></>;
    case "email": return <>{items ?? <Quiet>No agent sends email yet. Add a mailbox on an agent's Channels tab.</Quiet>}<Quiet>SPF, DKIM, DMARC and MX are set where your domain's DNS is hosted. Check DNS reads them again.</Quiet></>;
    case "whatsapp": return <>{items ?? <Quiet>No agent uses WhatsApp yet.</Quiet>}<WhatsAppExtras /><SettingsForm fields={FIELDS.whatsapp} onSaved={onChanged} /></>;
    case "tools": return <>{items}<Tools cards={step.tools ?? []} onChanged={onChanged} /></>;
    case "signals": return <>{items ?? <Quiet>Only Sales agents use trade signals.</Quiet>}<DeskLink href={SIGNALS_URL} label="Review proposed signals" /></>;
    case "voice": return <>{items}<VoiceExtras onChanged={onChanged} /><SettingsForm fields={FIELDS.voice} onSaved={onChanged} /></>;
    case "agents": return <>{items}<AgentsList agents={step.agents ?? []} /></>;
    default: return items;
  }
}

function Quiet({ children }: { children: React.ReactNode }) {
  return <p className="text-xs text-ink-3">{children}</p>;
}

/* ─── checks ─── */

function Items({ items, onChanged }: { items: SetupItem[]; onChanged: () => void }) {
  return (
    <ul className="divide-y divide-border rounded-md border border-border min-w-0">
      {items.map((it, i) => {
        const m = itemMark(it);
        return (
          <li key={i} className="flex items-start gap-2 px-2 py-2 min-w-0">
            <span className={cn("size-5 shrink-0 rounded-full grid place-items-center text-xs font-semibold mt-0.5", ACCENT_CLASSES[m.accent].tint, ACCENT_CLASSES[m.accent].text)} aria-label={m.label} title={m.label}>{m.mark}</span>
            <div className="min-w-0 flex-1">
              <p className="text-sm text-ink-1 break-words">{it.label}</p>
              {it.detail && <p className="text-xs text-ink-2 mt-0.5 break-words">{it.detail}</p>}
            </div>
            <Fix fix={it.fix} onChanged={onChanged} />
          </li>
        );
      })}
    </ul>
  );
}

function Fix({ fix, onChanged }: { fix: string | null; onChanged: () => void }) {
  const a = fixAction(fix);
  if (!a) return null;
  if (a.kind === "dns") return <DnsButton agent={a.agent} row={a.row} onChanged={onChanged} />;
  if (a.kind === "app") return <Link to={a.to} className={buttonVariants({ variant: "default", size: "sm" })}>Open</Link>;
  return <a href={a.href} target="_blank" rel="noreferrer" className={buttonVariants({ variant: "default", size: "sm" })}>Open<ExternalLink /></a>;
}

function DeskLink({ href, label }: { href: string; label: string }) {
  return <a href={href} target="_blank" rel="noreferrer" className={buttonVariants({ variant: "default", size: "sm" })}>{label}<ExternalLink /></a>;
}

function DnsButton({ agent, row, onChanged }: { agent: string; row: string; onChanged: () => void }) {
  const { call, loading } = useFrappePostCall(S.checkDns);
  const run = async () => {
    try {
      const r = (await call({ agent, row })).message as { ok: boolean; paused: number };
      if (r.ok) toast.success("Sending records look right.");
      else if (r.paused) toast.error("This mailbox was paused: its domain is on a blocklist.", { duration: 10000 });
      else toast.warning("Some sending records are still missing. See the row for which.");
      onChanged();
    } catch (e) { toast.error(serverMessage(e)); }
  };
  return <Button size="sm" variant="default" disabled={loading} onClick={run}><Stethoscope className={loading ? "animate-spin" : ""} />{loading ? "Checking…" : "Check DNS"}</Button>;
}

/* ─── forms ─── */

function SettingsForm({ fields, onSaved }: { fields: string[]; onSaved: () => void }) {
  const { data } = useSchema(SETTINGS);
  const schema = data?.message;
  if (!schema) return <div className="flex justify-center py-4 text-ink-3"><Loader2 className="size-4 animate-spin" /></div>;
  return (
    <div className="rounded-md border border-border overflow-hidden min-w-0">
      <DocForm doctype={SETTINGS} name="" schema={schema} fields={fields} onSaved={onSaved} />
    </div>
  );
}

function ToolForm({ name, onSaved }: { name: string; onSaved: () => void }) {
  const { data } = useSchema(TOOL);
  const schema = data?.message;
  if (!schema) return <div className="flex justify-center py-8 text-ink-3"><Loader2 className="size-5 animate-spin" /></div>;
  return <DocForm doctype={TOOL} name={name} schema={schema} fields={FIELDS.tool} onSaved={onSaved} />;
}

/* ─── step extras ─── */

function ProviderExtras({ isSystemManager, onChanged }: { isSystemManager: boolean; onChanged: () => void }) {
  const { call, loading } = useFrappePostCall(S.testProvider);
  if (!isSystemManager) {
    return (
      <div className="flex items-start gap-2 flex-wrap min-w-0">
        <p className="text-xs text-ink-2 flex-1 min-w-[200px]">Only a System Manager can change the AI provider (it serves every app). Ask one, or open it in Desk.</p>
        <DeskLink href="/app/ai-settings" label="AI Settings" />
      </div>
    );
  }
  const run = async () => {
    try {
      const r = (await call({})).message as { ok: boolean; detail: string };
      if (r.ok) toast.success(r.detail || "The provider answered."); else toast.error(r.detail || "The provider did not answer.", { duration: 10000 });
      onChanged();
    } catch (e) { toast.error(serverMessage(e)); }
  };
  return (
    <div className="flex items-center gap-2 flex-wrap">
      <Button size="sm" variant="default" disabled={loading} onClick={run}><Plug className={loading ? "animate-pulse" : ""} />{loading ? "Testing…" : "Test connection (one tiny paid call)"}</Button>
    </div>
  );
}

function WhatsAppExtras() {
  const navigate = useNavigate();
  return (
    <div className="flex items-center gap-2 flex-wrap">
      <Button size="sm" variant="default" onClick={() => navigate("/admin/templates")}><Stethoscope />WhatsApp templates and Diagnose</Button>
    </div>
  );
}

function Tools({ cards, onChanged }: { cards: ToolCard[]; onChanged: () => void }) {
  const [edit, setEdit] = useState<ToolCard | null>(null);
  const [people, setPeople] = useState(false);
  const [all, setAll] = useState(false);
  const main = cards.filter((c) => c.needed || c.enabled);
  const rest = cards.filter((c) => !(c.needed || c.enabled));
  const shown = all ? cards : main;
  return (
    <div className="space-y-2 min-w-0">
      {cards.length === 0 && <Quiet>No data tools on this site yet.</Quiet>}
      <ul className="grid grid-cols-1 tablet:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-2">
        {shown.map((c) => <li key={c.key} className="min-w-0"><ToolCardView card={c} onEdit={() => setEdit(c)} onChanged={onChanged} /></li>)}
      </ul>
      <div className="flex items-center gap-2 flex-wrap">
        {rest.length > 0 && <Button size="sm" variant="ghost" onClick={() => setAll((v) => !v)}>{all ? "Show only tools in use" : `Show ${rest.length} other ${rest.length === 1 ? "tool" : "tools"}`}</Button>}
        <Button size="sm" variant="default" onClick={() => setPeople(true)}><Settings2 />People finder settings</Button>
      </div>
      <Sheet open={edit !== null} onOpenChange={(o) => !o && setEdit(null)} title={edit?.label ?? ""} width="w-[560px]" className="!p-0">
        {edit && <ToolForm name={edit.key} onSaved={() => onChanged()} />}
      </Sheet>
      <Sheet open={people} onOpenChange={setPeople} title="People finder" width="w-[560px]" className="!p-0">
        {people && <SettingsForm fields={FIELDS.people} onSaved={onChanged} />}
      </Sheet>
    </div>
  );
}

function money(v: number): string {
  return Number(v || 0).toLocaleString("en-IN", { maximumFractionDigits: 2 });
}

function ToolCardView({ card: c, onEdit, onChanged }: { card: ToolCard; onEdit: () => void; onChanged: () => void }) {
  const { call, loading } = useFrappePostCall(S.testTool);
  const state = !c.enabled ? { accent: "neutral" as const, label: "Off" } : c.needs_key && !c.has_key ? { accent: "amber" as const, label: "No key" } : { accent: "green" as const, label: "On" };
  const test = async () => {
    try {
      const r = (await call({ key: c.key })).message as { ok: boolean | null; detail: string };
      if (r.ok) toast.success(r.detail || "The key works."); else if (r.ok === false) toast.error(r.detail || "The key did not work.", { duration: 10000 }); else toast(r.detail);
      onChanged();
    } catch (e) { toast.error(serverMessage(e)); }
  };
  return (
    <article className="rounded-md border border-border p-2 space-y-1.5 min-w-0 h-full">
      <div className="flex items-center gap-1.5 min-w-0 flex-wrap">
        <span className="text-sm text-ink-1 font-medium truncate min-w-0">{c.label}</span>
        <Chip size="sm" accent={state.accent} label={state.label} />
        {c.needed && <Chip size="sm" accent="blue" label="Needed" />}
        {c.tier && <Chip size="sm" accent="neutral" label={c.tier} />}
      </div>
      <p className="text-xs text-ink-2 tabular-nums">{c.monthly_budget ? `${money(c.used_this_month)} of ${money(c.monthly_budget)} spent this month` : c.used_this_month ? `${money(c.used_this_month)} spent this month, no budget set` : "No monthly budget set"}</p>
      {c.notes && <p className="text-xs text-ink-3 line-clamp-2 break-words">{c.notes}</p>}
      {c.last_error && <p className="text-xs text-crayon-rose-text break-words line-clamp-3">Last error: {c.last_error}</p>}
      <div className="flex items-center gap-2 flex-wrap pt-0.5">
        <Button size="sm" variant="default" onClick={onEdit}><Wrench />Settings</Button>
        {c.can_test && <Button size="sm" variant="ghost" disabled={loading} onClick={test}><Plug className={loading ? "animate-pulse" : ""} />{loading ? "Testing…" : "Test key (free)"}</Button>}
      </div>
    </article>
  );
}

function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try { await navigator.clipboard.writeText(text); setCopied(true); window.setTimeout(() => setCopied(false), 1500); }
    catch { toast.error("Could not copy. Select the text and copy it by hand."); }
  };
  return <Button size="icon-sm" variant="ghost" onClick={copy} aria-label={`Copy ${label}`}>{copied ? <Check className="text-crayon-green-text" /> : <Copy />}</Button>;
}

function CopyLine({ text, label }: { text: string; label: string }) {
  return (
    <div className="flex items-center gap-2 rounded-md border border-border bg-surface-sunken px-2 py-1 min-w-0">
      <code className="text-xs text-ink-1 font-mono break-all flex-1 min-w-0">{text}</code>
      <CopyButton text={text} label={label} />
    </div>
  );
}

function VoiceExtras({ onChanged }: { onChanged: () => void }) {
  const { call: makeKey, loading: making } = useFrappePostCall(S.makeVoiceKey);
  const { call: check, loading: checking } = useFrappePostCall(S.checkVoice);
  const [confirm, setConfirm] = useState(false);
  const [key, setKey] = useState<string | null>(null);
  const make = async () => {
    try { const r = (await makeKey({ confirm: 1 })).message as { key: string }; setConfirm(false); setKey(r.key); onChanged(); }
    catch (e) { toast.error(serverMessage(e)); }
  };
  const runCheck = async () => {
    try {
      const r = (await check({})).message as { ok: boolean; detail: string };
      if (r.ok) toast.success(r.detail || "The voice service answered."); else toast.error(r.detail || "The voice service did not answer.", { duration: 10000 });
      onChanged();
    } catch (e) { toast.error(serverMessage(e)); }
  };
  return (
    <div className="space-y-2 min-w-0">
      <div className="space-y-1 min-w-0">
        <p className="text-xs text-ink-2">The voice service runs on its own VM. To install it, run this from <code className="font-mono">apps/excom/voice_service</code> on that VM:</p>
        <CopyLine text={INSTALL_CMD} label="install command" />
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        <Button size="sm" variant="default" disabled={making} onClick={() => setConfirm(true)}><KeyRound />Make a new key</Button>
        <Button size="sm" variant="default" disabled={checking} onClick={runCheck}><Stethoscope className={checking ? "animate-spin" : ""} />{checking ? "Checking…" : "Check service"}</Button>
      </div>
      <Modal open={confirm} onOpenChange={setConfirm} title="Make a new voice key?"
        footer={<><Button variant="ghost" onClick={() => setConfirm(false)}>Cancel</Button><Button variant="danger" disabled={making} onClick={make}>{making ? "Working…" : "Make key"}</Button></>}>
        <p className="text-sm text-ink-2">AI calls stop until the voice service has this key. You will see the key once, to copy to the VM.</p>
      </Modal>
      <Modal open={key !== null} onOpenChange={(o) => !o && setKey(null)} title="Your new voice key"
        footer={<Button variant="primary" onClick={() => setKey(null)}>Done</Button>}>
        <div className="space-y-2 text-sm text-ink-2">
          <p>Copy it now: it is not shown again.</p>
          {key && <CopyLine text={key} label="voice key" />}
          <p>On the voice VM, put it in <code className="font-mono">/etc/excom-voice.env</code> as <code className="font-mono">VOICE_KEY=…</code>, then restart the voice service. Use Check service after.</p>
        </div>
      </Modal>
    </div>
  );
}

function AgentsList({ agents }: { agents: SetupAgent[] }) {
  return (
    <div className="space-y-2 min-w-0">
      {agents.length > 0 && (
        <ul className="divide-y divide-border rounded-md border border-border min-w-0">
          {agents.map((a) => (
            <li key={a.name} className="flex items-center gap-2 px-2 py-2 min-w-0 flex-wrap">
              <Link to={`/agents/${encodeURIComponent(a.name)}`} className="text-sm text-ink-1 font-medium hover:underline truncate min-w-0 flex-1">{a.agent_name}</Link>
              <Chip size="sm" accent="neutral" label={a.agent_type} />
              <Chip size="sm" accent={a.status === "Live" ? "green" : a.status === "Shadow" ? "blue" : a.status === "On Hold" ? "amber" : "neutral"} label={a.status} />
              <Chip size="sm" accent={a.blockers ? "rose" : "green"} label={a.blockers ? `${a.blockers} ${a.blockers === 1 ? "blocker" : "blockers"}` : "No blockers"} />
            </li>
          ))}
        </ul>
      )}
      <Link to="/agents/new" className={buttonVariants({ variant: "default", size: "sm" })}>Build an agent</Link>
    </div>
  );
}
