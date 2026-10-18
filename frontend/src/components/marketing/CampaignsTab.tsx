import { useState } from "react";
import { ChevronDown, ChevronRight, Loader2, Search } from "lucide-react";
import { useFrappeGetCall } from "@/lib/api";
import { Button, EmptyState, Input, Select } from "../primitives";
import { cn } from "../ui/utils";
import { SourceBadge, StatusBadge } from "./bits";
import { money, num, pct, ratio } from "./format";
import type { Explorer, ExplorerRow } from "./types";
import { useMarketingFilters } from "./useMarketingFilters";

type Level = "Campaign" | "Ad Group" | "Ad";
const CHILD: Record<Level, Level | null> = { Campaign: "Ad Group", "Ad Group": "Ad", Ad: null };
const PAGE = 50;
const STATUSES = ["Active", "Paused", "In Review", "Rejected", "Error", "Billing Issue", "Archived", "Deleted"];
const SORTS = [
  { value: "spend", label: "Spend" },
  { value: "impressions", label: "Impressions" },
  { value: "clicks", label: "Clicks" },
  { value: "leads", label: "Leads" },
  { value: "purchases", label: "Purchases" },
  { value: "purchase_value", label: "Purchase value" },
  { value: "title", label: "Name" },
];
const COLS = "grid-cols-[minmax(0,2.4fr)_minmax(0,0.9fr)_repeat(7,minmax(0,0.8fr))]";

function Cells({ r }: { r: ExplorerRow }) {
  const m = r.metrics;
  const c = m?.currency ?? r.currency ?? undefined;
  return (
    <>
      <span className="text-right tabular-nums">{m ? money(m.spend, c) : "—"}</span>
      <span className="text-right tabular-nums">{m ? num(m.impressions) : "—"}</span>
      <span className="text-right tabular-nums">{m ? num(m.clicks) : "—"}</span>
      <span className="text-right tabular-nums">{m ? pct(m.ctr) : "—"}</span>
      <span className="text-right tabular-nums">{m ? num(m.leads) : "—"}</span>
      <span className="text-right tabular-nums">{m ? money(m.cpl, c) : "—"}</span>
      <span className="text-right tabular-nums">{m ? ratio(m.roas) : "—"}</span>
    </>
  );
}

function Level({ level, parent, depth, query }: { level: Level; parent?: string; depth: number; query: { search: string; status: string; order_by: string } }) {
  const { apiParams } = useMarketingFilters();
  const [start, setStart] = useState(0);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const params = { ...apiParams, level, parent, start, page_length: PAGE, ...(depth === 0 ? { search: query.search || undefined, status: query.status || undefined } : {}), order_by: query.order_by };
  const { data, isLoading, error } = useFrappeGetCall<{ message: Explorer }>("excom.excom_ads.dashboard.explorer", params, `ads-explorer-${JSON.stringify(params)}`, { keepPreviousData: true });
  const ex = data?.message;
  const child = CHILD[level];

  if (isLoading && !ex) return <div className="py-3 flex justify-center"><Loader2 className="size-4 animate-spin text-ink-3" /></div>;
  if (error) return <p className="text-xs text-crayon-rose-text px-3 py-2">Could not load {level.toLowerCase()}s.</p>;
  if (!ex || !ex.rows.length) return depth === 0 ? <EmptyState compact title="Nothing matches" hint="Change the filters, or run a structure sync on the account." /> : <p className="text-xs text-ink-3 py-1.5" style={{ paddingLeft: 12 + depth * 20 }}>No {level === "Ad Group" ? "ad groups (Performance Max and some campaign types have none)" : `${level.toLowerCase()}s`}.</p>;

  return (
    <div>
      {ex.rows.map((r) => (
        <div key={r.name}>
          <div className={cn("grid items-center gap-2 px-3 py-1.5 border-t border-border text-sm min-w-[900px] hover:bg-surface-hover", COLS, r.not_returned_since && "opacity-60")}>
            <div className="flex items-center gap-1 min-w-0" style={{ paddingLeft: depth * 20 }}>
              {child ? (
                <button className="shrink-0 text-ink-3 hover:text-ink-1" aria-label={open[r.name] ? "Collapse" : "Expand"} aria-expanded={!!open[r.name]} onClick={() => setOpen((o) => ({ ...o, [r.name]: !o[r.name] }))}>
                  {open[r.name] ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
                </button>
              ) : <span className="w-4 shrink-0" />}
              <span className="truncate" title={`${r.title} · ${r.provider} ${r.external_id}${r.not_returned_since ? " · no longer returned by the platform" : ""}`}>{r.title}</span>
              {r.objective && <span className="text-xs text-ink-3 truncate hidden laptop:inline">{r.objective}</span>}
            </div>
            <span className="min-w-0"><StatusBadge status={r.status} /></span>
            <Cells r={r} />
          </div>
          {child && open[r.name] && <Level level={child} parent={r.name} depth={depth + 1} query={query} />}
        </div>
      ))}
      {(start > 0 || start + PAGE < ex.total) && (
        <div className="flex items-center gap-2 px-3 py-1.5 border-t border-border text-xs text-ink-3" style={{ paddingLeft: 12 + depth * 20 }}>
          <span>{start + 1}–{Math.min(start + PAGE, ex.total)} of {ex.total}</span>
          <Button size="sm" variant="ghost" disabled={start === 0} onClick={() => setStart(Math.max(0, start - PAGE))}>Previous</Button>
          <Button size="sm" variant="ghost" disabled={start + PAGE >= ex.total} onClick={() => setStart(start + PAGE)}>Next</Button>
        </div>
      )}
    </div>
  );
}

export function CampaignsTab() {
  const [search, setSearch] = useState("");
  const [typed, setTyped] = useState("");
  const [status, setStatus] = useState("");
  const [orderBy, setOrderBy] = useState("spend");
  const query = { search, status, order_by: orderBy };
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-1.5">
        <form className="relative" onSubmit={(e) => { e.preventDefault(); setSearch(typed.trim()); }}>
          <Search className="size-3.5 absolute left-2 top-1/2 -translate-y-1/2 text-ink-3" />
          <Input className="h-7 text-xs pl-7 w-[220px]" placeholder="Search campaigns or IDs" value={typed} onChange={(e) => setTyped(e.target.value)} onBlur={() => setSearch(typed.trim())} aria-label="Search campaigns" />
        </form>
        <Select className="h-7 text-xs w-[140px]" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
          <option value="">Any status</option>
          {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
        </Select>
        <Select className="h-7 text-xs w-[150px]" value={orderBy} onChange={(e) => setOrderBy(e.target.value)} aria-label="Sort by">
          {SORTS.map((s) => <option key={s.value} value={s.value}>Sort: {s.label}</option>)}
        </Select>
        <span className="ml-auto"><SourceBadge source="Platform" /></span>
      </div>
      <div className="rounded-lg border border-border overflow-x-auto">
        <div className={cn("grid gap-2 px-3 py-1.5 text-xs text-ink-3 min-w-[900px] bg-surface-sunken", COLS)}>
          <span>Campaign → ad group → ad</span><span>Status</span>
          <span className="text-right">Spend</span><span className="text-right">Impr.</span><span className="text-right">Clicks</span><span className="text-right">CTR</span><span className="text-right">Leads</span><span className="text-right">CPL</span><span className="text-right">ROAS</span>
        </div>
        <Level key={JSON.stringify(query)} level="Campaign" depth={0} query={query} />
      </div>
      <p className="text-xs text-ink-3">Read-only mirror of the platforms. Changes (pause, budgets) arrive in release 2 through approved action requests.</p>
    </div>
  );
}
