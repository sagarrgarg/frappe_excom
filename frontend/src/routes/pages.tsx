import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Users, MoreHorizontal, Shield, GitMerge, ListChecks, Cog, Settings, Radio, BarChart3, Rows3, ArrowLeftRight, Bug, Search, Mail, Phone, KanbanSquare, Megaphone, LogOut, MessageSquareText, Bot } from "lucide-react";
import { PageFrame } from "../components/shell/PageFrame";
import { useInbox } from "../components/shell/InboxProvider";
import { Row, Avatar, Input, EmptyState, Badge } from "../components/primitives";
import { BroadcastPage } from "../components/BroadcastPage";
import { AnalyticsPage } from "../components/AnalyticsPage";
import { TeamManagementPage } from "../components/TeamManagementPage";
import { MergeSuggestionsPage } from "../components/MergeSuggestionsPage";
import { SubscriberListPage } from "../components/SubscriberListPage";
import { SubscriberRulesPage } from "../components/SubscriberRulesPage";
import { SettingsPage } from "../components/SettingsPage";
import { useInboxMeta } from "../hooks/useInboxMeta";
import { hasRole } from "../lib/ui-flag";
import { channelMeta } from "../lib/channels";
import { FeedbackDialog } from "../components/shell/FeedbackDialog";
import { CallButton } from "../components/voice/CallButton";
import { AttentionBell } from "../components/shell/AttentionBell";
import { AvailabilityToggle } from "../components/voice/AvailabilityToggle";
import { LinePicker } from "../components/voice/LinePicker";
import { useSoftphoneContext } from "../components/voice/SoftphoneProvider";

/** Admin pages mount inside the shell; their own header renders the back button on phone/tablet via `embedded`. */
function useBack() { const navigate = useNavigate(); return () => (window.history.length > 1 ? navigate(-1) : navigate("/inbox")); }

export function BroadcastsRoute() { const back = useBack(); return <BroadcastPage onNavigateBack={back} embedded />; }
export function AnalyticsRoute() { const back = useBack(); return <AnalyticsPage onNavigateBack={back} embedded />; }
export function TeamsRoute() { const back = useBack(); return <TeamManagementPage onNavigateBack={back} embedded />; }
export function MergeRoute() { const back = useBack(); return <MergeSuggestionsPage onNavigateBack={back} embedded />; }
export function SubscribersRoute() { const back = useBack(); const navigate = useNavigate(); return <SubscriberListPage onNavigateBack={back} onNavigateToBroadcasts={() => navigate("/broadcasts")} embedded />; }
export function RulesRoute() { const back = useBack(); return <SubscriberRulesPage onNavigateBack={back} embedded />; }
export function SettingsRoute() { const back = useBack(); return <SettingsPage onNavigateBack={back} embedded />; }

/** Contacts — identities in the inbox, searchable. Opens the record. */
export function ContactsRoute() {
  const { allContacts, openRecord } = useInbox();
  const [q, setQ] = useState("");
  const list = useMemo(() => {
    const s = q.trim().toLowerCase();
    const sorted = [...allContacts].sort((a, b) => a.contactName.localeCompare(b.contactName));
    if (!s) return sorted;
    return sorted.filter((c) => `${c.contactName} ${c.contactInfo.company} ${c.contactInfo.phone} ${c.contactInfo.email}`.toLowerCase().includes(s));
  }, [allContacts, q]);
  return (
    <PageFrame title="Contacts" icon={<Users />} className="!p-0" actions={<span className="text-xs text-ink-3 tabular-nums">{list.length}</span>}>
      <div className="-m-3">
        <div className="sticky top-0 z-10 bg-surface border-b border-border p-2"><div className="relative"><Search className="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-ink-muted" /><Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search contacts" className="pl-8" data-search-input /></div></div>
        {list.length === 0 ? <EmptyState icon={<Users />} title="No contacts" hint="Contacts appear here once they have a conversation." compact /> : list.map((c) => (
          <Row key={c.id} onClick={() => openRecord(c.id)} className="border-b border-border">
            <Avatar name={c.contactName} src={c.contactAvatar} size={32} />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-ink-1 truncate">{c.contactName}</p>
              <p className="text-xs text-ink-3 truncate flex items-center gap-2">{c.contactInfo.company && <span className="truncate">{c.contactInfo.company}</span>}{c.contactInfo.phone && <span className="inline-flex items-center gap-1 shrink-0"><Phone className="size-3" />{c.contactInfo.phone}</span>}{c.contactInfo.email && <span className="inline-flex items-center gap-1 truncate"><Mail className="size-3" />{c.contactInfo.email}</span>}</p>
            </div>
            <span className="flex items-center gap-0.5 shrink-0">{c.channels.map((ch) => { const m = channelMeta(ch); return <m.icon key={ch} className={`size-3.5 text-crayon-${m.accent}-base`} />; })}</span>
            {/* Calling someone should not require finding a thread first. Renders nothing when
                the site has no voice line or the contact has no number. */}
            <span className="shrink-0" onClick={(e) => e.stopPropagation()}>
              <CallButton number={c.contactInfo.phone} displayName={c.contactName} size="sm" variant="ghost" />
            </span>
          </Row>
        ))}
      </div>
    </PageFrame>
  );
}

/** Phone "More" tab — everything the rail's avatar menu holds. */
/**
 * The phone's way to everything that is not a bottom tab. It has to list every page the rail does —
 * Calls, Pipeline, Marketing and Tasks used to be missing, so on a phone they could only be reached
 * by typing the address — plus what sits at the foot of the rail: availability for calls, what is
 * waiting, and signing out.
 */
export function MoreRoute() {
  const navigate = useNavigate();
  const { density, setDensity, setPaletteOpen } = useInbox();
  const { mergeCount } = useInboxMeta();
  const voice = useSoftphoneContext();
  const voiceEnabled = Boolean(voice?.config?.enabled);
  const hasCalls = voiceEnabled || Boolean(voice?.whatsappLines?.length);
  const manager = hasRole("Excom Admin") || hasRole("System Manager");
  const [fb, setFb] = useState(false);
  const groups: { label: string; icon: React.ReactNode; to: string; count?: number }[][] = [
    [
      { label: "Pipeline", icon: <KanbanSquare />, to: "/pipeline" },
      { label: "Contacts", icon: <Users />, to: "/contacts" },
      ...(voiceEnabled ? [{ label: "Calls", icon: <Phone />, to: "/calls" }] : []),
    ],
    [
      { label: "Broadcasts", icon: <Radio />, to: "/broadcasts" },
      { label: "Analytics", icon: <BarChart3 />, to: "/analytics" },
      ...(hasRole("Excom Marketing") || manager ? [{ label: "Marketing", icon: <Megaphone />, to: "/marketing" }] : []),
      ...(hasRole("Excom Agent") || manager ? [{ label: "Agents", icon: <Bot />, to: "/agents" }] : []),
    ],
    [
      ...(manager ? [{ label: "Admin", icon: <Shield />, to: "/admin" }] : []),
      { label: "Merge suggestions", icon: <GitMerge />, to: "/merge", count: mergeCount },
      { label: "Subscribers", icon: <ListChecks />, to: "/subscribers" },
      { label: "Subscriber rules", icon: <Cog />, to: "/rules" },
      { label: "Settings", icon: <Settings />, to: "/settings" },
    ],
  ];
  const row = "border-b border-border [&_svg]:size-5 [&_svg]:text-ink-3";
  return (
    <PageFrame title="More" icon={<MoreHorizontal />} className="!p-0">
      <div className="-m-3">
        {/* What is waiting, and whether calls can reach this agent — the foot of the rail on a laptop. */}
        <div className="px-1.5 py-1.5 border-b border-border space-y-0.5">
          <AttentionBell />
          {hasCalls && <AvailabilityToggle />}
          <LinePicker />
        </div>
        {groups.map((g, gi) => (
          <div key={gi} className={gi > 0 ? "mt-2 border-t border-border" : ""}>
            {g.map((it) => (
              <Row key={it.to} dense onClick={() => navigate(it.to)} className={row}>
                {it.icon}<span className="flex-1 text-sm text-ink-1 truncate">{it.label}</span>
                {it.count ? <span className="text-xs text-ink-3 tabular-nums">{it.count}</span> : null}
              </Row>
            ))}
          </div>
        ))}
        <div className="mt-2 border-t border-border">
          <Row dense onClick={() => setPaletteOpen(true)} className={row}><Search /><span className="flex-1 text-sm">Search everything</span></Row>
          <Row dense onClick={() => setDensity(density === "compact" ? "comfortable" : "compact")} className={row}><Rows3 /><span className="flex-1 text-sm">Density: {density === "compact" ? "Compact" : "Comfortable"}</span></Row>
          <Row dense onClick={() => setFb(true)} className={row}><MessageSquareText /><span className="flex-1 text-sm">Send feedback</span></Row>
          <Row dense onClick={() => window.open("https://github.com/sagarrgarg/frappe_excom/issues", "_blank")} className={row}><Bug /><span className="flex-1 text-sm">Report issue</span></Row>
          <Row dense onClick={() => { window.location.href = "/?cmd=web_logout"; }} className={row}><LogOut /><span className="flex-1 text-sm">Sign out</span></Row>
        </div>
      </div>
      <FeedbackDialog open={fb} onOpenChange={setFb} />
    </PageFrame>
  );
}
