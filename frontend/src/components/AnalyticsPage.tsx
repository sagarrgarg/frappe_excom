import { useState, useMemo, useCallback, useEffect } from "react";
import { BarChart3, TrendingUp, MessageCircle, DollarSign, Clock, Users, Activity, RefreshCw, Loader2 } from "lucide-react";
import { useFrappeGetCall } from "frappe-react-sdk";
import { Button, Select, SegmentedControl } from "./primitives";
import { AdminPage } from "./shell/AdminPage";
import { chartTheme } from "../lib/chart-theme";
import { toast } from "sonner";
import {
  AreaChart,
  Area,
  BarChart,
  Bar,
  PieChart,
  Pie,
  Cell,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";

interface AnalyticsPageProps {
  onNavigateBack: () => void;
  embedded?: boolean;
}

type AnalyticsTab = "overview" | "messaging" | "conversations" | "pricing";
type Period = "7" | "14" | "30" | "90";

const PERIOD_LABELS: Record<Period, string> = {
  "7": "7 Days",
  "14": "14 Days",
  "30": "30 Days",
  "90": "90 Days",
};

/** Chart colours come from the token CSS variables (UX-001 §9.1: crayon palette, ≥2px strokes, no gradient fills). */
const T = chartTheme();
const CATEGORY_COLORS: Record<string, string> = {
  MARKETING: T.byName.violet,
  UTILITY: T.byName.blue,
  AUTHENTICATION: T.byName.amber,
  SERVICE: T.byName.green,
  UNKNOWN: T.axis,
};
const CHART_COLORS = T.series;
const TT = T.tooltip;
const TICK = T.tick;
const GRID = T.grid;

function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toLocaleString();
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return `${h}h ${m}m`;
}

function formatDate(unix: number): string {
  return new Date(unix * 1000).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function StatCard({
  label,
  value,
  sub,
  icon: Icon,
  color = "blue",
}: {
  label: string;
  value: string | number;
  sub?: string;
  icon: React.FC<{ className?: string }>;
  color?: string;
}) {
  const iconTone: Record<string, string> = { blue: "text-crayon-blue-base", purple: "text-crayon-violet-base", green: "text-crayon-green-base", amber: "text-crayon-amber-base", rose: "text-crayon-rose-base" };
  return (
    <div className="rounded-lg border border-border bg-surface p-3 min-w-0">
      <div className="flex items-center gap-2 mb-1.5 min-w-0">
        <Icon className={`size-4 shrink-0 ${iconTone[color] || iconTone.blue}`} />
        <span className="text-xs text-ink-3 truncate">{label}</span>
      </div>
      <p className="text-lg text-ink-1 tabular-nums truncate">{typeof value === "number" ? formatNumber(value) : value}</p>
      {sub && <p className="text-xs text-ink-3 mt-0.5 truncate">{sub}</p>}
    </div>
  );
}

export function AnalyticsPage({ onNavigateBack, embedded }: AnalyticsPageProps) {
  const [activeTab, setActiveTab] = useState<AnalyticsTab>("overview");
  const [period, setPeriod] = useState<Period>("30");
  const [selectedAccount, setSelectedAccount] = useState("");

  const { data: accountsData } = useFrappeGetCall<{ message: any[] }>(
    "excom.excom.api.analytics.get_wa_accounts"
  );
  const accounts = accountsData?.message ?? [];

  useEffect(() => {
    if (accounts.length > 0 && !selectedAccount) {
      setSelectedAccount(accounts[0].name);
    }
  }, [accounts, selectedAccount]);

  const overviewParams = useMemo(
    () => ({
      account_name: selectedAccount,
      days: parseInt(period),
    }),
    [selectedAccount, period]
  );

  const internalParams = useMemo(
    () => ({ days: parseInt(period) }),
    [period]
  );

  const { data: overviewData, isLoading: overviewLoading, mutate: refreshOverview } =
    useFrappeGetCall<{ message: any }>(
      "excom.excom.api.analytics.get_analytics_overview",
      selectedAccount ? overviewParams : undefined,
    );

  const { data: internalData, isLoading: internalLoading, mutate: refreshInternal } =
    useFrappeGetCall<{ message: any }>(
      "excom.excom.api.analytics.get_internal_metrics",
      internalParams,
    );

  const overview = overviewData?.message || {};
  const internal = internalData?.message || {};

  const handleRefresh = useCallback(() => {
    refreshOverview();
    refreshInternal();
    toast.success("Analytics refreshed");
  }, [refreshOverview, refreshInternal]);

  const messagingChartData = useMemo(() => {
    const points = overview?.messaging?.data_points || [];
    return points.map((dp: any) => ({
      date: formatDate(dp.start),
      sent: dp.sent || 0,
      delivered: dp.delivered || 0,
    }));
  }, [overview]);

  const internalDayData = useMemo(() => {
    const points = internal?.messages_by_day || [];
    const dayMap: Record<string, { day: string; inbound: number; outbound: number }> = {};
    for (const p of points) {
      if (!dayMap[p.day]) dayMap[p.day] = { day: p.day, inbound: 0, outbound: 0 };
      if (p.direction === "Inbound") dayMap[p.day].inbound = p.count;
      else dayMap[p.day].outbound = p.count;
    }
    return Object.values(dayMap).sort((a, b) => a.day.localeCompare(b.day)).map((d) => ({
      ...d,
      day: new Date(d.day).toLocaleDateString("en-US", { month: "short", day: "numeric" }),
    }));
  }, [internal]);

  const channelPieData = useMemo(() => {
    return (internal?.messages_by_channel || []).map((c: any) => ({
      name: c.channel ? c.channel.charAt(0).toUpperCase() + c.channel.slice(1) : "Unknown",
      value: c.count,
    }));
  }, [internal]);

  const categoryPieData = useMemo(() => {
    const cats = overview?.conversations?.by_category || {};
    return Object.entries(cats).map(([name, value]) => ({
      name: name.charAt(0) + name.slice(1).toLowerCase(),
      value: value as number,
      fill: CATEGORY_COLORS[name] || T.axis,
    }));
  }, [overview]);

  const typeBarData = useMemo(() => {
    return (internal?.messages_by_type || []).slice(0, 8).map((t: any) => ({
      type: t.message_type,
      count: t.count,
    }));
  }, [internal]);

  const isLoading = overviewLoading || internalLoading;

  const tabs: { id: AnalyticsTab; label: string; icon: React.FC<{ className?: string }> }[] = [
    { id: "overview", label: "Overview", icon: BarChart3 },
    { id: "messaging", label: "Messages", icon: MessageCircle },
    { id: "conversations", label: "Conversations", icon: Users },
    { id: "pricing", label: "Costs", icon: DollarSign },
  ];

  return (
    <AdminPage
      title="Analytics"
      icon={<BarChart3 />}
      onBack={onNavigateBack}
      embedded={embedded}
      wide
      actions={
        <>
          {accounts.length > 1 && (
            <Select value={selectedAccount} onChange={(e) => setSelectedAccount(e.target.value)} className="w-[150px] hidden tablet:block" aria-label="Account">
              {accounts.map((a: any) => <option key={a.name} value={a.name}>{a.account_name || a.name}</option>)}
            </Select>
          )}
          <Select value={period} onChange={(e) => setPeriod(e.target.value as Period)} className="w-[100px]" aria-label="Period">
            {(Object.entries(PERIOD_LABELS) as [Period, string][]).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </Select>
          <Button variant="ghost" size="icon" onClick={handleRefresh} disabled={isLoading} aria-label="Refresh">{isLoading ? <Loader2 className="animate-spin" /> : <RefreshCw />}</Button>
        </>
      }
      toolbar={<SegmentedControl variant="segmented" value={activeTab} onChange={setActiveTab} segments={tabs.map((t) => ({ value: t.id, label: t.label, icon: <t.icon /> }))} />}
    >
      <div>
        {isLoading && !overview.messaging && !internal.messages_by_day ? (
          <div className="flex items-center justify-center py-20 text-ink-3">
            <Loader2 className="size-5 animate-spin" />
          </div>
        ) : (
          <>
            {activeTab === "overview" && (
              <OverviewTab
                overview={overview}
                internal={internal}
                messagingChartData={messagingChartData}
                internalDayData={internalDayData}
                channelPieData={channelPieData}
                categoryPieData={categoryPieData}
                typeBarData={typeBarData}
              />
            )}
            {activeTab === "messaging" && (
              <MessagingTab
                overview={overview}
                internal={internal}
                messagingChartData={messagingChartData}
                internalDayData={internalDayData}
              />
            )}
            {activeTab === "conversations" && (
              <ConversationsTab
                overview={overview}
                internal={internal}
                categoryPieData={categoryPieData}
              />
            )}
            {activeTab === "pricing" && (
              <PricingTab overview={overview} />
            )}
          </>
        )}
      </div>
    </AdminPage>
  );
}


function OverviewTab({
  overview,
  internal,
  messagingChartData,
  internalDayData,
  channelPieData,
  categoryPieData,
  typeBarData,
}: any) {
  return (
    <div className="space-y-4">
      {/* KPI cards */}
      <div className="grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(150px,1fr))]">
        <StatCard
          label="Messages Sent"
          value={overview?.messaging?.total_sent || 0}
          sub={`${overview?.messaging?.delivery_rate || 0}% delivered`}
          icon={MessageCircle}
          color="blue"
        />
        <StatCard
          label="Conversations"
          value={overview?.conversations?.total || 0}
          sub={`$${overview?.conversations?.total_cost || 0} total cost`}
          icon={Users}
          color="purple"
        />
        <StatCard
          label="Active Threads"
          value={internal?.active_threads || 0}
          sub={`${internal?.total_threads || 0} total`}
          icon={Activity}
          color="green"
        />
        <StatCard
          label="Avg Response Time"
          value={formatDuration(internal?.avg_response_time_seconds || 0)}
          sub={`${internal?.new_contacts || 0} new contacts`}
          icon={Clock}
          color="amber"
        />
      </div>

      {/* Charts row 1 */}
      <div className="grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr))]">
        <ChartCard title="Message volume" subtitle="Inbound vs outbound" rows={internalDayData} columns={[{ key: "day", label: "Day" }, { key: "inbound", label: "In" }, { key: "outbound", label: "Out" }]}>
          {internalDayData.length > 0 ? (
            <ResponsiveContainer width="100%" height={240}>
              <AreaChart data={internalDayData}>
                <CartesianGrid strokeDasharray="3 3" stroke={GRID} />
                <XAxis dataKey="day" tick={TICK} />
                <YAxis tick={TICK} />
                <Tooltip contentStyle={TT} />
                <Area type="monotone" dataKey="inbound" stackId="1" stroke={T.byName.blue} strokeWidth={2} fill={T.byName.blue} fillOpacity={0.15} name="Inbound" />
                <Area type="monotone" dataKey="outbound" stackId="1" stroke={T.byName.violet} strokeWidth={2} fill={T.byName.violet} fillOpacity={0.15} name="Outbound" />
                <Legend />
              </AreaChart>
            </ResponsiveContainer>
          ) : <EmptyChart />}
        </ChartCard>

        <ChartCard title="Channels" subtitle="Messages by channel" rows={channelPieData} columns={[{ key: "name", label: "Channel" }, { key: "value", label: "Messages" }]}>
          {channelPieData.length > 0 ? (
            <ResponsiveContainer width="100%" height={240}>
              <PieChart>
                <Pie data={channelPieData} cx="50%" cy="50%" innerRadius={56} outerRadius={92} stroke={T.surface} strokeWidth={2} dataKey="value" label={({ name, percent }: { name?: string; percent?: number }) => `${name ?? ""} ${((percent ?? 0) * 100).toFixed(0)}%`}>
                  {channelPieData.map((_: any, i: number) => (
                    <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip contentStyle={TT} />
              </PieChart>
            </ResponsiveContainer>
          ) : <EmptyChart />}
        </ChartCard>
      </div>

      {/* Charts row 2 */}
      <div className="grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr))]">
        <ChartCard title="Conversation categories" subtitle="From Meta analytics" rows={categoryPieData} columns={[{ key: "name", label: "Category" }, { key: "value", label: "Count" }]}>
          {categoryPieData.length > 0 ? (
            <ResponsiveContainer width="100%" height={240}>
              <PieChart>
                <Pie data={categoryPieData} cx="50%" cy="50%" innerRadius={56} outerRadius={92} stroke={T.surface} strokeWidth={2} dataKey="value" label={({ name, percent }: { name?: string; percent?: number }) => `${name ?? ""} ${((percent ?? 0) * 100).toFixed(0)}%`}>
                  {categoryPieData.map((entry: any, i: number) => (
                    <Cell key={i} fill={entry.fill} />
                  ))}
                </Pie>
                <Tooltip contentStyle={TT} />
              </PieChart>
            </ResponsiveContainer>
          ) : <EmptyChart message="No Meta conversation data" />}
        </ChartCard>

        <ChartCard title="Message types" rows={typeBarData} columns={[{ key: "type", label: "Type" }, { key: "count", label: "Count" }]}>
          {typeBarData.length > 0 ? (
            <ResponsiveContainer width="100%" height={240}>
              <BarChart data={typeBarData} layout="vertical">
                <CartesianGrid strokeDasharray="3 3" stroke={GRID} />
                <XAxis type="number" tick={TICK} />
                <YAxis type="category" dataKey="type" tick={TICK} width={80} />
                <Tooltip contentStyle={TT} />
                <Bar dataKey="count" fill={T.byName.blue} radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : <EmptyChart />}
        </ChartCard>
      </div>

      {/* Agent performance */}
      {(internal?.agent_performance || []).length > 0 && (
        <ChartCard title="Agent Performance" subtitle="Messages sent by agent">
          <div className="space-y-2 mt-2">
            {internal.agent_performance.map((agent: any, i: number) => {
              const maxCount = internal.agent_performance[0]?.messages_sent || 1;
              return (
                <div key={i} className="flex items-center gap-3">
                  <span className="text-xs text-ink-3 w-40 truncate">{agent.agent}</span>
                  <div className="flex-1 bg-surface-sunken rounded-full h-5 overflow-hidden">
                    <div
                      className="h-full bg-crayon-blue-base/30 rounded-full flex items-center justify-end pr-2"
                      style={{ width: `${Math.max((agent.messages_sent / maxCount) * 100, 10)}%` }}
                    >
                      <span className="text-xs text-ink-1 font-medium">{agent.messages_sent}</span>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </ChartCard>
      )}
    </div>
  );
}


function MessagingTab({ overview, internal, messagingChartData, internalDayData }: any) {
  return (
    <div className="space-y-4">
      <div className="grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(150px,1fr))]">
        <StatCard label="Total Sent" value={overview?.messaging?.total_sent || 0} icon={MessageCircle} color="blue" />
        <StatCard label="Total Delivered" value={overview?.messaging?.total_delivered || 0} icon={TrendingUp} color="green" />
        <StatCard label="Delivery Rate" value={`${overview?.messaging?.delivery_rate || 0}%`} icon={Activity} color="purple" />
        <StatCard label="New Contacts" value={internal?.new_contacts || 0} icon={Users} color="amber" />
      </div>

      <ChartCard title="Meta messaging" subtitle="Sent vs delivered (Meta API)" rows={messagingChartData} columns={[{ key: "date", label: "Date" }, { key: "sent", label: "Sent" }, { key: "delivered", label: "Delivered" }]}>
        {messagingChartData.length > 0 ? (
          <ResponsiveContainer width="100%" height={280}>
            <AreaChart data={messagingChartData}>
              <CartesianGrid strokeDasharray="3 3" stroke={GRID} />
              <XAxis dataKey="date" tick={TICK} />
              <YAxis tick={TICK} />
              <Tooltip contentStyle={TT} />
              <Area type="monotone" dataKey="sent" stroke={T.byName.blue} strokeWidth={2} fill={T.byName.blue} fillOpacity={0.15} name="Sent" />
              <Area type="monotone" dataKey="delivered" stroke={T.byName.green} strokeWidth={2} fill={T.byName.green} fillOpacity={0.15} name="Delivered" />
              <Legend />
            </AreaChart>
          </ResponsiveContainer>
        ) : <EmptyChart message="No Meta messaging data available" />}
      </ChartCard>

      <ChartCard title="Internal message volume" subtitle="Inbound vs outbound" rows={internalDayData} columns={[{ key: "day", label: "Day" }, { key: "inbound", label: "In" }, { key: "outbound", label: "Out" }]}>
        {internalDayData.length > 0 ? (
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={internalDayData}>
              <CartesianGrid strokeDasharray="3 3" stroke={GRID} />
              <XAxis dataKey="day" tick={TICK} />
              <YAxis tick={TICK} />
              <Tooltip contentStyle={TT} />
              <Bar dataKey="inbound" fill={T.byName.blue} name="Inbound" radius={[4, 4, 0, 0]} />
              <Bar dataKey="outbound" fill={T.byName.violet} name="Outbound" radius={[4, 4, 0, 0]} />
              <Legend />
            </BarChart>
          </ResponsiveContainer>
        ) : <EmptyChart />}
      </ChartCard>
    </div>
  );
}


function ConversationsTab({ overview, internal, categoryPieData }: any) {
  const convPoints = overview?.conversations?.data_points || [];

  const dailyConvData = useMemo(() => {
    const dayMap: Record<string, Record<string, string | number>> = {};
    for (const dp of convPoints) {
      const day = formatDate(dp.start);
      if (!dayMap[day]) dayMap[day] = { day };
      const cat = dp.conversation_category || "OTHER";
      dayMap[day][cat] = (dayMap[day][cat] || 0) + (dp.conversation || 0);
    }
    return Object.values(dayMap);
  }, [convPoints]);

  const categories = useMemo(() => {
    const cats = new Set<string>();
    for (const dp of convPoints) {
      if (dp.conversation_category) cats.add(dp.conversation_category);
    }
    return Array.from(cats);
  }, [convPoints]);

  return (
    <div className="space-y-4">
      <div className="grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(150px,1fr))]">
        <StatCard label="Total Conversations" value={overview?.conversations?.total || 0} icon={Users} color="purple" />
        <StatCard label="Total Cost" value={`$${overview?.conversations?.total_cost || 0}`} icon={DollarSign} color="amber" />
        <StatCard label="Active Threads" value={internal?.active_threads || 0} icon={Activity} color="green" />
        <StatCard label="Avg Response" value={formatDuration(internal?.avg_response_time_seconds || 0)} icon={Clock} color="blue" />
      </div>

      <div className="grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr))]">
        <ChartCard title="Conversations by category" subtitle="From Meta analytics" rows={categoryPieData} columns={[{ key: "name", label: "Category" }, { key: "value", label: "Count" }]}>
          {categoryPieData.length > 0 ? (
            <ResponsiveContainer width="100%" height={260}>
              <PieChart>
                <Pie data={categoryPieData} cx="50%" cy="50%" innerRadius={64} outerRadius={104} stroke={T.surface} strokeWidth={2} dataKey="value" label={({ name, value }) => `${name}: ${value}`}>
                  {categoryPieData.map((entry: any, i: number) => (
                    <Cell key={i} fill={entry.fill} />
                  ))}
                </Pie>
                <Tooltip contentStyle={TT} />
                <Legend />
              </PieChart>
            </ResponsiveContainer>
          ) : <EmptyChart message="No conversation category data" />}
        </ChartCard>

        <ChartCard title="Daily conversations" subtitle="By category" rows={dailyConvData} columns={[{ key: "day", label: "Day" }, ...categories.map((c) => ({ key: c, label: c.charAt(0) + c.slice(1).toLowerCase() }))]}>
          {dailyConvData.length > 0 ? (
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={dailyConvData}>
                <CartesianGrid strokeDasharray="3 3" stroke={GRID} />
                <XAxis dataKey="day" tick={TICK} />
                <YAxis tick={TICK} />
                <Tooltip contentStyle={TT} />
                {categories.map((cat, i) => (
                  <Bar key={cat} dataKey={cat} stackId="stack" fill={CATEGORY_COLORS[cat] || CHART_COLORS[i % CHART_COLORS.length]} name={cat.charAt(0) + cat.slice(1).toLowerCase()} />
                ))}
                <Legend />
              </BarChart>
            </ResponsiveContainer>
          ) : <EmptyChart message="No daily conversation data" />}
        </ChartCard>
      </div>

      {/* Category breakdown table */}
      {Object.keys(overview?.conversations?.by_category || {}).length > 0 && (
        <ChartCard title="Category Breakdown" subtitle="Conversation counts by type">
          <div className="mt-3 space-y-2">
            {Object.entries(overview.conversations.by_category).map(([cat, count]: [string, any]) => (
              <div key={cat} className="flex items-center justify-between py-2 px-3 rounded-lg bg-surface-sunken">
                <div className="flex items-center gap-2">
                  <div className="w-3 h-3 rounded-full" style={{ backgroundColor: CATEGORY_COLORS[cat] || T.axis }} />
                  <span className="text-sm text-ink-2">{cat.charAt(0) + cat.slice(1).toLowerCase()}</span>
                </div>
                <span className="text-sm font-medium text-ink-1">{formatNumber(count)}</span>
              </div>
            ))}
          </div>
        </ChartCard>
      )}
    </div>
  );
}


function PricingTab({ overview }: any) {
  const pricingPoints = overview?.pricing?.data_points || [];

  const pricingByCategory = useMemo(() => {
    const catMap: Record<string, { volume: number; cost: number }> = {};
    for (const dp of pricingPoints) {
      const cat = dp.pricing_category || "OTHER";
      if (!catMap[cat]) catMap[cat] = { volume: 0, cost: 0 };
      catMap[cat].volume += dp.volume || 0;
      catMap[cat].cost += dp.cost || 0;
    }
    return Object.entries(catMap).map(([name, data]) => ({
      name: name.charAt(0) + name.slice(1).toLowerCase().replace(/_/g, " "),
      ...data,
    }));
  }, [pricingPoints]);

  const dailyPricing = useMemo(() => {
    const dayMap: Record<string, { day: string; cost: number; volume: number }> = {};
    for (const dp of pricingPoints) {
      const day = formatDate(dp.start);
      if (!dayMap[day]) dayMap[day] = { day, cost: 0, volume: 0 };
      dayMap[day].cost += dp.cost || 0;
      dayMap[day].volume += dp.volume || 0;
    }
    return Object.values(dayMap);
  }, [pricingPoints]);

  return (
    <div className="space-y-4">
      <div className="grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(150px,1fr))]">
        <StatCard label="Total Cost" value={`$${overview?.pricing?.total_cost || 0}`} icon={DollarSign} color="rose" />
        <StatCard label="Total Volume" value={overview?.pricing?.total_volume || 0} icon={MessageCircle} color="blue" />
        <StatCard
          label="Cost per Message"
          value={
            overview?.pricing?.total_volume
              ? `$${(overview.pricing.total_cost / overview.pricing.total_volume).toFixed(4)}`
              : "$0"
          }
          icon={TrendingUp}
          color="amber"
        />
      </div>

      <div className="grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr))]">
        <ChartCard title="Daily spending" subtitle="Cost over time" rows={dailyPricing} columns={[{ key: "day", label: "Day" }, { key: "cost", label: "Cost", format: (v) => `$${Number(v).toFixed(2)}` }, { key: "volume", label: "Volume" }]}>
          {dailyPricing.length > 0 ? (
            <ResponsiveContainer width="100%" height={260}>
              <AreaChart data={dailyPricing}>
                <CartesianGrid strokeDasharray="3 3" stroke={GRID} />
                <XAxis dataKey="day" tick={TICK} />
                <YAxis tick={TICK} tickFormatter={(v) => `$${v}`} />
                <Tooltip contentStyle={TT} formatter={(v) => [`$${Number(v).toFixed(2)}`, "Cost"]} />
                <Area type="monotone" dataKey="cost" stroke={T.byName.rose} strokeWidth={2} fill={T.byName.rose} fillOpacity={0.15} />
              </AreaChart>
            </ResponsiveContainer>
          ) : <EmptyChart message="No pricing data available" />}
        </ChartCard>

        <ChartCard title="Cost by category" subtitle="Spending breakdown" rows={pricingByCategory} columns={[{ key: "name", label: "Category" }, { key: "cost", label: "Cost", format: (v) => `$${Number(v).toFixed(2)}` }, { key: "volume", label: "Volume" }]}>
          {pricingByCategory.length > 0 ? (
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={pricingByCategory}>
                <CartesianGrid strokeDasharray="3 3" stroke={GRID} />
                <XAxis dataKey="name" tick={TICK} angle={-20} textAnchor="end" height={60} />
                <YAxis tick={TICK} tickFormatter={(v) => `$${v}`} />
                <Tooltip contentStyle={TT} />
                <Bar dataKey="cost" fill={T.byName.amber} name="Cost ($)" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : <EmptyChart message="No category pricing data" />}
        </ChartCard>
      </div>

      {pricingByCategory.length > 0 && (
        <ChartCard title="Pricing Breakdown" subtitle="Volume and cost by category">
          <div className="mt-3">
            <div className="grid grid-cols-3 gap-2 text-xs text-ink-3 font-medium px-3 py-1.5">
              <span>Category</span>
              <span className="text-right">Volume</span>
              <span className="text-right">Cost</span>
            </div>
            {pricingByCategory.map((row) => (
              <div key={row.name} className="grid grid-cols-3 gap-2 px-3 py-2 rounded-lg hover:bg-surface-sunken">
                <span className="text-sm text-ink-2">{row.name}</span>
                <span className="text-sm text-ink-1 text-right">{formatNumber(row.volume)}</span>
                <span className="text-sm text-ink-1 text-right">${row.cost.toFixed(2)}</span>
              </div>
            ))}
          </div>
        </ChartCard>
      )}
    </div>
  );
}


/**
 * ChartCard — chart from tablet up; below 640 a table of the same rows (UX-001 §9.1 "table fallback under 640").
 * Pass `rows` + `columns` for the fallback; charts without tabular data render as-is.
 */
function ChartCard({ title, subtitle, children, rows, columns }: { title: string; subtitle?: string; children: React.ReactNode; rows?: Record<string, any>[]; columns?: { key: string; label: string; format?: (v: any) => string }[] }) {
  const hasTable = Boolean(rows && rows.length && columns && columns.length);
  return (
    <div className="bg-surface border border-border rounded-lg p-3 min-w-0">
      <h3 className="text-sm font-medium text-ink-1 truncate">{title}</h3>
      {subtitle && <p className="text-xs text-ink-3 mt-0.5 mb-2 truncate">{subtitle}</p>}
      <div className={hasTable ? "hidden tablet:block" : ""}>{children}</div>
      {hasTable && (
        <div className="tablet:hidden overflow-x-auto">
          <table className="w-full text-xs tabular-nums">
            <thead><tr className="text-ink-3 border-b border-border">{columns!.map((c) => <th key={c.key} className="text-left font-medium py-1 pr-2 whitespace-nowrap">{c.label}</th>)}</tr></thead>
            <tbody>{rows!.slice(0, 30).map((r, i) => <tr key={i} className="border-b border-border">{columns!.map((c) => <td key={c.key} className="py-1 pr-2 text-ink-1 truncate max-w-[140px]">{c.format ? c.format(r[c.key]) : String(r[c.key] ?? "—")}</td>)}</tr>)}</tbody>
          </table>
        </div>
      )}
    </div>
  );
}


function EmptyChart({ message = "No data available for this period" }: { message?: string }) {
  return (
    <div className="flex items-center justify-center h-40 text-ink-3">
      <div className="text-center">
        <BarChart3 className="w-8 h-8 mx-auto mb-2 opacity-30" />
        <p className="text-xs">{message}</p>
      </div>
    </div>
  );
}
