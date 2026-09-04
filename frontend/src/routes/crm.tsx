import { useMemo, useState } from "react";
import { useFrappeGetCall } from "@/lib/api";
import { useNavigate } from "react-router-dom";
import { Sun, Inbox, KanbanSquare, Loader2, MessageSquare, ExternalLink, Filter, Clock, AlertTriangle, CheckSquare } from "lucide-react";
import { PageFrame } from "../components/shell/PageFrame";
import { NewLeadDialog } from "../components/crm/NewLeadDialog";
import { Plus } from "lucide-react";
import { useInbox } from "../components/shell/InboxProvider";
import { Row, Avatar, Chip, Badge, EmptyState, Button, Select, SegmentedControl } from "../components/primitives";
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

/** Shared row for leads/opportunities: title · type · stage · thread age · open conversation. */
function CrmRow({ r, kind, extra, onOpenThread }: { r: any; kind: "lead" | "opp"; extra?: React.ReactNode; onOpenThread: (oi: string, thread?: string) => void }) {
  const title = kind === "lead" ? r.lead_name || r.company_name || r.name : r.customer_name || r.party_name || r.name;
  const ch = r._thread_channel ? channelMeta(r._thread_channel) : null;
  return (
    <Row dense={false} className="border-b border-border" onClick={() => r.omni_identity && onOpenThread(r.omni_identity, r._thread)} interactive={Boolean(r.omni_identity)}>
      <Avatar name={title} size={32} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5 min-w-0">
          <span className="text-sm font-medium text-ink-1 truncate">{title}</span>
          {r.customer_type ? <Chip size="sm" accent="teal" label={r.customer_type} /> : kind === "lead" ? <Chip size="sm" accent="amber" label="Unclassified" /> : null}
          {ch && <ch.icon className={cn("size-3.5 shrink-0", `text-crayon-${ch.accent}-base`)} />}
          {r._thread_unread > 0 && <Badge solid count={r._thread_unread} />}
          <span className="ml-auto text-xs text-ink-3 tabular-nums shrink-0">{age(r.creation || r.stage_entered_at)}</span>
        </div>
        <div className="flex items-center gap-1.5 mt-0.5 min-w-0 text-xs text-ink-3">
          {kind === "opp" ? <span className="truncate">{r.pipeline_stage}{r.opportunity_amount ? ` · ${r.currency || ""} ${Number(r.opportunity_amount).toLocaleString("en-IN")}` : ""}</span> : <span className="truncate">{r.intake_stage || "Captured"}{r.intake_source ? ` · ${r.intake_source}` : r.first_touch_channel ? ` · ${r.first_touch_channel}` : ""}</span>}
          {r._sla_breached && <Chip size="sm" accent="rose" icon={<AlertTriangle />} label="SLA" />}
          {r.next_action_at && <span className={cn("shrink-0 tabular-nums", parseFrappeDateTime(r.next_action_at) < new Date() ? "text-crayon-rose-text" : "")}><Clock className="inline size-3 mr-0.5" />{formatServerShortDateTime(parseFrappeDateTime(r.next_action_at))}</span>}
          {r.lead_owner || r.opportunity_owner ? <span className="truncate ml-auto">{(r.lead_owner || r.opportunity_owner).split("@")[0]}</span> : <span className="ml-auto text-crayon-amber-text">Unassigned</span>}
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

/** /today — overdue next actions, SLA-breaching intake, today's actions, unassigned for my teams. */
export function TodayRoute() {
  const { data, isLoading, mutate } = useFrappeGetCall<{ message: { overdue: any[]; sla: any[]; today: any[]; unassigned: any[] } }>(`${M}.get_today`, undefined, undefined, { refreshInterval: 60_000 });
  const open = useOpenThread();
  const d = data?.message;
  const sections: [string, any[], "opp" | "lead", React.ReactNode][] = d ? [["Overdue", d.overdue, "opp", <AlertTriangle />], ["SLA at risk", d.sla, "lead", <Clock />], ["Today", d.today, "opp", <Sun />], ["Unassigned", d.unassigned, "lead", <Inbox />]] : [];
  return (
    <PageFrame title="Today" icon={<Sun />} className="!p-0" actions={<Button size="sm" variant="ghost" onClick={() => mutate()}>Refresh</Button>}>
      <div className="-m-3">
        {isLoading && !d ? <div className="flex justify-center py-10 text-ink-3"><Loader2 className="size-5 animate-spin" /></div> : sections.every(([, rows]) => !rows.length) ? <EmptyState icon={<CheckSquare />} title="Nothing due" hint="No overdue actions, SLA risks or unassigned enquiries right now." /> : (
          sections.map(([label, rows, kind, icon]) => rows.length ? (
            <section key={label}>
              <h3 className="sticky top-0 z-10 flex items-center gap-2 px-3 h-8 text-xs text-ink-3 bg-surface-sunken border-b border-border [&_svg]:size-3.5">{icon}{label}<Badge accent={label === "Overdue" ? "rose" : label === "SLA at risk" ? "amber" : "neutral"} count={rows.length} /></h3>
              {rows.map((r) => <CrmRow key={r.name} r={r} kind={kind} onOpenThread={open} />)}
            </section>
          ) : null)
        )}
      </div>
    </PageFrame>
  );
}

/** /intake — S1–S5 queue by intake_stage, SLA pip, bulk classify, one-click open conversation. */
export function IntakeRoute() {
  const opts = useCrmOptions();
  const [stage, setStage] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [ct, setCt] = useState("");
  const { data, isLoading, mutate } = useFrappeGetCall<{ message: any[] }>(`${M}.get_intake_queue`, { filters: JSON.stringify(stage ? { intake_stage: stage } : {}) }, undefined, { refreshInterval: 60_000 });
  const actions = useCrmActions(() => { mutate(); setSelected(new Set()); });
  const open = useOpenThread();
  const rows = data?.message ?? [];
  const toggle = (n: string) => setSelected((s) => { const x = new Set(s); x.has(n) ? x.delete(n) : x.add(n); return x; });
  const [newLead, setNewLead] = useState(false);
  return (
    <PageFrame title="Leads" icon={<Inbox />} className="!p-0" actions={<><Badge count={rows.length} accent="neutral" /><Button size="sm" variant="primary" onClick={() => setNewLead(true)}><Plus />New lead</Button><NewLeadDialog open={newLead} onClose={() => setNewLead(false)} onCreated={() => mutate()} /></>}>
      <div className="-m-3">
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
        {isLoading && !rows.length ? <div className="flex justify-center py-10 text-ink-3"><Loader2 className="size-5 animate-spin" /></div> : rows.length === 0 ? <EmptyState icon={<Inbox />} title="Intake queue is empty" hint="New enquiries from IndiaMART, TradeIndia, Meta lead ads and websites land here." /> : (
          rows.map((r) => (
            <div key={r.name} className="flex items-stretch min-w-0">
              <label className="flex items-center px-2 border-b border-border shrink-0"><input type="checkbox" className="size-4" checked={selected.has(r.name)} onChange={() => toggle(r.name)} /></label>
              <div className="flex-1 min-w-0"><CrmRow r={r} kind="lead" onOpenThread={open} /></div>
            </div>
          ))
        )}
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
