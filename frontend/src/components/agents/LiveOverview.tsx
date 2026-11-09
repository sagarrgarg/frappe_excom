import { AlertTriangle, CircleCheck, CircleHelp, CircleX, Clock, Lock, RefreshCw } from "lucide-react";
import { useFrappeGetCall } from "@/lib/api";
import { Button, Chip, type Accent } from "../primitives";
import { cn } from "../ui/utils";
import { parseError } from "../../lib/errors";
import { bounceRate } from "../../lib/agents-logic";
import type { LiveView, LiveWaiting, MailboxHealth, Mode } from "../../types/agents";
import { M, Meter, Panel, POLICY_LABEL, Stat, cost, liveKey, useOpenThread, when } from "./bits";

/** live(name), refreshed every minute while a screen that uses it is open. One cached copy per agent. */
export function useLive(name: string) {
  const { data, error, isLoading, mutate } = useFrappeGetCall<{ message: LiveView }>(M.live, { name }, liveKey(name), {
    refreshInterval: 60_000, revalidateOnFocus: true, shouldRetryOnError: false,
  });
  return { live: data?.message, error, isLoading, reload: () => { mutate(); } };
}

const MODE_ACCENT: Record<Mode, Accent> = { Off: "neutral", Shadow: "violet", Review: "amber", Auto: "green" };
const TASK_ORDER = ["Done", "Running", "Queued", "Held", "Batched", "Skipped", "Failed"];
const taskRank = (s: string) => { const i = TASK_ORDER.indexOf(s); return i < 0 ? TASK_ORDER.length : i; };

function hoursText(h: number): string {
  if (h < 1) return `${Math.max(1, Math.round(h * 60))} min`;
  return `${Math.round(h * 10) / 10} h`;
}

/** The live view (HLD-007 §13.1): today's work, channel results, spend, health, modes. */
export function LiveOverview({ name, go }: { name: string; go: (tab: "autonomy" | "channels" | "usage" | "needs-you") => void }) {
  const { live, error, isLoading, reload } = useLive(name);
  if (!live) {
    return (
      <Panel title="Today" className="laptop:col-span-2">
        {error ? <p role="alert" className="text-sm text-crayon-rose-text">{parseError(error).message}</p> : <p className="text-sm text-ink-3">{isLoading ? "Loading today's numbers…" : "No numbers yet."}</p>}
      </Panel>
    );
  }
  const t = live.today;
  const c = live.channels;
  const tasks = Object.entries(t.tasks || {}).sort(([a], [b]) => taskRank(a) - taskRank(b));
  const done = Number(t.tasks?.Done || 0);
  const failed = Number(t.tasks?.Failed || 0);
  const reasons: Record<string, number> = {};
  t.rejected.forEach((r) => { const k = r || "No reason given"; reasons[k] = (reasons[k] || 0) + 1; });
  const intents = Object.entries(c.replies_by_intent || {}).sort((a, b) => b[1] - a[1]);
  const replies = intents.reduce((n, [, v]) => n + Number(v || 0), 0);
  const late = c.waiting.filter((w) => w.late).length;
  const refresh = <Button size="sm" variant="ghost" onClick={reload} aria-label="Refresh today's numbers" title="Refreshes every minute on its own"><RefreshCw className={isLoading ? "animate-spin" : ""} /></Button>;

  return (
    <>
      <Panel title="Today's work" actions={refresh}>
        <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] tablet:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)] gap-2">
          <Stat label="Tasks done" value={done} />
          <Stat label="Tasks failed" value={failed} tone={failed ? "rose" : undefined} />
          <Stat label="Waiting for you" value={<button type="button" className="hover:underline" onClick={() => go("needs-you")}>{t.waiting_for_you}</button>} tone={t.waiting_for_you ? "amber" : undefined} />
          <Stat label="You approved" value={t.approved} />
        </div>
        {tasks.length > 0 && (
          <p className="text-xs text-ink-3 mt-2 break-words">All tasks today: {tasks.map(([s, n]) => `${n} ${s.toLowerCase()}`).join(", ")}.</p>
        )}
        <h3 className="text-xs text-ink-3 mt-3 mb-1">Rejected today{t.rejected.length ? `: ${t.rejected.length}` : ""}</h3>
        {t.rejected.length === 0 ? <p className="text-sm text-ink-2">None.</p> : (
          <ul className="flex flex-wrap gap-1">
            {Object.entries(reasons).map(([r, n]) => <li key={r}><Chip size="sm" accent="rose" label={`${r} · ${n}`} /></li>)}
          </ul>
        )}
      </Panel>

      <Panel title="Channel results today">
        <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)] gap-2">
          <Stat label="Sent" value={c.sent} />
          <Stat label="Bounced" value={c.bounces} tone={c.bounces ? "rose" : undefined} hint="Messages returned as undeliverable" />
          <Stat label="Replies" value={replies} />
        </div>
        <h3 className="text-xs text-ink-3 mt-3 mb-1">Replies by what the buyer wants</h3>
        {intents.length === 0 ? <p className="text-sm text-ink-2">No replies yet today.</p> : (
          <ul className="flex flex-wrap gap-1">
            {intents.map(([k, n]) => <li key={k}><Chip size="sm" accent={k === "unread" ? "neutral" : "violet"} label={`${k === "unread" ? "Not read yet" : k} · ${n}`} /></li>)}
          </ul>
        )}
      </Panel>

      <Panel className="laptop:col-span-2" title={<>Buyers waiting for a person{c.waiting.length ? <span className="text-ink-3 font-normal"> · {c.waiting.length}{late ? `, ${late} late` : ""}</span> : null}</>}>
        {c.waiting.length === 0 ? <p className="text-sm text-ink-2">Nobody is waiting.</p> : (
          <>
            <ul className="divide-y divide-border -my-1">{c.waiting.map((w) => <li key={w.name}><WaitingLine w={w} /></li>)}</ul>
            <p className="text-xs text-ink-3 mt-2">Counted in working hours, Monday to Friday. Late means over 4 working hours.</p>
          </>
        )}
      </Panel>

      <Panel title="Spend" actions={<Button size="sm" variant="link" onClick={() => go("channels")}>Change budgets</Button>}>
        <div className="space-y-3">
          <Meter label="AI today" used={live.spend.today} budget={live.spend.daily_budget} />
          <Meter label="AI this month" used={live.spend.month} budget={live.spend.monthly_budget} />
        </div>
        <p className="text-xs text-ink-3 mt-3">Paid lookup tools this month, all agents together: {cost(live.spend.tools_month)}.</p>
      </Panel>

      <Panel title="Health">
        <div className="flex items-center gap-2 flex-wrap text-sm">
          {live.health.failures_in_a_row > 0
            ? <span className="inline-flex items-center gap-1 text-crayon-rose-text"><AlertTriangle className="size-4" aria-hidden />{live.health.failures_in_a_row} failures in a row</span>
            : <span className="inline-flex items-center gap-1 text-crayon-green-text"><CircleCheck className="size-4" aria-hidden />No recent failures</span>}
        </div>
        <h3 className="text-xs text-ink-3 mt-3 mb-1">Mailboxes and numbers</h3>
        {live.health.channels.length === 0 ? <p className="text-sm text-ink-2">No channels set up.</p> : (
          <ul className="space-y-2">{live.health.channels.map((ch) => <li key={ch.account}><ChannelHealth account={ch.account} paused={Boolean(ch.paused)} reason={ch.pause_reason} health={ch.health} /></li>)}</ul>
        )}
      </Panel>

      <Panel className="laptop:col-span-2" title="What it may do on its own" actions={<Button size="sm" variant="link" onClick={() => go("autonomy")}>{live.promotions.length ? `Autonomy · ${live.promotions.length} ${live.promotions.length === 1 ? "suggestion" : "suggestions"}` : "Change"}</Button>}>
        {Object.keys(live.health.modes).length === 0 ? <p className="text-sm text-ink-2">No actions set up.</p> : (
          <ul className="grid grid-cols-1 tablet:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-x-4 gap-y-1">
            {Object.entries(live.health.modes).map(([action, m]) => (
              <li key={action} className="flex items-center gap-2 min-w-0 min-h-8">
                <span className="text-sm text-ink-1 flex-1 min-w-0 truncate">{POLICY_LABEL[action]?.label || action.replace(/_/g, " ")}</span>
                <Chip size="sm" accent={MODE_ACCENT[m.mode] ?? "neutral"} label={m.mode} />
                <span className="text-xs text-ink-3 inline-flex items-center gap-0.5 shrink-0" title="The ceiling: it can never be set higher"><Lock className="size-3" aria-hidden />at most {m.ceiling}</span>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </>
  );
}

function WaitingLine({ w }: { w: LiveWaiting }) {
  const open = useOpenThread();
  return (
    <button type="button" onClick={() => open(w.name)} className="w-full text-left flex items-center gap-2 py-1.5 min-w-0 hover:bg-surface-hover focus-visible:bg-surface-hover outline-none rounded-md px-1">
      <span className="text-sm text-ink-1 truncate min-w-0 flex-1">{w.display_name || w.name}</span>
      {w.ai_intent && <Chip size="sm" accent="violet" label={w.ai_intent} />}
      <span className={cn("shrink-0 inline-flex items-center gap-1 text-sm tabular-nums", w.late ? "text-crayon-rose-text font-medium" : "text-ink-2")}>
        {w.late ? <span aria-hidden className="size-2 rounded-full bg-crayon-rose-base" /> : <Clock className="size-3.5" aria-hidden />}
        {hoursText(w.hours)}
        {w.late && <span className="sr-only">, late</span>}
      </span>
    </button>
  );
}

/** One DNS check: a tick when present, a cross when missing, a question mark when unknown. */
function Tick({ label, ok, why }: { label: string; ok: boolean | null; why?: string }) {
  const Icon = ok === null ? CircleHelp : ok ? CircleCheck : CircleX;
  const state = ok === null ? "unknown" : ok ? "set up" : "missing";
  return (
    <li className={cn("inline-flex items-center gap-1 text-xs", ok === null ? "text-ink-3" : ok ? "text-crayon-green-text" : "text-crayon-rose-text")} title={why}>
      <Icon className="size-3.5" aria-hidden />{label}<span className="sr-only">: {state}</span>
    </li>
  );
}

function ChannelHealth({ account, paused, reason, health: h }: { account: string; paused: boolean; reason?: string | null; health: MailboxHealth | null }) {
  const sent = Number(h?.sent_7d || 0);
  const bounced = Number(h?.hard_bounces_7d || 0);
  const rate = bounceRate(bounced, sent);
  const failedLookup = Boolean(h?.error);
  const known = (v: unknown) => (failedLookup ? null : Boolean(v));
  return (
    <div className={cn("rounded-md border p-2 min-w-0", paused ? "border-crayon-amber-base bg-crayon-amber-tint" : "border-border")}>
      <div className="flex items-center gap-2 min-w-0 flex-wrap">
        <span className="text-sm text-ink-1 truncate min-w-0 flex-1">{account}</span>
        {paused ? <Chip size="sm" accent="amber" label="Paused" /> : h ? <Chip size="sm" accent={h.ok ? "green" : "rose"} label={h.ok ? "Healthy" : "Needs attention"} /> : null}
      </div>
      {paused && <p className="text-xs text-crayon-amber-text mt-1 break-words">Paused: {reason || "no reason given"}</p>}
      {!h ? (
        <p className="text-xs text-ink-3 mt-1">Not checked yet.</p>
      ) : (
        <>
          <ul className="flex flex-wrap gap-x-3 gap-y-1 mt-1.5" aria-label={`Mail checks for ${account}`}>
            <Tick label="SPF" ok={known(h.spf)} why={h.spf || undefined} />
            <Tick label="DKIM" ok={known(h.dkim)} />
            <Tick label="DMARC" ok={known(h.dmarc)} why={h.dmarc || undefined} />
            <Tick label="MX" ok={known(h.mx && h.mx.length)} />
            <Tick label={h.blocklisted ? "On a blocklist" : "Not blocklisted"} ok={failedLookup || h.blocklisted == null ? null : !h.blocklisted} why={h.blocklisted == null ? "The blocklist did not answer" : undefined} />
          </ul>
          <p className="text-xs text-ink-3 mt-1">
            Bounces in 7 days: {rate === null ? "nothing sent" : <span className={cn("tabular-nums", rate >= 3 ? "text-crayon-rose-text font-medium" : "text-ink-2")}>{rate}% ({bounced} of {sent})</span>}
            {h.checked_on ? ` · checked ${when(h.checked_on)}` : ""}
          </p>
          {failedLookup && <p className="text-xs text-crayon-amber-text mt-0.5 break-words">The check could not finish: {h.error}</p>}
        </>
      )}
    </div>
  );
}
