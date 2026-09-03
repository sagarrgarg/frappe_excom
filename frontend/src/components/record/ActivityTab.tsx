import { useMemo } from "react";
import { Loader2, History, ArrowRightLeft, Pencil, Lock, UserCheck, MessageSquare, CheckCircle2 } from "lucide-react";
import { useActivity, type ActivityItem } from "../../hooks/useActivity";
import type { RecordRef } from "../../hooks/useRecordLinks";
import { EmptyState } from "../primitives";
import type { FeedMessage } from "../../hooks/useIdentityMessages";
import { formatServerShortDateTime, parseFrappeDateTime } from "../../utils/datetime";

type Row = { id: string; at: Date; icon: React.ReactNode; title: string; detail?: string };

/** Activity tab — Version + transfer log (server) merged client-side with thread system messages (internal notes). */
export function ActivityTab({ record, threadIds, messages }: { record: RecordRef | null; threadIds: string[]; messages: FeedMessage[] }) {
  const { items, isLoading } = useActivity(record, threadIds);
  const rows = useMemo<Row[]>(() => {
    const out: Row[] = [];
    for (const it of items as ActivityItem[]) {
      if (it.kind === "comment") { out.push({ id: it.id, at: parseFrappeDateTime(it.at), icon: <MessageSquare />, title: `${it.by} commented on ${record?.doctype || "record"}`, detail: it.text }); continue; }
      if (it.kind === "closure") { out.push({ id: it.id, at: parseFrappeDateTime(it.at), icon: <CheckCircle2 />, title: `${it.by} closed · ${it.outcome}`, detail: it.reason || undefined }); continue; }
      if (it.kind === "version") {
        const changed = it.changed.map((c) => `${c.field}: ${fmt(c.old)} → ${fmt(c.new)}`).join("; ");
        if (!changed) continue;
        out.push({ id: it.id, at: parseFrappeDateTime(it.at), icon: <Pencil />, title: `${it.by} updated ${record?.doctype || "record"}`, detail: changed });
      } else {
        out.push({ id: it.id, at: parseFrappeDateTime(it.at), icon: <ArrowRightLeft />, title: `${it.by} transferred ${it.from_team ? `from ${it.from_team} ` : ""}to ${it.to_team}`, detail: it.note || undefined });
      }
    }
    for (const m of messages) {
      if (m.isInternal) out.push({ id: `note-${m.id}`, at: m.timestamp, icon: <Lock />, title: `${m.sentBy?.name || "Someone"} added an internal note`, detail: m.content });
    }
    out.sort((a, b) => b.at.getTime() - a.at.getTime());
    return out;
  }, [items, messages, record]);

  if (isLoading && rows.length === 0) return <div className="flex justify-center py-8 text-ink-3"><Loader2 className="size-5 animate-spin" /></div>;
  if (rows.length === 0) return <EmptyState icon={<History />} title="No activity yet" hint="Comments and field changes on the linked record, transfers, closures and internal notes appear here." compact />;
  return (
    <ol className="divide-y divide-border">
      {rows.map((r) => (
        <li key={r.id} className="flex gap-2 px-3 py-2 min-w-0">
          <span className="mt-0.5 size-6 rounded-full bg-surface-sunken text-ink-3 flex items-center justify-center shrink-0 [&_svg]:size-3.5">{r.icon}</span>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 min-w-0"><p className="text-sm text-ink-1 truncate">{r.title}</p><span className="ml-auto text-xs text-ink-3 tabular-nums shrink-0">{formatServerShortDateTime(r.at)}</span></div>
            {r.detail && <p className="text-xs text-ink-2 break-words mt-0.5 line-clamp-3">{r.detail}</p>}
          </div>
        </li>
      ))}
    </ol>
  );
}

function fmt(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

export const ActivityIcon = UserCheck;
