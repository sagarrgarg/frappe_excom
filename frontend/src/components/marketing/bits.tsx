import { AlertTriangle, ArrowDownRight, ArrowUpRight, Info } from "lucide-react";
import { Badge, type Accent } from "../primitives";
import { cn } from "../ui/utils";
import { ago, NA } from "./format";
import type { Freshness } from "./types";

export const STATUS_ACCENT: Record<string, Accent> = {
  Active: "green",
  Paused: "neutral",
  "In Review": "amber",
  Rejected: "rose",
  Error: "rose",
  "Billing Issue": "rose",
  Archived: "neutral",
  Deleted: "neutral",
  Unknown: "neutral",
  Disabled: "rose",
  Unsettled: "rose",
  "Pending Review": "amber",
  "Grace Period": "amber",
  "Pending Closure": "amber",
  Closed: "neutral",
};

export const SEVERITY_ACCENT: Record<string, Accent> = { Info: "blue", Medium: "amber", High: "rose", Critical: "rose" };

export function StatusBadge({ status }: { status: string | null | undefined }) {
  return <Badge accent={STATUS_ACCENT[status ?? ""] ?? "neutral"}>{status || "Unknown"}</Badge>;
}

/** Where a number comes from. Platform and Verified figures are never added together. */
export function SourceBadge({ source }: { source: "Platform" | "Verified" }) {
  return (
    <span title={source === "Platform" ? "As reported by the ad platform, under its own attribution" : "Computed from ERPNext and CRM records"} className={cn("text-xs px-1.5 rounded border", source === "Platform" ? "border-crayon-blue-base/40 text-crayon-blue-text" : "border-crayon-green-base/40 text-crayon-green-text")}>
      {source}
    </span>
  );
}

export function Delta({ value, goodWhenUp = true }: { value: number | null; goodWhenUp?: boolean }) {
  if (value === null || !Number.isFinite(value)) return <span className="text-xs text-ink-3">{NA}</span>;
  const up = value >= 0;
  const good = up === goodWhenUp;
  const Icon = up ? ArrowUpRight : ArrowDownRight;
  return (
    <span className={cn("inline-flex items-center gap-0.5 text-xs tabular-nums", good ? "text-crayon-green-text" : "text-crayon-rose-text")}>
      <Icon className="size-3" />
      {Math.abs(value * 100).toFixed(Math.abs(value) >= 0.1 ? 0 : 1)}%
    </span>
  );
}

export function Kpi({ label, value, sub, delta, goodWhenUp, source = "Platform", hint }: { label: string; value: string; sub?: React.ReactNode; delta?: number | null; goodWhenUp?: boolean; source?: "Platform" | "Verified"; hint?: string }) {
  return (
    <div className="rounded-lg border border-border bg-surface p-3 min-w-0" title={hint}>
      <div className="flex items-center justify-between gap-2 mb-1 min-w-0">
        <span className="text-xs text-ink-3 truncate">{label}</span>
        <SourceBadge source={source} />
      </div>
      <div className="flex items-baseline gap-2 min-w-0">
        <p className="text-lg text-ink-1 tabular-nums truncate">{value}</p>
        {delta !== undefined && <Delta value={delta} goodWhenUp={goodWhenUp} />}
      </div>
      {sub && <div className="text-xs text-ink-3 mt-0.5 truncate">{sub}</div>}
    </div>
  );
}

/** A figure is only as fresh as its oldest source. Stale or broken sources are said out loud. */
export function FreshnessBanner({ freshness }: { freshness: Freshness | undefined }) {
  if (!freshness) return null;
  const { stale, errors, as_of, stale_after_hours } = freshness;
  if (!stale.length && !errors.length) {
    return (
      <p className="flex items-center gap-1.5 text-xs text-ink-3">
        <Info className="size-3.5" /> Data as of {ago(as_of)}
      </p>
    );
  }
  return (
    <div className="flex items-start gap-2 rounded-md border border-crayon-amber-base/50 bg-crayon-amber-tint px-3 py-2 text-xs text-crayon-amber-text" role="status">
      <AlertTriangle className="size-4 shrink-0 mt-0.5" />
      <div className="min-w-0">
        {errors.length > 0 && <p>Not syncing: {errors.map((e) => `${e.account_name} (${e.error_class})`).join(", ")}.</p>}
        {stale.length > 0 && <p>Older than {stale_after_hours} h: {stale.map((s) => `${s.account_name} (${ago(s.last_metrics_sync)})`).join(", ")}. Figures for these accounts may be incomplete.</p>}
      </div>
    </div>
  );
}
