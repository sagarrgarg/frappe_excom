import { useState } from "react";
import { BarChart3, Download, FileSpreadsheet, FileText, Loader2, RefreshCw } from "lucide-react";
import { useFrappeGetCall } from "@/lib/api";
import { PageFrame } from "../shell/PageFrame";
import { Button, EmptyState, SegmentedControl, Select } from "../primitives";
import { cn } from "../ui/utils";

/**
 * Activity — what a person actually did, over a day, a week or a month.
 *
 * An agent sees themselves and nobody else; that is decided on the server, not by hiding the
 * dropdown, because a report is the sort of screen somebody tries by editing the URL. The page only
 * shows the picker when the server says there is more than one person to pick.
 */

type Period = "daily" | "weekly" | "monthly";

interface Stream { total: number; by_channel?: Record<string, number>; by_outcome?: Record<string, number>; by_kind?: Record<string, number> }
interface Calls { outbound: number; inbound: number; connected: number; missed: number; talk_seconds: number }

interface Row {
  user: string;
  full_name: string;
  messages: Stream;
  calls: Calls;
  closures: Stream;
  tasks: { created: number; completed: number };
  records: Stream;
  active_days: number;
  did_anything: boolean;
}

interface Report {
  period: Period;
  from: string;
  to: string;
  generated_at: string;
  can_see_everyone: boolean;
  rows: Row[];
  totals: {
    people: number;
    messages: Stream;
    calls: Calls;
    closures: Stream;
    tasks: { created: number; completed: number };
    records: Stream;
  };
}

function mmss(seconds: number) {
  const s = Math.max(0, Math.round(seconds || 0));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
  return `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}m`;
}

export function ReportsPage() {
  const [period, setPeriod] = useState<Period>("weekly");
  const [on, setOn] = useState(() => new Date().toISOString().slice(0, 10));
  const [who, setWho] = useState("");

  const { data: peopleData } = useFrappeGetCall<{ message: { name: string; full_name: string }[] }>(
    "excom.excom.api.reports.reportable_users",
    undefined,
    "reportable-users",
  );
  const people = peopleData?.message ?? [];

  const { data, isLoading, mutate } = useFrappeGetCall<{ message: Report }>(
    "excom.excom.api.reports.get_activity_report",
    { user: who, period, on },
    `activity:${who}:${period}:${on}`,
    { revalidateOnFocus: false },
  );
  const report = data?.message;

  const download = (fmt: "xlsx" | "pdf") => {
    const q = new URLSearchParams({ user: who, period, on, fmt });
    // A plain navigation, so the browser handles the file the way it handles any download.
    window.location.href =
      `/api/method/excom.excom.api.report_files.download_activity_report?${q}`;
  };

  return (
    <PageFrame
      title="Activity"
      icon={<BarChart3 />}
      wide
      actions={
        <>
          <SegmentedControl<Period>
            value={period}
            onChange={setPeriod}
            ariaLabel="Over what period"
            segments={[
              { value: "daily", label: "Day" },
              { value: "weekly", label: "Week" },
              { value: "monthly", label: "Month" },
            ]}
          />
          <input
            type="date"
            value={on}
            onChange={(e) => setOn(e.target.value)}
            aria-label="Which day to report on"
            className="h-8 rounded-md border border-border bg-surface px-2 text-sm text-ink-1"
          />
          {/* Only where there is a choice: an agent has exactly one person to report on. */}
          {people.length > 1 && (
            <Select
              value={who}
              onChange={(e) => setWho(e.target.value)}
              aria-label="Whose activity"
              className="w-[190px]"
            >
              <option value="">Everyone</option>
              {people.map((p) => (
                <option key={p.name} value={p.name}>
                  {p.full_name}
                </option>
              ))}
            </Select>
          )}
          <Button variant="ghost" size="icon" aria-label="Refresh" onClick={() => void mutate()}>
            <RefreshCw className={isLoading ? "animate-spin" : ""} />
          </Button>
          <Button size="sm" variant="subtle" onClick={() => download("xlsx")}>
            <FileSpreadsheet className="size-4" />
            Excel
          </Button>
          <Button size="sm" variant="subtle" onClick={() => download("pdf")}>
            <FileText className="size-4" />
            PDF
          </Button>
        </>
      }
    >
      {isLoading && !report ? (
        <div className="flex justify-center py-10 text-ink-3">
          <Loader2 className="size-5 animate-spin" />
        </div>
      ) : !report ? (
        <EmptyState icon={<BarChart3 />} title="No report" hint="Pick a period to see activity." />
      ) : (
        <>
          <p className="text-xs text-ink-3 mb-3">
            {report.from === report.to ? report.from : `${report.from} to ${report.to}`} ·{" "}
            {report.totals.people} {report.totals.people === 1 ? "person" : "people"}
          </p>

          <div className="grid grid-cols-2 tablet:grid-cols-3 laptop:grid-cols-6 gap-px bg-border border border-border rounded-md overflow-hidden mb-4">
            <Tile label="Messages sent" value={report.totals.messages.total} />
            <Tile label="Calls out" value={report.totals.calls.outbound} />
            <Tile label="Calls in" value={report.totals.calls.inbound} />
            <Tile label="Talk time" value={mmss(report.totals.calls.talk_seconds)} />
            <Tile label="Closed" value={report.totals.closures.total} accent="good" />
            <Tile label="Missed calls" value={report.totals.calls.missed} accent={report.totals.calls.missed ? "warn" : undefined} />
          </div>

          <div className="overflow-x-auto border border-border rounded-md bg-surface">
            <table className="w-full min-w-[56rem] border-collapse">
              <thead>
                <tr className="bg-surface-sunken">
                  <Th className="text-left">Person</Th>
                  <Th>Messages</Th>
                  <Th>Calls out</Th>
                  <Th>Calls in</Th>
                  <Th>Connected</Th>
                  <Th>Missed</Th>
                  <Th>Talk time</Th>
                  <Th>Closed</Th>
                  <Th>Tasks made</Th>
                  <Th>Tasks done</Th>
                  <Th>New records</Th>
                  <Th>Active days</Th>
                </tr>
              </thead>
              <tbody>
                {report.rows.map((r) => (
                  <tr key={r.user} className={cn("border-t border-border", !r.did_anything && "text-ink-3")}>
                    <Td className="text-left font-medium">{r.full_name}</Td>
                    <Td>{r.messages.total}</Td>
                    <Td>{r.calls.outbound}</Td>
                    <Td>{r.calls.inbound}</Td>
                    <Td>{r.calls.connected}</Td>
                    <Td className={r.calls.missed ? "text-crayon-rose-text font-medium" : undefined}>
                      {r.calls.missed}
                    </Td>
                    <Td>{mmss(r.calls.talk_seconds)}</Td>
                    <Td className={r.closures.total ? "text-crayon-green-text font-medium" : undefined}>
                      {r.closures.total}
                    </Td>
                    <Td>{r.tasks.created}</Td>
                    <Td>{r.tasks.completed}</Td>
                    <Td>{r.records.total}</Td>
                    <Td>{r.active_days}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {report.rows.every((r) => !r.did_anything) && (
            <p className="mt-3 text-sm text-ink-3">
              Nothing was recorded in this period. That is a fact about the period, not a missing report.
            </p>
          )}
        </>
      )}
    </PageFrame>
  );
}

function Tile({ label, value, accent }: { label: string; value: number | string; accent?: "good" | "warn" }) {
  return (
    <div className="bg-surface p-3">
      <div
        className={cn(
          "text-xl font-semibold tabular-nums leading-tight",
          accent === "good" && "text-crayon-green-text",
          accent === "warn" && "text-crayon-rose-text",
        )}
      >
        {value}
      </div>
      <div className="text-xs text-ink-3 mt-0.5">{label}</div>
    </div>
  );
}

function Th({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <th
      className={cn(
        "px-2.5 py-2 text-right text-[0.68rem] uppercase tracking-wide font-semibold text-ink-3 whitespace-nowrap",
        className,
      )}
    >
      {children}
    </th>
  );
}

function Td({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <td className={cn("px-2.5 py-2 text-right text-sm tabular-nums whitespace-nowrap", className)}>
      {children}
    </td>
  );
}
