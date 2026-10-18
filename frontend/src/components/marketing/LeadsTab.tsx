import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ExternalLink, Loader2, MessageSquare, Radio } from "lucide-react";
import { useFrappeGetCall } from "@/lib/api";
import { deskUrl } from "../../hooks/useRecordLinks";
import { Badge, Button, EmptyState, type Accent } from "../primitives";
import { cn } from "../ui/utils";
import { Kpi, SourceBadge } from "./bits";
import { money, NA, num } from "./format";
import { useMarketingFilters } from "./useMarketingFilters";

/** Shapes returned by excom.excom_ads.leads_dashboard. null = not available, never zero. */
type SlaStatus = "Met" | "Missed" | "Breached" | "Pending" | "No SLA";

interface LeadRow {
  name: string;
  doctype: string;
  lead_name: string;
  company_name: string | null;
  status: string;
  lead_owner: string | null;
  creation: string;
  intake_source: string | null;
  omni_identity: string | null;
  ads_touch_source: string | null;
  ads_provider: string | null;
  account_name: string | null;
  ads_campaign_name: string | null;
  ads_ad_name: string | null;
  ads_is_test: number;
  ads_is_organic: number;
  ads_link_status: string | null;
  first_response_at: string | null;
  first_response_secs: number | null;
  first_person_reply_at: string | null;
  sla_target_secs: number | null;
  sla_status: SlaStatus;
  sla_due_at: string | null;
}

type Unavailable = { available: false; reason: string };
type Feed = { available: true; rows: LeadRow[]; has_more: boolean; now: string; start: number } | Unavailable;

interface CampaignLeads {
  campaign: string;
  campaign_name: string | null;
  provider: string | null;
  currency: string | null;
  spend: number | null;
  platform_leads: number | null;
  verified_leads: number | null;
  cpl_platform: number | null;
  cpl_verified: number | null;
}

interface LeadsSummary {
  from_date: string;
  to_date: string;
  by_campaign: CampaignLeads[];
  verified_available: boolean;
  reason?: string;
  totals: { verified_leads: number; organic: number; unlinked: number; test_leads_excluded: number; truncated: boolean } | null;
  funnel: { stage: string; label: string; count: number | null }[] | null;
  sla: { counts: Record<SlaStatus, number>; median_first_response_secs: number | null; measured: number; breaches: { name: string; lead_name: string; creation: string; lead_owner: string | null; ads_campaign_name: string | null; omni_identity: string | null }[] } | null;
}

const PAGE = 50;
const LIVE_MS = 30_000;
const SLA_ACCENT: Record<SlaStatus, Accent> = { Met: "green", Missed: "amber", Breached: "rose", Pending: "blue", "No SLA": "neutral" };

/** Frappe sends naive site-time strings; read them as local and correct by the server clock offset. */
function ts(v: string | null | undefined): number | null {
  if (!v) return null;
  const t = new Date(v.replace(" ", "T")).getTime();
  return Number.isNaN(t) ? null : t;
}

export function duration(secs: number | null | undefined): string {
  if (secs === null || secs === undefined || !Number.isFinite(secs)) return NA;
  const s = Math.max(0, Math.round(Math.abs(secs)));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} min`;
  return `${Math.floor(s / 86400)} d ${Math.floor((s % 86400) / 3600)} h`;
}

/** Ticks once a second while mounted: only rendered for leads whose SLA clock is still running. */
function Countdown({ due, offset }: { due: number; offset: number }) {
  const [now, setNow] = useState(() => Date.now() + offset);
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now() + offset), 1000);
    return () => window.clearInterval(id);
  }, [offset]);
  const left = Math.round((due - now) / 1000);
  if (left <= 0) return <Badge accent="rose">Breached · overdue {duration(-left)}</Badge>;
  const m = Math.floor(left / 60);
  const s = left % 60;
  return <Badge accent={left < 600 ? "amber" : "blue"}>Reply within {m >= 60 ? duration(left) : `${m}:${String(s).padStart(2, "0")}`}</Badge>;
}

function Sla({ r, offset, now }: { r: LeadRow; offset: number; now: number }) {
  if (r.sla_status === "Pending" && r.sla_due_at) {
    const due = ts(r.sla_due_at);
    if (due !== null) return <Countdown due={due} offset={offset} />;
  }
  const created = ts(r.creation);
  if (r.sla_status === "Breached") return <Badge accent="rose" solid>Breached · {duration(created !== null ? (now - created) / 1000 - (r.sla_target_secs ?? 0) : null)} over</Badge>;
  if (r.sla_status === "Met" || r.sla_status === "Missed") return <Badge accent={SLA_ACCENT[r.sla_status]}>{r.sla_status} · answered in {duration(r.first_response_secs)}</Badge>;
  return <Badge accent="neutral">No SLA</Badge>;
}

function LeadLinks({ doctype, name, identity }: { doctype: string; name: string; identity: string | null }) {
  const navigate = useNavigate();
  return (
    <div className="flex items-center gap-1 shrink-0">
      {identity && <Button size="sm" variant="ghost" onClick={() => navigate(`/t/${encodeURIComponent(identity)}`)} title="Open the person in Excom"><MessageSquare />Open</Button>}
      <a href={deskUrl(doctype, name)} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs text-ink-3 hover:text-ink-1 px-1.5" title="Open the lead in Desk"><ExternalLink className="size-3.5" />Desk</a>
    </div>
  );
}

function Funnel({ stages }: { stages: NonNullable<LeadsSummary["funnel"]> }) {
  const base = stages[0]?.count ?? 0;
  return (
    <div className="rounded-lg border border-border p-3 space-y-1.5 min-w-0">
      <div className="flex items-center justify-between gap-2"><p className="text-sm text-ink-1">Funnel</p><SourceBadge source="Verified" /></div>
      {stages.map((s) => {
        const share = s.count !== null && base ? s.count / base : null;
        return (
          <div key={s.stage} className="grid grid-cols-[minmax(0,10rem)_minmax(0,1fr)_minmax(0,4.5rem)] items-center gap-2 text-xs">
            <span className="text-ink-2 truncate">{s.label}</span>
            <div className="h-2 rounded bg-surface-sunken overflow-hidden" aria-hidden>
              {share !== null && <div className="h-full bg-crayon-green-base" style={{ width: `${Math.max(share * 100, s.count ? 2 : 0)}%` }} />}
            </div>
            <span className="text-right tabular-nums text-ink-1" title={s.count === null ? "Not available: you cannot read these records" : undefined}>
              {s.count === null ? NA : num(s.count)}{share !== null && s.stage !== "leads" ? <span className="text-ink-3"> · {Math.round(share * 100)}%</span> : null}
            </span>
          </div>
        );
      })}
      <p className="text-xs text-ink-3">Leads created in the range, followed through ERPNext's own links (opportunity from the lead, quotation for the lead or its opportunity, submitted sales order from that quotation).</p>
    </div>
  );
}

function ByCampaign({ rows }: { rows: CampaignLeads[] }) {
  if (!rows.length) return <EmptyState compact title="No campaign leads in this range" />;
  const cols = "grid-cols-[minmax(0,2.2fr)_repeat(6,minmax(0,0.9fr))]";
  return (
    <div className="rounded-lg border border-border overflow-x-auto">
      <div className={cn("grid gap-2 px-3 py-1.5 text-xs text-ink-3 min-w-[760px] bg-surface-sunken", cols)}>
        <span>Campaign</span><span className="text-right">Spend</span>
        <span className="text-right" title="As reported by the ad platform">Leads · platform</span><span className="text-right">CPL · platform</span>
        <span className="text-right" title="ERPNext Leads whose first ad touch is this campaign, test leads excluded">Leads · verified</span><span className="text-right">CPL · verified</span>
        <span className="text-right" title="Verified ÷ platform leads">Match</span>
      </div>
      {rows.map((r) => (
        <div key={r.campaign} className={cn("grid items-center gap-2 px-3 py-1.5 border-t border-border text-sm min-w-[760px]", cols)}>
          <span className="truncate" title={`${r.campaign_name ?? r.campaign} · ${r.provider ?? ""}`}>{r.campaign_name ?? r.campaign}<span className="text-xs text-ink-3"> · {r.provider}</span></span>
          <span className="text-right tabular-nums">{money(r.spend, r.currency)}</span>
          <span className="text-right tabular-nums">{num(r.platform_leads)}</span>
          <span className="text-right tabular-nums">{money(r.cpl_platform, r.currency)}</span>
          <span className="text-right tabular-nums">{num(r.verified_leads)}</span>
          <span className="text-right tabular-nums">{money(r.cpl_verified, r.currency)}</span>
          <span className="text-right tabular-nums text-ink-3">{r.platform_leads && r.verified_leads !== null ? `${Math.round((r.verified_leads / r.platform_leads) * 100)}%` : NA}</span>
        </div>
      ))}
    </div>
  );
}

export function LeadsTab() {
  const { apiParams } = useMarketingFilters();
  const [includeTest, setIncludeTest] = useState(false);
  const [pages, setPages] = useState(1);
  const key = JSON.stringify(apiParams);
  useEffect(() => setPages(1), [key, includeTest]);
  const feedParams = { ...apiParams, start: 0, page_length: PAGE * pages, include_test: includeTest ? 1 : 0 };
  const feed = useFrappeGetCall<{ message: Feed }>("excom.excom_ads.leads_dashboard.feed", feedParams, `ads-leads-feed-${JSON.stringify(feedParams)}`, { refreshInterval: LIVE_MS, keepPreviousData: true, revalidateOnFocus: true });
  const summary = useFrappeGetCall<{ message: LeadsSummary }>("excom.excom_ads.leads_dashboard.summary", apiParams, `ads-leads-summary-${key}`, { refreshInterval: 2 * LIVE_MS, keepPreviousData: true });
  const f = feed.data?.message;
  const s = summary.data?.message;

  // Server clock minus browser clock, so SLA timers agree with the server whatever the browser's time zone.
  const offset = useMemo(() => {
    const server = f && f.available ? ts(f.now) : null;
    return server !== null ? server - Date.now() : 0;
  }, [f]);
  const now = Date.now() + offset;

  if ((feed.isLoading && !f) || (summary.isLoading && !s)) return <div className="flex justify-center py-16"><Loader2 className="size-5 animate-spin text-ink-3" /></div>;
  if (f && !f.available) return <EmptyState title="Leads are not available" hint={f.reason} />;
  if (feed.error) return <EmptyState title="Could not load leads" hint={String((feed.error as { message?: string })?.message ?? "")} />;

  const platformLeads = s?.by_campaign.reduce<number | null>((acc, r) => (r.platform_leads === null ? acc : (acc ?? 0) + r.platform_leads), null) ?? null;
  const rows = f && f.available ? f.rows : [];
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-xs text-ink-3">
        <span className="inline-flex items-center gap-1"><Radio className={cn("size-3.5", feed.isValidating ? "text-crayon-green-text" : "")} />Live · refreshes every {LIVE_MS / 1000} s</span>
        {s && <span>{s.from_date} → {s.to_date}</span>}
        <label className="ml-auto inline-flex items-center gap-1.5 cursor-pointer">
          <input type="checkbox" checked={includeTest} onChange={(e) => setIncludeTest(e.target.checked)} />
          Show platform test leads (never counted)
        </label>
      </div>

      {s && s.verified_available && s.totals && s.sla && (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(170px,1fr))] gap-2">
          <Kpi label="Ad leads in ERPNext" value={num(s.totals.verified_leads)} source="Verified" sub={`${num(s.totals.organic)} organic · ${num(s.totals.unlinked)} not yet linked`} hint="ERPNext Leads with an ad touch, created in the range; test leads excluded" />
          <Kpi label="Leads reported by platforms" value={num(platformLeads)} source="Platform" hint="Sum of what the networks report for these campaigns. Never added to verified leads." />
          <Kpi label="SLA breached" value={num(s.sla.counts.Breached)} source="Verified" sub={`${num(s.sla.counts.Pending)} waiting · ${num(s.sla.counts.Missed)} answered late`} hint="First-response target of the lead's intake source" />
          <Kpi label="Median first response" value={duration(s.sla.median_first_response_secs)} source="Verified" sub={`over ${num(s.sla.measured)} leads`} />
          <Kpi label="Test leads excluded" value={num(s.totals.test_leads_excluded)} source="Verified" hint="Sent by the platforms' lead-form testing tools" />
        </div>
      )}
      {s && !s.verified_available && <EmptyState compact title="Verified lead figures are not available" hint={s.reason} />}
      {s?.totals?.truncated && <p className="text-xs text-crayon-amber-text">Counted over the latest 5,000 leads; narrow the filters for exact figures.</p>}

      <div className="grid gap-3 laptop:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <div className="space-y-1.5 min-w-0">
          <div className="flex items-center gap-2"><p className="text-sm text-ink-1">Leads by campaign</p><SourceBadge source="Platform" /><SourceBadge source="Verified" /></div>
          {s ? <ByCampaign rows={s.by_campaign} /> : null}
          <p className="text-xs text-ink-3">Platform and verified leads are shown side by side and never added. CPL is platform spend ÷ each.</p>
        </div>
        {s?.funnel && <Funnel stages={s.funnel} />}
      </div>

      <div className="space-y-1.5">
        <p className="text-sm text-ink-1">Latest ad leads</p>
        {!rows.length ? (
          <EmptyState compact title="No ad leads in this range" hint="Leads from Meta lead ads, Google lead forms, click-to-WhatsApp and tagged website enquiries appear here as they arrive." />
        ) : (
          <ul className="rounded-lg border border-border divide-y divide-border">
            {rows.map((r) => {
              const created = ts(r.creation);
              return (
                <li key={r.name} className="p-3 flex items-start gap-3 min-w-0">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-1.5 min-w-0">
                      <span className="text-sm text-ink-1 truncate" title={r.lead_name}>{r.lead_name}</span>
                      {r.company_name && <span className="text-xs text-ink-3 truncate">{r.company_name}</span>}
                      <Sla r={r} offset={offset} now={now} />
                      {r.ads_is_test ? <Badge accent="amber">Test lead</Badge> : null}
                      {r.ads_is_organic ? <Badge accent="neutral">Organic</Badge> : null}
                      {r.ads_link_status && r.ads_link_status !== "Linked" && <Badge accent="neutral" title="The ad is not linked to a synced campaign yet">{r.ads_link_status}</Badge>}
                    </div>
                    <p className="text-xs text-ink-2 truncate" title={[r.ads_campaign_name, r.ads_ad_name].filter(Boolean).join(" · ")}>
                      {r.ads_provider ?? "Ad"} {r.ads_touch_source ? `· ${r.ads_touch_source}` : ""} · {r.ads_campaign_name ?? "campaign not known yet"}{r.ads_ad_name ? ` · ${r.ads_ad_name}` : ""}
                    </p>
                    <p className="text-xs text-ink-3">
                      {created !== null ? `${duration((now - created) / 1000)} ago` : r.creation} · {r.status} · {r.lead_owner ? `owner ${r.lead_owner}` : "unassigned"}
                      {r.first_person_reply_at ? ` · first reply by a person ${duration(((ts(r.first_person_reply_at) ?? 0) - (created ?? 0)) / 1000)} after arrival` : ""}
                    </p>
                  </div>
                  <LeadLinks doctype={r.doctype} name={r.name} identity={r.omni_identity} />
                </li>
              );
            })}
          </ul>
        )}
        {f && f.available && f.has_more && (
          <Button size="sm" variant="ghost" onClick={() => setPages((p) => p + 1)}>Load more</Button>
        )}
      </div>

      {s?.sla && s.sla.breaches.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-sm text-ink-1">Waiting past their SLA</p>
          <ul className="rounded-lg border border-crayon-rose-base/40 divide-y divide-border">
            {s.sla.breaches.map((b) => (
              <li key={b.name} className="px-3 py-2 flex items-center gap-3 min-w-0 text-sm">
                <span className="truncate flex-1">{b.lead_name}<span className="text-xs text-ink-3"> · {b.ads_campaign_name ?? "—"} · {b.lead_owner ?? "unassigned"}</span></span>
                <LeadLinks doctype="Lead" name={b.name} identity={b.omni_identity} />
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
