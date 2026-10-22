import { useState } from "react";
import { AlertCircle, CheckCircle2, CircleDashed, ExternalLink, Loader2, RefreshCw, TriangleAlert } from "lucide-react";
import { useFrappePostCall } from "frappe-react-sdk";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { useFrappeGetCall } from "@/lib/api";
import { Button, EmptyState } from "../primitives";
import { serverMessage } from "../admin/util";
import { cn } from "../ui/utils";
import { ago } from "./format";

type Status = "ok" | "warn" | "error" | "off";
interface Item { key: string; label: string; status: Status; detail: string; hint: string; link: string | null; count?: number; action?: { method: string; args: Record<string, unknown>; label: string } }
interface Section { key: string; title: string; description: string; status: Status; items: Item[] }
interface Health { sections: Section[]; counts: Record<Status, number>; checked_at: string }

const ICON: Record<Status, React.ReactNode> = {
  ok: <CheckCircle2 className="size-4 text-crayon-green-text" aria-label="OK" />,
  warn: <TriangleAlert className="size-4 text-crayon-amber-text" aria-label="Needs attention" />,
  error: <AlertCircle className="size-4 text-crayon-rose-text" aria-label="Broken" />,
  off: <CircleDashed className="size-4 text-ink-3" aria-label="Not set up" />,
};
const LABEL: Record<Status, string> = { ok: "OK", warn: "Needs attention", error: "Broken", off: "Not set up" };

function ItemLink({ href }: { href: string }) {
  // Excom routes stay in the app; Desk links open in a new tab.
  if (href.startsWith("/excom/")) return <Link to={href.replace(/^\/excom/, "")} className="text-xs text-crayon-blue-text hover:underline shrink-0">Open</Link>;
  return (
    <a href={href} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 text-xs text-crayon-blue-text hover:underline shrink-0">
      Desk <ExternalLink className="size-3" />
    </a>
  );
}

function Row({ it, onDone }: { it: Item; onDone: () => void }) {
  const { call, loading } = useFrappePostCall(it.action?.method ?? "frappe.ping");
  const run = async () => {
    try {
      const r = (await call(it.action!.args)) as { message?: { ok?: boolean; error?: string } };
      if (r?.message && r.message.ok === false) toast.error(r.message.error || "Test failed");
      else toast.success("Checked");
      onDone();
    } catch (e) {
      toast.error(serverMessage(e) || "Test failed");
      onDone();
    }
  };
  return (
    <li className="flex items-start gap-2.5 px-3 py-2 min-w-0">
      <span className="mt-0.5 shrink-0">{ICON[it.status]}</span>
      <div className="min-w-0 flex-1">
        <p className="text-sm text-ink-1 truncate" title={it.label}>{it.label}</p>
        {it.detail && <p className="text-xs text-ink-2">{it.detail}</p>}
        {it.hint && <p className={cn("text-xs mt-0.5", it.status === "error" ? "text-crayon-rose-text" : "text-ink-3")}>{it.hint}</p>}
      </div>
      {it.action && (
        <Button size="sm" variant="ghost" onClick={run} disabled={loading}>
          {loading ? <Loader2 className="animate-spin" /> : <RefreshCw />}
          {it.action.label}
        </Button>
      )}
      {it.link && <ItemLink href={it.link} />}
    </li>
  );
}

/** Every link the Marketing OS depends on, with what to fix. Admins and connection managers only. */
export function ConnectionsTab() {
  const { data, isLoading, error, mutate, isValidating } = useFrappeGetCall<{ message: Health }>("excom.excom_ads.connections.health", undefined, "ads-connections", { revalidateOnFocus: true });
  const [onlyProblems, setOnlyProblems] = useState(false);
  const h = data?.message;

  if (isLoading && !h) return <div className="flex justify-center py-16"><Loader2 className="size-5 animate-spin text-ink-3" /></div>;
  if (error) return <EmptyState title="Could not check the connections" hint={serverMessage(error) || "You need the Manage Connections right."} />;
  if (!h) return null;
  const problems = h.counts.error + h.counts.warn;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3 rounded-lg border border-border p-3">
        {(["error", "warn", "ok", "off"] as Status[]).map((s) => (
          <span key={s} className="inline-flex items-center gap-1.5 text-sm text-ink-2">{ICON[s]}<span className="tabular-nums text-ink-1">{h.counts[s]}</span> {LABEL[s]}</span>
        ))}
        <span className="text-xs text-ink-3 ml-auto">Checked {ago(h.checked_at)}</span>
        <Button size="sm" variant={onlyProblems ? "subtle" : "ghost"} onClick={() => setOnlyProblems((v) => !v)} aria-pressed={onlyProblems} disabled={!problems && !onlyProblems}>
          {onlyProblems ? "Show everything" : `Only problems (${problems})`}
        </Button>
        <Button size="sm" variant="ghost" onClick={() => mutate()} disabled={isValidating}>{isValidating ? <Loader2 className="animate-spin" /> : <RefreshCw />}Refresh</Button>
      </div>
      <div className="grid gap-3 laptop:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        {h.sections.map((sec) => {
          const items = onlyProblems ? sec.items.filter((i) => i.status === "error" || i.status === "warn") : sec.items;
          if (!items.length) return null;
          return (
            <section key={sec.key} className={cn("rounded-lg border min-w-0", sec.status === "error" ? "border-crayon-rose-base/50" : sec.status === "warn" ? "border-crayon-amber-base/50" : "border-border")}>
              <header className="flex items-start gap-2 px-3 pt-3 pb-1">
                <span className="mt-0.5">{ICON[sec.status]}</span>
                <div className="min-w-0">
                  <h2 className="text-sm text-ink-1">{sec.title}</h2>
                  {sec.description && <p className="text-xs text-ink-3">{sec.description}</p>}
                </div>
              </header>
              <ul className="divide-y divide-border">{items.map((it) => <Row key={it.key} it={it} onDone={() => mutate()} />)}</ul>
            </section>
          );
        })}
      </div>
    </div>
  );
}
