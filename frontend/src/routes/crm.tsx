import { useMemo, useState } from "react";
import { useFrappeGetCall } from "@/lib/api";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Sun, Inbox, KanbanSquare, Loader2, MessageSquare, ExternalLink, Filter, Clock, AlertTriangle, CheckSquare, X, ListTodo } from "lucide-react";
import { currentUser } from "../lib/ui-flag";
import { PageFrame } from "../components/shell/PageFrame";
import { NewLeadDialog } from "../components/crm/NewLeadDialog";
import { Plus } from "lucide-react";
import { useInbox } from "../components/shell/InboxProvider";
import { Row, Avatar, Chip, Badge, EmptyState, Button, Select, Input, SegmentedControl } from "../components/primitives";
import { GateChips } from "../components/crm/GateChips";
import { StagePicker } from "../components/crm/StagePicker";
import { useCrmActions, useCrmOptions } from "../hooks/useCrm";
import { deskUrl } from "../hooks/useRecordLinks";
import { channelMeta } from "../lib/channels";
import { cn } from "../components/ui/utils";
import { formatServerShortDateTime, parseFrappeDateTime } from "../utils/datetime";

const M = "excom.excom.api.crm";

function age(from?: string): string {
  if (!from) return "";
  const s = (Date.now() - parseFrappeDateTime(from).getTime()) / 1000;
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))}m`;
  if (s < 86400) return `${Math.round(s / 3600)}h`;
  return `${Math.round(s / 86400)}d`;
}

/**
 * Shared row for leads/opportunities: title · type · stage · thread age · open conversation.
 *
 * Same rule as the inbox row: only what is true and unusual. "Unclassified", "Unassigned" and an
 * SLA chip used to be on nearly every lead; the stage tabs already say which leads are not
 * classified, and an owner is shown when it is somebody else. SLA is a red pip, unread is bold.
 */
function CrmRow({ r, kind, extra, onOpenThread }: { r: any; kind: "lead" | "opp"; extra?: React.ReactNode; onOpenThread: (oi: string, thread?: string) => void }) {
  const title = kind === "lead" ? r.lead_name || r.company_name || r.name : r.customer_name || r.party_name || r.name;
  const ch = r._thread_channel ? channelMeta(r._thread_channel) : null;
  return (
    <Row dense={false} className="border-b border-border" onClick={() => r.omni_identity && onOpenThread(r.omni_identity, r._thread)} interactive={Boolean(r.omni_identity)}>
      <Avatar name={title} size={32} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5 min-w-0">
          <span className={cn("text-sm text-ink-1 truncate", r._thread_unread > 0 ? "font-semibold" : "font-medium")} title={r._thread_unread > 0 ? `${r._thread_unread} unread` : undefined}>{title}</span>
          {r.customer_type && <Chip size="sm" accent="teal" label={r.customer_type} />}
          {ch && <ch.icon className={cn("size-3.5 shrink-0", `text-crayon-${ch.accent}-base`)} />}
          {r._sla_breached && <span className="size-2 rounded-full bg-crayon-rose-base shrink-0" title="SLA breached — no reply in time" aria-label="SLA breached" />}
          <span className="ml-auto text-xs text-ink-3 tabular-nums shrink-0">{age(r.creation || r.stage_entered_at)}</span>
        </div>
        <div className="flex items-center gap-1.5 mt-0.5 min-w-0 text-xs text-ink-3">
          {kind === "opp" ? <span className="truncate">{r.pipeline_stage}{r.opportunity_amount ? ` · ${r.currency || ""} ${Number(r.opportunity_amount).toLocaleString("en-IN")}` : ""}</span> : <span className="truncate">{r.intake_stage || "Captured"}{r.intake_source ? ` · ${r.intake_source}` : r.first_touch_channel ? ` · ${r.first_touch_channel}` : ""}</span>}

          {r.next_action_at && <span className={cn("shrink-0 tabular-nums", parseFrappeDateTime(r.next_action_at) < new Date() ? "text-crayon-rose-text" : "")}><Clock className="inline size-3 mr-0.5" />{formatServerShortDateTime(parseFrappeDateTime(r.next_action_at))}</span>}
          {(r.lead_owner || r.opportunity_owner) && (r.lead_owner || r.opportunity_owner) !== currentUser() && <span className="truncate ml-auto shrink-0 max-w-[40%]" title={`Owner: ${r.lead_owner || r.opportunity_owner}`}>{(r.lead_owner || r.opportunity_owner).split("@")[0]}</span>}
        </div>
        {extra}
      </div>
    </Row>
  );
}

function useOpenThread() {
  const { openRecord } = useInbox();
  return (oi: string, thread?: string) => openRecord(oi, thread);
}

/**
 * "Needs attention": what used to be the Today page — overdue next actions, SLA-breaching intake,
 * today's actions, unassigned enquiries. It lived as a page of its own beside Leads and the inbox
 * and listed largely the same people a third time, so it is now the first tab of Leads.
 */
function AttentionList({ onEmpty }: { onEmpty?: () => void }) {
  const { data, isLoading } = useFrappeGetCall<{ message: { overdue: any[]; sla: any[]; today: any[]; unassigned: any[] } }>(`${M}.get_today`, undefined, undefined, { refreshInterval: 60_000 });
  const open = useOpenThread();
  const d = data?.message;
  const sections: [string, any[], "opp" | "lead", React.ReactNode, string][] = d ? [
    ["Overdue follow-ups", d.overdue, "opp", <AlertTriangle />, "text-crayon-rose-text"],
    ["SLA at risk", d.sla, "lead", <Clock />, "text-crayon-amber-text"],
    ["Due today", d.today, "opp", <Sun />, ""],
    ["Unassigned", d.unassigned, "lead", <Inbox />, ""],
  ] : [];
  if (isLoading && !d) return <div className="flex justify-center py-10 text-ink-3"><Loader2 className="size-5 animate-spin" /></div>;
  if (sections.every(([, rows]) => !rows.length)) {
    return <EmptyState icon={<CheckSquare />} title="Nothing needs you right now" hint="No overdue follow-ups, SLA risks or unassigned enquiries." action={onEmpty ? <Button size="sm" onClick={onEmpty}>See all leads</Button> : undefined} />;
  }
  return (
    <>
      {sections.map(([label, rows, kind, icon, tone]) => rows.length ? (
        <section key={label}>
          <h3 className={cn("sticky top-0 z-10 flex items-center gap-2 px-3 h-8 text-xs bg-surface-sunken border-b border-border [&_svg]:size-3.5", tone || "text-ink-3")}>
            {icon}<span className="font-medium">{label}</span><span className="text-ink-3 tabular-nums">{rows.length}</span>
          </h3>
          {rows.map((r) => <CrmRow key={r.name} r={r} kind={kind} onOpenThread={open} />)}
        </section>
      ) : null)}
    </>
  );
}


type LeadFilters = Record<string, string>;

/** The filter bar over the leads queue. Every dropdown is built from values the leads actually
 *  carry, so a country nobody has a lead in is never offered. */
function LeadFilterBar({ opts, value, onChange }: { opts: any; value: LeadFilters; onChange: (f: LeadFilters) => void }) {
  const [open, setOpen] = useState(false);
  const set = (k: string, v: string) => onChange({ ...value, [k]: v });
  const active = Object.entries(value).filter(([k, v]) => v && k !== "intake_stage");
  const clear = () => onChange(value.intake_stage ? { intake_stage: value.intake_stage } : {});

  const selects: { key: string; label: string; options: { value: string; label: string }[] }[] = [
    { key: "country", label: "Country", options: (opts.countries ?? []).map((c: string) => ({ value: c, label: c })) },
    { key: "excom_team", label: "Team", options: (opts.teams ?? []).map((t: string) => ({ value: t, label: t })) },
    { key: "customer_type", label: "Type", options: (opts.customer_types ?? []).map((t: string) => ({ value: t, label: t })) },
    { key: "intake_source", label: "Source", options: (opts.sources ?? []).map((t: string) => ({ value: t, label: t })) },
    { key: "lead_owner", label: "Owner", options: (opts.owners ?? []) },
    { key: "first_touch_channel", label: "Channel", options: (opts.channels ?? []).map((t: string) => ({ value: t, label: t })) },
    { key: "territory", label: "Territory", options: (opts.territories ?? []).map((t: string) => ({ value: t, label: t })) },
    { key: "status", label: "Status", options: (opts.statuses ?? []).map((t: string) => ({ value: t, label: t })) },
  ];

  return (
    <div className="border-b border-border bg-surface-sunken px-2 py-1.5 flex flex-col gap-1.5">
      <div className="flex items-center gap-1.5 min-w-0">
        <Input
          value={value.q ?? ""}
          onChange={(e) => set("q", e.target.value)}
          placeholder="Search name, company, email or phone"
          className="flex-1 min-w-0 max-w-[22rem]"
          aria-label="Search leads"
        />
        <Button size="sm" variant={open || active.length ? "primary" : "ghost"} onClick={() => setOpen((o) => !o)}>
          <Filter />Filters{active.length ? ` (${active.length})` : ""}
        </Button>
        {active.length > 0 && (
          <Button size="sm" variant="ghost" onClick={clear} aria-label="Clear filters"><X />Clear</Button>
        )}
      </div>
      {open && (
        <div className="grid gap-1.5 grid-cols-[repeat(auto-fill,minmax(9rem,1fr))]">
          {selects.map((s) => (
            <Select key={s.key} value={value[s.key] ?? ""} onChange={(e) => set(s.key, e.target.value)} aria-label={s.label}>
              <option value="">{s.label}: any</option>
              {s.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </Select>
          ))}
          <Input type="date" value={value.from_date ?? ""} onChange={(e) => set("from_date", e.target.value)} aria-label="From date" />
          <Input type="date" value={value.to_date ?? ""} onChange={(e) => set("to_date", e.target.value)} aria-label="To date" />
        </div>
      )}
    </div>
  );
}

/** /intake — S1–S5 queue by intake_stage, SLA pip, bulk classify, one-click open conversation. */
export function IntakeRoute() {
  const opts = useCrmOptions();
  const [sp, setSp] = useSearchParams();
  const show: "attention" | "all" = sp.get("show") === "all" ? "all" : "attention";
  const setShow = (v: "attention" | "all") => { const n = new URLSearchParams(sp); if (v === "attention") n.delete("show"); else n.set("show", v); setSp(n, { replace: true }); };
  const [filters, setFilters] = useState<LeadFilters>({});
  const stage = filters.intake_stage ?? "";
  const setStage = (s: string) => setFilters((f) => ({ ...f, intake_stage: s }));
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [ct, setCt] = useState("");
  const query = useMemo(() => Object.fromEntries(Object.entries(filters).filter(([, v]) => v)), [filters]);
  const { data, isLoading, mutate } = useFrappeGetCall<{ message: any[] }>(`${M}.get_intake_queue`, { filters: JSON.stringify(query) }, undefined, { refreshInterval: 60_000 });
  const actions = useCrmActions(() => { mutate(); setSelected(new Set()); });
  const open = useOpenThread();
  const rows = data?.message ?? [];
  const toggle = (n: string) => setSelected((s) => { const x = new Set(s); x.has(n) ? x.delete(n) : x.add(n); return x; });
  const [newLead, setNewLead] = useState(false);
  return (
    <PageFrame title="Leads" icon={<ListTodo />} className="!p-0" actions={<><Button size="sm" variant="primary" onClick={() => setNewLead(true)}><Plus />New lead</Button><NewLeadDialog open={newLead} onClose={() => setNewLead(false)} onCreated={() => mutate()} /></>}>
      <div className="-m-3">
        <SegmentedControl<"attention" | "all"> ariaLabel="Which leads" className="px-2 border-b border-border bg-surface" value={show} onChange={setShow}
          segments={[{ value: "attention", label: "Needs attention", icon: <AlertTriangle /> }, { value: "all", label: "All leads", icon: <ListTodo />, count: rows.length }]} />
        {show === "attention" ? <AttentionList onEmpty={() => setShow("all")} /> : <>
        <LeadFilterBar opts={opts} value={filters} onChange={setFilters} />
        <div className="sticky top-0 z-10 bg-surface-sunken border-b border-border px-2 py-1.5 flex items-center gap-2 min-w-0">
          <div className="chip-row flex-1 h-7">
            <Chip size="sm" label="All" accent={!stage ? "blue" : "neutral"} onClick={() => setStage("")} />
            {opts.intake_stages.map((s) => <Chip key={s} size="sm" label={s} accent={stage === s ? "blue" : "neutral"} onClick={() => setStage(s)} />)}
          </div>
          {selected.size > 0 && (
            <div className="flex items-center gap-1.5 shrink-0">
              <Select value={ct} onChange={(e) => setCt(e.target.value)} className="w-[150px]"><option value="">Classify as…</option>{opts.customer_types.map((t) => <option key={t}>{t}</option>)}</Select>
              <Button size="sm" variant="primary" disabled={!ct} onClick={() => actions.bulkClassify([...selected], ct)}>Apply to {selected.size}</Button>
            </div>
          )}
        </div>
        {isLoading && !rows.length ? <div className="flex justify-center py-10 text-ink-3"><Loader2 className="size-5 animate-spin" /></div> : rows.length === 0 ? <EmptyState icon={<Inbox />} title={Object.keys(query).length ? "No leads match these filters" : "Intake queue is empty"} hint={Object.keys(query).length ? "Clear a filter to widen the search." : "New enquiries from IndiaMART, TradeIndia, Meta lead ads and websites land here."} /> : (
          rows.map((r) => (
            <div key={r.name} className="group flex items-stretch min-w-0">
              {/* Selecting is for bulk classify: the box shows on hover or once something is picked, not on every row. */}
              <label className={cn("hidden pointer-fine:flex items-center px-2 border-b border-border shrink-0", selected.size === 0 && "[&>input]:opacity-0 group-hover:[&>input]:opacity-100 focus-within:[&>input]:opacity-100")}><input type="checkbox" className="size-4" aria-label={`Select ${r.lead_name || r.name}`} checked={selected.has(r.name)} onChange={() => toggle(r.name)} /></label>
              <div className="flex-1 min-w-0"><CrmRow r={r} kind="lead" onOpenThread={open} /></div>
            </div>
          ))
        )}
        </>}
      </div>
    </PageFrame>
  );
}

/** /pipeline — kanban on pipeline_stage per customer_type; phone gets a stage-picker list, not a drag board. */
export function PipelineRoute() {
  const opts = useCrmOptions();
  const { bp } = useInbox();
  const [ct, setCt] = useState<string>(() => { try { return localStorage.getItem("excom_pipeline_type") || ""; } catch { return ""; } });
  const type = ct || opts.customer_types[0] || "";
  const { data, isLoading, mutate } = useFrappeGetCall<{ message: { stages: string[]; columns: Record<string, any[]>; count: number } }>(type ? `${M}.get_pipeline` : null, type ? { customer_type: type } : undefined, undefined, { refreshInterval: 60_000 });
  const actions = useCrmActions(() => mutate());
  const open = useOpenThread();
  const d = data?.message;
  const phone = bp === "phone";
  const pick = (t: string) => { setCt(t); try { localStorage.setItem("excom_pipeline_type", t); } catch { /* ignore */ } };
  const stagesFor = (r: any) => ({ flags: r._gates || {}, stages: d?.stages || [], current: r.pipeline_stage, blocked: {} });
  const total = useMemo(() => Object.values(d?.columns || {}).flat().reduce((s: number, r: any) => s + Number(r.opportunity_amount || 0), 0), [d]);

  return (
    <PageFrame title="Pipeline" icon={<KanbanSquare />} className="!p-0" wide actions={<span className="text-xs text-ink-3 tabular-nums hidden tablet:inline">{d?.count ?? 0} open · ₹{total.toLocaleString("en-IN")}</span>}>
      <div className="-m-3 flex flex-col h-full min-h-0">
        <div className="shrink-0 bg-surface-sunken border-b border-border px-2 py-1.5"><div className="chip-row h-7">{opts.customer_types.map((t) => <Chip key={t} size="sm" label={t} accent={type === t ? "blue" : "neutral"} onClick={() => pick(t)} />)}</div></div>
        {isLoading && !d ? <div className="flex justify-center py-10 text-ink-3"><Loader2 className="size-5 animate-spin" /></div> : !d || d.count === 0 ? <EmptyState icon={<KanbanSquare />} title={`No open ${type} opportunities`} hint="Qualify a lead to start a deal here." /> : phone ? (
          <div>
            {d.stages.map((s) => (d.columns[s] || []).length ? (
              <section key={s}><h3 className="sticky top-0 z-10 px-3 h-8 flex items-center text-xs text-ink-3 bg-surface-sunken border-b border-border">{s}<Badge accent="neutral" count={d.columns[s].length} className="ml-2" /></h3>
                {d.columns[s].map((r) => <CrmRow key={r.name} r={r} kind="opp" onOpenThread={open} extra={<div className="chip-row mt-1 h-6"><StagePicker compact gate={stagesFor(r)} onSelect={(st, n) => actions.setStage(r.name, st, n)} onOverride={(g, reason) => actions.overrideGate(r.name, g, reason)} /><GateChips flags={r._gates || {}} /></div>} />)}
              </section>
            ) : null)}
          </div>
        ) : (
          <div className="flex-1 min-h-0 overflow-x-auto overflow-y-hidden">
            <div className="flex gap-2 p-2 h-full min-w-max">
              {d.stages.map((s) => (
                <div key={s} className="w-[260px] shrink-0 flex flex-col min-h-0 rounded-lg bg-surface-sunken border border-border">
                  <div className="shrink-0 h-8 px-2 flex items-center gap-2 text-xs font-medium text-ink-2 border-b border-border"><span className="truncate">{s}</span><Badge accent="neutral" count={(d.columns[s] || []).length} /><span className="ml-auto text-ink-3 tabular-nums">{opts.stages[s]?.probability ?? ""}%</span></div>
                  <div className="flex-1 min-h-0 overflow-y-auto p-1.5 space-y-1.5">
                    {(d.columns[s] || []).map((r) => (
                      <div key={r.name} className="rounded-md bg-surface border border-border p-2 min-w-0 max-h-[132px]">
                        <div className="flex items-center gap-1.5 min-w-0"><button type="button" className="text-sm font-medium text-ink-1 truncate text-left hover:underline" onClick={() => r.omni_identity && open(r.omni_identity, r._thread)}>{r.customer_name || r.party_name || r.name}</button><a href={deskUrl("Opportunity", r.name)} target="_blank" rel="noreferrer" className="ml-auto text-ink-muted hover:text-ink-2 shrink-0"><ExternalLink className="size-3.5" /></a></div>
                        <div className="flex items-center gap-1.5 mt-0.5 text-xs text-ink-3 tabular-nums min-w-0"><span className="truncate">{r.currency || ""} {Number(r.opportunity_amount || 0).toLocaleString("en-IN")}</span><span className="ml-auto shrink-0">{age(r.stage_entered_at)} here</span>{r._thread_last_inbound_at && <span className="shrink-0 inline-flex items-center gap-0.5"><MessageSquare className="size-3" />{age(r._thread_last_inbound_at)}</span>}</div>
                        <div className="chip-row mt-1.5 h-6"><StagePicker compact gate={stagesFor(r)} onSelect={(st, n) => actions.setStage(r.name, st, n)} onOverride={(g, reason) => actions.overrideGate(r.name, g, reason)} /><GateChips flags={r._gates || {}} />{r.opportunity_owner && <Avatar name={r.opportunity_owner} size={20} />}</div>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </PageFrame>
  );
}

export const CrmIcons = { Filter, SegmentedControl };
