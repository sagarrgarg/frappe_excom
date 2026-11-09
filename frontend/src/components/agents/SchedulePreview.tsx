import { useMemo, useState } from "react";
import { CalendarClock, ExternalLink } from "lucide-react";
import { useFrappeGetCall } from "@/lib/api";
import { Button } from "../primitives";
import { parseError } from "../../lib/errors";
import { deskUrl } from "../../hooks/useRecordLinks";
import { parseFrappeDateTime } from "../../utils/datetime";
import { inZone } from "../../lib/agents-logic";
import type { DryRunRow } from "../../types/agents";
import { M, Panel, when } from "./bits";

const DAYS = 21;
const FIRST = 15;

type Enrollment = { name: string; lead?: string | null; person?: string | null; zone: string; steps: DryRunRow[] };

/**
 * Schedule preview (HLD-007 §15.3, sequencer.dry_run): when each step of each active enrollment
 * would go under today's rules (windows, waits, warm-up, caps), in the buyer's time and ours.
 * Nothing is sent.
 */
export function SchedulePreview({ name }: { name: string }) {
  const { data, error, isLoading, mutate } = useFrappeGetCall<{ message: DryRunRow[] }>(M.dryRun, { name, days: DAYS }, `agent-dry-run-${name}`, { revalidateOnFocus: false, shouldRetryOnError: false });
  const [all, setAll] = useState(false);
  const rows = useMemo(() => data?.message ?? [], [data]);
  const enrollments = useMemo(() => {
    const map = new Map<string, Enrollment>();
    rows.forEach((r) => {
      if (!map.has(r.enrollment)) map.set(r.enrollment, { name: r.enrollment, lead: r.lead, person: r.person, zone: r.zone, steps: [] });
      map.get(r.enrollment)!.steps.push(r);
    });
    map.forEach((e) => e.steps.sort((a, b) => a.step - b.step));
    return [...map.values()];
  }, [rows]);
  const fits = rows.filter((r) => r.send_at).length;
  const later = rows.length - fits;
  const shown = all ? enrollments : enrollments.slice(0, FIRST);

  return (
    <Panel title={<span className="inline-flex items-center gap-1.5"><CalendarClock className="size-4 text-ink-3" aria-hidden />Schedule preview, next {DAYS} days</span>}
      actions={<Button size="sm" variant="ghost" disabled={isLoading} onClick={() => mutate()}>Work it out again</Button>}>
      {error ? <p role="alert" className="text-sm text-crayon-rose-text">{parseError(error).message}</p>
        : !data ? <p className="text-sm text-ink-3">Working out the schedule…</p>
        : enrollments.length === 0 ? <p className="text-sm text-ink-2">Nobody is enrolled in a sequence, so nothing is scheduled.</p>
        : (
          <div className="space-y-2 min-w-0">
            <p className="text-sm text-ink-2">
              {enrollments.length} {enrollments.length === 1 ? "buyer" : "buyers"} in a sequence: {fits} {fits === 1 ? "message fits" : "messages fit"} in the next {DAYS} days
              {later ? <>, <span className="text-crayon-amber-text">{later} would go later</span> (the daily limits are full)</> : null}. Nothing is sent from here.
            </p>
            <ul className="space-y-2">
              {shown.map((e) => (
                <li key={e.name} className="rounded-md border border-border p-2.5 min-w-0">
                  <div className="flex items-center gap-2 min-w-0 flex-wrap">
                    {e.lead
                      ? <a href={deskUrl("Lead", e.lead)} target="_blank" rel="noreferrer" className="text-sm font-medium text-ink-1 hover:underline inline-flex items-center gap-1 min-w-0"><span className="truncate">{e.lead}</span><ExternalLink className="size-3 shrink-0" aria-hidden /></a>
                      : <span className="text-sm font-medium text-ink-1 truncate min-w-0">{e.person || e.name}</span>}
                    <span className="text-xs text-ink-3 truncate min-w-0">their time zone: {e.zone}</span>
                  </div>
                  <ol className="mt-1.5 space-y-1">
                    {e.steps.map((s) => {
                      const at = s.send_at ? parseFrappeDateTime(s.send_at) : null;
                      const theirs = at ? inZone(at, s.zone) : null;
                      return (
                        <li key={s.step} className="grid grid-cols-[3.5rem_minmax(0,1fr)] gap-x-2 text-sm min-w-0">
                          <span className="text-ink-3">Step {s.step}</span>
                          {at ? (
                            <span className="min-w-0 break-words text-ink-1">
                              {theirs ? <>{theirs} <span className="text-ink-3">their time</span> · </> : null}
                              {when(s.send_at)} <span className="text-ink-3">ours</span>
                              <span className="block text-xs text-ink-3 break-all">{s.account ? `from ${s.account}` : "no mailbox free"}</span>
                            </span>
                          ) : (
                            <span className="min-w-0 text-crayon-amber-text">{s.account ? `Not within ${DAYS} days: ${s.account} is full` : "No mailbox it may send from"}</span>
                          )}
                        </li>
                      );
                    })}
                  </ol>
                </li>
              ))}
            </ul>
            {enrollments.length > FIRST && <Button size="sm" variant="link" onClick={() => setAll((v) => !v)}>{all ? "Show fewer" : `Show all ${enrollments.length}`}</Button>}
          </div>
        )}
    </Panel>
  );
}
