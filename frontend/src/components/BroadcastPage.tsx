import { useState, useEffect, useCallback, useRef } from "react";
import { useFrappePostCall } from "frappe-react-sdk";
import {
  Plus,
  Search,
  Radio,
  Loader2,
  CheckCircle2,
  XCircle,
  Clock,
  AlertTriangle,
  Mail,
  MessageSquare,
  FileText,
  BarChart3,
  TrendingUp,
  MousePointerClick,
  UserX,
  Timer,
  Activity,
  Settings2,
} from "lucide-react";
import { Button, Input, Select, Chip, EmptyState, SegmentedControl } from "./primitives";
import { AdminPage, DataTable } from "./shell/AdminPage";
import { BroadcastWizard } from "./BroadcastWizard";
import { toast } from "sonner";

interface BroadcastItem {
  name: string;
  broadcast_name: string;
  subscriber_list: string;
  subscriber_list_name: string;
  channel: string;
  status: string;
  docstatus: number;
  wa_template: string;
  email_subject: string;
  scheduled_at?: string | null;
  total_recipients: number;
  sent_count: number;
  failed_count: number;
  creation: string;
  modified: string;
}

interface BroadcastLog {
  omni_identity: string;
  display_name: string;
  status: string;
  recipient_address: string;
  error_message: string;
  sent_at: string;
  creation: string;
}

const STATUS_STYLES: Record<string, { bg: string; text: string; icon: typeof Clock }> = {
  Draft: { bg: "bg-surface-active", text: "text-ink-3", icon: Clock },
  Scheduled: { bg: "bg-crayon-violet-tint", text: "text-crayon-violet-text", icon: Timer },
  Queued: { bg: "bg-crayon-blue-tint", text: "text-crayon-blue-text", icon: Clock },
  Sending: { bg: "bg-crayon-amber-tint", text: "text-crayon-amber-text", icon: Loader2 },
  Completed: { bg: "bg-crayon-green-tint", text: "text-crayon-green-text", icon: CheckCircle2 },
  "Partially Failed": { bg: "bg-crayon-amber-tint", text: "text-crayon-amber-text", icon: AlertTriangle },
  Failed: { bg: "bg-crayon-rose-tint", text: "text-crayon-rose-text", icon: XCircle },
};

function StatusBadge({ status }: { status: string }) {
  const style = STATUS_STYLES[status] || STATUS_STYLES.Draft;
  const Icon = style.icon;
  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${style.bg} ${style.text}`}>
      <Icon className={`w-3 h-3 ${status === "Sending" ? "animate-spin" : ""}`} />
      {status}
    </span>
  );
}

function ChannelIcon({ channel }: { channel: string }) {
  if (channel === "WhatsApp") return <MessageSquare className="w-4 h-4 text-crayon-green-text" />;
  return <Mail className="w-4 h-4 text-crayon-blue-text" />;
}

function formatDuration(seconds: number): string {
  if (!seconds) return "—";
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  return m > 0 ? `${h}h ${m}m` : `${h}h`;
}

interface MetricsData {
  broadcast_name: string;
  channel: string;
  status: string;
  time_windows: number[];
  delivery_funnel: {
    total_recipients: number; sent: number; delivered: number; read: number;
    sent_rate: number; delivered_rate: number; read_rate: number;
    failed: number; failed_rate: number;
  };
  response_by_window: { window_hours: number; window_label: string; count: number; rate: number }[];
  button_clicks: { button_text: string; click_count: number; rate: number }[];
  reply_quality: {
    text_replies: number; button_only: number; no_response: number;
    text_reply_rate: number; button_only_rate: number; no_response_rate: number;
  };
  optout: { count: number; rate: number };
  summary: { engagement_rate: number; avg_response_time_seconds: number; best_performing_cta: string | null };
}

function FunnelBar({ label, value, total, color }: { label: string; value: number; total: number; color: string }) {
  const pct = total > 0 ? (value / total) * 100 : 0;
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs">
        <span className="text-ink-3">{label}</span>
        <span className="text-ink-2 font-medium">{value} ({pct.toFixed(1)}%)</span>
      </div>
      <div className="h-2.5 bg-surface-sunken rounded-full overflow-hidden">
        <div className={`h-full rounded-full transition-all duration-700 ${color}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function MetricCard({ icon: Icon, label, value, sub, color = "text-ink-1" }: {
  icon: any; label: string; value: string | number; sub?: string; color?: string;
}) {
  return (
    <div className="bg-surface rounded-xl p-3 border border-border">
      <div className="flex items-center gap-2 mb-2">
        <Icon className={`w-4 h-4 ${color}`} />
        <span className="text-xs text-ink-3 font-medium">{label}</span>
      </div>
      <div className={`text-lg font-semibold ${color}`}>{value}</div>
      {sub && <div className="text-xs text-ink-3 mt-0.5">{sub}</div>}
    </div>
  );
}

function AnalyticsPanel({ broadcastName }: { broadcastName: string }) {
  const [metrics, setMetrics] = useState<MetricsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [customWindows, setCustomWindows] = useState("");
  const [editingWindows, setEditingWindows] = useState(false);

  const { call: fetchMetrics } = useFrappePostCall(
    "excom.excom.services.broadcast_metrics.get_broadcast_metrics"
  );

  const load = useCallback(async (tw?: string) => {
    setLoading(true);
    try {
      const res = await fetchMetrics({ broadcast_name: broadcastName, time_windows: tw || customWindows });
      setMetrics((res as any)?.message || null);
    } catch {
      toast.error("Failed to load metrics");
    } finally {
      setLoading(false);
    }
  }, [fetchMetrics, broadcastName, customWindows]);

  useEffect(() => { load(); }, [broadcastName, load]);

  if (loading && !metrics) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-8 h-8 text-crayon-blue-text animate-spin" />
      </div>
    );
  }

  if (!metrics) return null;

  const { delivery_funnel: df, response_by_window: rw, button_clicks: bc, reply_quality: rq, optout, summary } = metrics;

  return (
    <div className="p-3 space-y-3">
      <div className="grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(150px,1fr))]">
        <MetricCard icon={Activity} label="Engagement" value={`${summary.engagement_rate}%`} sub="Replies + Clicks / Sent" color="text-crayon-green-text" />
        <MetricCard icon={Timer} label="Avg Response" value={formatDuration(summary.avg_response_time_seconds)} sub="Time to first reply" color="text-crayon-blue-text" />
        <MetricCard icon={MousePointerClick} label="Best CTA" value={summary.best_performing_cta || "—"} sub="Most clicked button" color="text-crayon-violet-text" />
        <MetricCard icon={UserX} label="Opt-outs" value={optout.count} sub={`${optout.rate}% of sent`} color="text-crayon-rose-text" />
      </div>

      <div className="grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(min(100%,360px),1fr))]">
        <div className="bg-surface rounded-xl p-4 border border-border">
          <h3 className="text-sm font-semibold text-ink-1 mb-3 flex items-center gap-2">
            <BarChart3 className="w-4 h-4 text-crayon-blue-text" /> Delivery Funnel
          </h3>
          <div className="space-y-3">
            <FunnelBar label="Sent" value={df.sent} total={df.total_recipients} color="bg-crayon-blue-base" />
            <FunnelBar label="Delivered" value={df.delivered} total={df.sent} color="bg-crayon-green-base" />
            <FunnelBar label="Read" value={df.read} total={df.delivered} color="bg-crayon-violet-base" />
            <FunnelBar label="Failed" value={df.failed} total={df.total_recipients} color="bg-crayon-rose-base" />
          </div>
        </div>

        <div className="bg-surface rounded-xl p-4 border border-border">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold text-ink-1 flex items-center gap-2">
              <TrendingUp className="w-4 h-4 text-crayon-green-text" /> Response by Time Window
            </h3>
            <button
              onClick={() => setEditingWindows(!editingWindows)}
              className="p-1 rounded hover:bg-surface-sunken text-ink-3 hover:text-ink-2 transition-colors"
              title="Customize time windows"
            >
              <Settings2 className="w-3.5 h-3.5" />
            </button>
          </div>
          {editingWindows && (
            <div className="flex gap-2 mb-3">
              <input
                type="text"
                placeholder="e.g. 1,3,6,12,24"
                value={customWindows}
                onChange={(e) => setCustomWindows(e.target.value)}
                className="flex-1 bg-surface-sunken border border-border-strong rounded-lg px-3 py-1.5 text-sm text-ink-2 focus:outline-none focus:border-crayon-blue-base/40"
              />
              <button
                onClick={() => load(customWindows)}
                className="px-3 py-1.5 bg-crayon-blue-base text-white text-xs rounded-lg hover:bg-crayon-blue-base"
              >
                Apply
              </button>
            </div>
          )}
          <div className="space-y-2">
            {rw.map((w) => (
              <div key={w.window_hours} className="flex items-center gap-3">
                <span className="text-xs text-ink-3 w-20 shrink-0">{w.window_label}</span>
                <div className="flex-1 h-6 bg-surface-sunken rounded-lg overflow-hidden relative">
                  <div
                    className="h-full bg-crayon-blue-tint text-crayon-blue-text rounded-lg transition-all duration-700"
                    style={{ width: `${Math.min(w.rate, 100)}%` }}
                  />
                  <span className="absolute inset-0 flex items-center justify-center text-xs font-medium text-ink-1">
                    {w.count} ({w.rate}%)
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(min(100%,360px),1fr))]">
        {bc.length > 0 && (
          <div className="bg-surface rounded-xl p-4 border border-border">
            <h3 className="text-sm font-semibold text-ink-1 mb-3 flex items-center gap-2">
              <MousePointerClick className="w-4 h-4 text-crayon-violet-text" /> CTA Button Clicks
            </h3>
            <div className="space-y-2">
              {bc.map((b, i) => (
                <div key={i} className="flex items-center justify-between py-2 px-3 rounded-lg bg-surface-sunken">
                  <div className="flex items-center gap-2">
                    <span className="w-5 h-5 rounded bg-crayon-violet-tint text-crayon-violet-text text-xs font-semibold flex items-center justify-center">
                      {i + 1}
                    </span>
                    <span className="text-sm text-ink-2">{b.button_text}</span>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="text-sm font-medium text-ink-1">{b.click_count}</span>
                    <span className="text-xs text-ink-3">{b.rate}%</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="bg-surface rounded-xl p-4 border border-border">
          <h3 className="text-sm font-semibold text-ink-1 mb-3 flex items-center gap-2">
            <MessageSquare className="w-4 h-4 text-crayon-blue-text" /> Reply Quality
          </h3>
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <div className="w-3 h-3 rounded-full bg-crayon-green-base" />
                <span className="text-sm text-ink-3">Text Replies</span>
              </div>
              <span className="text-sm text-ink-2 font-medium">{rq.text_replies} ({rq.text_reply_rate}%)</span>
            </div>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <div className="w-3 h-3 rounded-full bg-crayon-violet-base" />
                <span className="text-sm text-ink-3">Button Only</span>
              </div>
              <span className="text-sm text-ink-2 font-medium">{rq.button_only} ({rq.button_only_rate}%)</span>
            </div>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <div className="w-3 h-3 rounded-full bg-surface-active" />
                <span className="text-sm text-ink-3">No Response</span>
              </div>
              <span className="text-sm text-ink-2 font-medium">{rq.no_response} ({rq.no_response_rate}%)</span>
            </div>
            <div className="h-3 flex rounded-full overflow-hidden mt-2">
              {rq.text_reply_rate > 0 && <div className="bg-crayon-green-base" style={{ width: `${rq.text_reply_rate}%` }} />}
              {rq.button_only_rate > 0 && <div className="bg-crayon-violet-base" style={{ width: `${rq.button_only_rate}%` }} />}
              {rq.no_response_rate > 0 && <div className="bg-surface-active" style={{ width: `${rq.no_response_rate}%` }} />}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function BroadcastDetailView({
  broadcastName,
  onBack,
  embedded,
}: {
  broadcastName: string;
  onBack: () => void;
  embedded?: boolean;
}) {
  const [detail, setDetail] = useState<any>(null);
  const [logs, setLogs] = useState<BroadcastLog[]>([]);
  const [logFilter, setLogFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<"logs" | "analytics">("logs");

  const { call: fetchDetail } = useFrappePostCall("excom.excom.api.broadcast.get_broadcast_detail");
  const { call: fetchLogs } = useFrappePostCall("excom.excom.api.broadcast.get_broadcast_logs");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetchDetail({ broadcast_name: broadcastName });
      const data = (res as any)?.message;
      setDetail(data);
      setLogs(data?.recent_logs || []);
    } catch {
      toast.error("Failed to load broadcast");
    } finally {
      setLoading(false);
    }
  }, [fetchDetail, broadcastName]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (
      !detail ||
      detail.status === "Sending" ||
      detail.status === "Scheduled" ||
      detail.status === "Queued"
    ) {
      const interval = setInterval(load, 5000);
      return () => clearInterval(interval);
    }
  }, [detail, load]);

  const loadFilteredLogs = useCallback(async () => {
    try {
      const res = await fetchLogs({ broadcast_name: broadcastName, status: logFilter, limit: 200 });
      setLogs((res as any)?.message?.logs || []);
    } catch {}
  }, [fetchLogs, broadcastName, logFilter]);

  useEffect(() => {
    if (logFilter) loadFilteredLogs();
    else if (detail) setLogs(detail.recent_logs || []);
  }, [logFilter]);

  if (loading && !detail) {
    return <AdminPage title="Broadcast" icon={<Radio />} onBack={onBack} embedded={embedded}><div className="flex justify-center py-12 text-ink-3"><Loader2 className="size-5 animate-spin" /></div></AdminPage>;
  }
  if (!detail) return null;

  const progress = detail.total_recipients > 0 ? Math.round(((detail.sent_count + detail.failed_count) / detail.total_recipients) * 100) : 0;
  const stats = [
    { l: "Recipients", v: detail.total_recipients, c: "text-ink-1" },
    { l: "Sent", v: detail.sent_count, c: "text-crayon-green-text" },
    { l: "Failed", v: detail.failed_count, c: "text-crayon-rose-text" },
    { l: "Progress", v: `${progress}%`, c: "text-crayon-blue-text" },
  ];

  return (
    <AdminPage
      title={<span className="inline-flex items-center gap-2 min-w-0"><ChannelIcon channel={detail.channel} /><span className="truncate">{detail.broadcast_name}</span></span>}
      onBack={onBack}
      embedded={embedded}
      actions={<StatusBadge status={detail.status} />}
      bleed
      toolbar={
        <div className="flex items-center gap-2 min-w-0">
          <SegmentedControl variant="segmented" value={activeTab} onChange={setActiveTab} segments={[{ value: "logs", label: "Delivery log", icon: <FileText /> }, { value: "analytics", label: "Analytics", icon: <BarChart3 /> }]} />
          {activeTab === "logs" && (
            <Select value={logFilter} onChange={(e) => setLogFilter(e.target.value)} className="ml-auto w-[120px]" aria-label="Filter by status">
              <option value="">All</option><option value="Sent">Sent</option><option value="Failed">Failed</option><option value="Skipped">Skipped</option>
            </Select>
          )}
        </div>
      }
    >
      <div className="px-3 pt-3 max-w-[1200px] mx-auto w-full">
        <p className="text-xs text-ink-3 truncate">{detail.subscriber_list} · {new Date(detail.creation).toLocaleDateString()}{detail.scheduled_at ? ` · Sends ${new Date(detail.scheduled_at).toLocaleString()}` : ""}</p>
        <div className="grid gap-2 mt-2 [grid-template-columns:repeat(auto-fit,minmax(120px,1fr))]">
          {stats.map((st) => <div key={st.l} className="rounded-md border border-border p-2.5 min-w-0"><div className={`text-lg tabular-nums truncate ${st.c}`}>{st.v}</div><div className="text-xs text-ink-3">{st.l}</div></div>)}
        </div>
        {detail.status === "Sending" && <div className="mt-2 h-1.5 bg-surface-sunken rounded-full overflow-hidden"><div className="h-full bg-crayon-blue-base transition-all" style={{ width: `${progress}%` }} /></div>}
      </div>

      {activeTab === "analytics" ? (
        <div className="max-w-[1200px] mx-auto w-full"><AnalyticsPanel broadcastName={broadcastName} /></div>
      ) : (
        <div className="mt-3">
          <DataTable
            rows={logs}
            keyOf={(l, ) => `${l.omni_identity}-${l.creation}`}
            empty={<EmptyState icon={<Clock />} title={detail.status === "Draft" ? "Submit the broadcast to start sending" : detail.status === "Scheduled" && detail.scheduled_at ? `Scheduled for ${new Date(detail.scheduled_at).toLocaleString()}` : "No delivery logs yet"} compact />}
            columns={[
              { key: "who", label: "Recipient", primary: true, render: (l) => <span className="text-ink-1">{l.display_name || l.omni_identity}</span> },
              { key: "addr", label: "Address", render: (l) => <span className="text-ink-3">{l.recipient_address || "—"}</span> },
              { key: "status", label: "Status", render: (l) => <Chip size="sm" accent={l.status === "Sent" ? "green" : l.status === "Failed" ? "rose" : "neutral"} label={l.status} /> },
              { key: "err", label: "Error", render: (l) => <span className="text-crayon-rose-text text-xs" title={l.error_message}>{l.error_message || "—"}</span> },
              { key: "time", label: "Time", align: "right", render: (l) => <span className="text-xs text-ink-3">{l.sent_at ? new Date(l.sent_at).toLocaleTimeString() : "—"}</span> },
            ]}
          />
        </div>
      )}
    </AdminPage>
  );
}

export function BroadcastPage({ onNavigateBack, embedded, presetList }: { onNavigateBack: () => void; embedded?: boolean; presetList?: string }) {
  const [viewMode, setViewMode] = useState<"list" | "detail">("list");
  const [selectedBroadcast, setSelectedBroadcast] = useState<string | null>(null);
  const [broadcasts, setBroadcasts] = useState<BroadcastItem[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [channelFilter, setChannelFilter] = useState("");
  const [showCompose, setShowCompose] = useState(Boolean(presetList));
  const [loading, setLoading] = useState(true);

  const { call: fetchBroadcasts } = useFrappePostCall("excom.excom.api.broadcast.get_broadcasts");

  const loadBroadcasts = useCallback(async () => {
    try {
      const res = await fetchBroadcasts({ search: searchQuery, status: statusFilter, channel: channelFilter });
      setBroadcasts((res as any)?.message?.broadcasts || []);
    } catch { toast.error("Failed to load broadcasts"); } finally { setLoading(false); }
  }, [fetchBroadcasts, searchQuery, statusFilter, channelFilter]);

  useEffect(() => { loadBroadcasts(); }, [loadBroadcasts]);

  if (viewMode === "detail" && selectedBroadcast) {
    return <BroadcastDetailView broadcastName={selectedBroadcast} embedded={embedded} onBack={() => { setViewMode("list"); setSelectedBroadcast(null); loadBroadcasts(); }} />;
  }

  return (
    <AdminPage
      title="Broadcasts"
      icon={<Radio />}
      onBack={onNavigateBack}
      embedded={embedded}
      bleed
      actions={<Button variant="primary" size="sm" onClick={() => setShowCompose(true)}><Plus />New</Button>}
      toolbar={
        <div className="flex items-center gap-2 min-w-0">
          <div className="relative flex-1 min-w-0"><Search className="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-ink-muted" /><Input value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)} placeholder="Search broadcasts" className="pl-8 bg-surface" /></div>
          <Select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} className="w-[120px] shrink-0" aria-label="Status">
            <option value="">All status</option><option value="Draft">Draft</option><option value="Scheduled">Scheduled</option><option value="Queued">Queued</option><option value="Sending">Sending</option><option value="Completed">Completed</option><option value="Failed">Failed</option>
          </Select>
          <Select value={channelFilter} onChange={(e) => setChannelFilter(e.target.value)} className="w-[110px] shrink-0 hidden tablet:block" aria-label="Channel">
            <option value="">All channels</option><option value="WhatsApp">WhatsApp</option><option value="Email">Email</option>
          </Select>
        </div>
      }
    >
      {loading ? (
        <div className="flex justify-center py-12 text-ink-3"><Loader2 className="size-5 animate-spin" /></div>
      ) : (
        <DataTable
          rows={broadcasts}
          keyOf={(b) => b.name}
          onRowClick={(b) => { setSelectedBroadcast(b.name); setViewMode("detail"); }}
          empty={<EmptyState icon={<Radio />} title="No broadcasts yet" hint="Reach a subscriber list on WhatsApp or email." action={<Button variant="primary" size="sm" onClick={() => setShowCompose(true)}><Plus />New broadcast</Button>} />}
          columns={[
            { key: "name", label: "Broadcast", primary: true, render: (b) => <span className="text-ink-1 font-medium">{b.broadcast_name}</span> },
            { key: "channel", label: "Channel", render: (b) => <span className="inline-flex items-center gap-1.5 text-ink-2"><ChannelIcon channel={b.channel} />{b.channel}</span> },
            { key: "list", label: "List", render: (b) => <span className="text-ink-3">{b.subscriber_list_name || b.subscriber_list}</span> },
            { key: "status", label: "Status", render: (b) => <StatusBadge status={b.status} /> },
            { key: "sent", label: "Sent / total", align: "right", render: (b) => <span className="text-ink-2">{b.sent_count}{b.failed_count > 0 && <span className="text-crayon-rose-text"> (+{b.failed_count} failed)</span>}<span className="text-ink-3"> / {b.total_recipients}</span></span> },
            { key: "date", label: "Date", align: "right", render: (b) => <span className="text-xs text-ink-3">{b.status === "Scheduled" && b.scheduled_at ? new Date(b.scheduled_at).toLocaleString() : new Date(b.creation).toLocaleDateString()}</span> },
          ]}
        />
      )}

      <BroadcastWizard open={showCompose} onOpenChange={setShowCompose} presetList={presetList} onCreated={(bcName) => { setShowCompose(false); setSelectedBroadcast(bcName); setViewMode("detail"); }} />
    </AdminPage>
  );
}
