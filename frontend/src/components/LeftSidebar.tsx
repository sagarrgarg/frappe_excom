import { useState, useEffect } from "react";
import type { ReactNode } from "react";
import { Search, MessageCircle, Tag, X, Users, Shield, GitMerge, Cog, Plus, Radio, Settings, BarChart3, Calendar, ChevronDown, Mail, Phone, Instagram, Bug } from "lucide-react";
import { Input } from "./ui/input";
import { Button } from "./ui/button";
import { useTags } from "../hooks/useTags";
import { useFrappePostCall } from "frappe-react-sdk";
import { useExcomBranding } from "../hooks/useBranding";

interface BroadcastFilterItem {
  name: string;
  broadcast_name: string;
  channel: string;
  status: string;
  total_recipients: number;
  sent_count: number;
  failed_count: number;
  creation: string;
}

interface LeftSidebarProps {
  selectedChannel: string;
  onChannelSelect: (channel: string) => void;
  searchQuery: string;
  onSearchChange: (query: string) => void;
  totalConversations: number;
  totalUnread: number;
  selectedTags?: string[];
  onTagFilterChange?: (tags: string[]) => void;
  onNavigateToSubscribers?: () => void;
  onNavigateToTeams?: () => void;
  onNavigateToMergeSuggestions?: () => void;
  onNavigateToSubscriberRules?: () => void;
  onNavigateToBroadcasts?: () => void;
  onNavigateToAnalytics?: () => void;
  onNavigateToSettings?: () => void;
  selectedTeamFilter?: string;
  onTeamFilterChange?: (team: string) => void;
  onNewConversation?: () => void;
  selectedBroadcast?: string;
  onBroadcastFilterChange?: (broadcast: string) => void;
  selectedBroadcastStatus?: string;
  onBroadcastStatusChange?: (status: string) => void;
  selectedAccountFilter?: string;
  onAccountFilterChange?: (account: string) => void;
  dateFrom?: string;
  dateTo?: string;
  onDateFromChange?: (date: string) => void;
  onDateToChange?: (date: string) => void;
}

interface ChannelAccount {
  name: string;
  label: string;
  channel: string;
}

const CHANNEL_CHIPS: { key: string; label: string; icon: ReactNode }[] = [
  { key: "all", label: "All", icon: <MessageCircle className="w-3.5 h-3.5" /> },
  { key: "whatsapp", label: "WA", icon: <MessageCircle className="w-3.5 h-3.5" /> },
  { key: "email", label: "Email", icon: <Mail className="w-3.5 h-3.5" /> },
  { key: "instagram", label: "IG", icon: <Instagram className="w-3.5 h-3.5" /> },
  { key: "voice", label: "Calls", icon: <Phone className="w-3.5 h-3.5" /> },
];

const CHANNEL_DISPLAY: Record<string, string> = {
  whatsapp: "WhatsApp",
  email: "Email",
  instagram: "Instagram",
  calls: "Calls",
  voice: "Calls",
};

export function LeftSidebar({
  selectedChannel,
  onChannelSelect,
  searchQuery,
  onSearchChange,
  totalConversations,
  totalUnread,
  selectedTags = [],
  onTagFilterChange,
  onNavigateToSubscribers,
  onNavigateToTeams,
  onNavigateToMergeSuggestions,
  onNavigateToSubscriberRules,
  onNavigateToBroadcasts,
  onNavigateToAnalytics,
  onNavigateToSettings,
  selectedTeamFilter = "",
  onTeamFilterChange,
  onNewConversation,
  selectedBroadcast = "",
  onBroadcastFilterChange,
  selectedBroadcastStatus = "",
  onBroadcastStatusChange,
  selectedAccountFilter = "",
  onAccountFilterChange,
  dateFrom = "",
  dateTo = "",
  onDateFromChange,
  onDateToChange,
}: LeftSidebarProps) {
  const { tags: allTags } = useTags();
  const [myTeams, setMyTeams] = useState<{ name: string; team_name: string }[]>([]);
  const [mergeBadge, setMergeBadge] = useState(0);
  const { call: fetchMyTeams } = useFrappePostCall("excom.excom.api.teams.get_my_teams");
  const { call: fetchMergeCount } = useFrappePostCall("excom.excom.api.merge_suggestions.get_suggestion_count");
  const { branding } = useExcomBranding();
  const [recentBroadcasts, setRecentBroadcasts] = useState<BroadcastFilterItem[]>([]);
  const [showBroadcastFilter, setShowBroadcastFilter] = useState(false);
  const { call: fetchRecentBroadcasts } = useFrappePostCall("excom.excom.api.chat.get_recent_broadcasts_for_filter");
  const [channelAccounts, setChannelAccounts] = useState<ChannelAccount[]>([]);
  const { call: fetchChannelAccounts } = useFrappePostCall("excom.excom.api.chat.get_channel_accounts");
  const [showDateFilter, setShowDateFilter] = useState(false);

  const today = () => new Date().toISOString().slice(0, 10);
  const daysAgo = (n: number) => {
    const d = new Date();
    d.setDate(d.getDate() - n);
    return d.toISOString().slice(0, 10);
  };
  const firstOfMonth = () => {
    const d = new Date();
    d.setDate(1);
    return d.toISOString().slice(0, 10);
  };

  useEffect(() => {
    fetchMyTeams({}).then((res) => {
      setMyTeams((res as any)?.message || []);
    }).catch(() => {});
    fetchMergeCount({}).then((res) => {
      setMergeBadge((res as any)?.message?.count || 0);
    }).catch(() => {});
    fetchRecentBroadcasts({}).then((res) => {
      setRecentBroadcasts((res as any)?.message || []);
    }).catch(() => {});
    fetchChannelAccounts({}).then((res) => {
      const raw: any[] = (res as any)?.message || [];
      setChannelAccounts(raw.map((a) => ({
        name: a.name,
        label: a.account_name || a.email_address || a.wa_phone_id || a.name,
        channel: a.channel,
      })));
    }).catch(() => {});
  }, []);
  return (
    <div className="w-64 bg-gradient-to-b from-zinc-100 to-white border-r border-zinc-200 flex flex-col h-full shrink-0 overflow-hidden">
      <div className="shrink-0 p-3 border-b border-zinc-200">
        {branding?.show_app_name && (
          <div className="mb-3 text-[11px] font-semibold tracking-wider uppercase text-zinc-600">
            {branding.app_name || "Excom"}
          </div>
        )}
        <div className="flex items-center gap-3 mb-4">
          <div
            className="w-10 h-10 rounded-lg flex items-center justify-center"
            style={{
              background: `linear-gradient(to bottom right, ${branding?.logo_gradient_from || "#3b82f6"}, ${branding?.logo_gradient_to || "#9333ea"})`,
            }}
          >
            <MessageCircle className="w-6 h-6 text-zinc-900" />
          </div>
          <div>
            <h1 className="font-semibold text-zinc-900">Excom</h1>
            <p className="text-xs text-zinc-600">Communication Hub</p>
          </div>
        </div>

        <div className="relative mb-3">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-600" />
          <Input
            placeholder="Search conversations..."
            value={searchQuery}
            onChange={(e) => onSearchChange(e.target.value)}
            className="pl-10"
          />
        </div>

        {/* Two-tier channel / account chip filter */}
        <div className="space-y-2 mb-1">
          {/* Tier 1: Channel chips */}
          <div className="flex flex-wrap gap-1">
            {CHANNEL_CHIPS.map(({ key, label, icon }) => {
              const isActive = selectedChannel === key && !selectedAccountFilter;
              return (
                <button
                  key={key}
                  onClick={() => {
                    onChannelSelect(key);
                    onAccountFilterChange?.("");
                  }}
                  className={`inline-flex items-center gap-1 px-2 py-1 rounded-full text-[11px] font-medium transition-all border ${
                    isActive
                      ? "bg-blue-500/20 text-blue-700 border-blue-500/50"
                      : "bg-zinc-100/50 text-zinc-600 border-zinc-300 hover:text-zinc-900 hover:border-zinc-300"
                  }`}
                >
                  {icon}
                  {label}
                </button>
              );
            })}
          </div>

          {/* Tier 2: Account sub-filter (only when a specific channel is selected) */}
          {selectedChannel !== "all" && channelAccounts.filter((a) => a.channel === selectedChannel).length > 0 && (
            <div className="flex flex-wrap gap-1 pl-1">
              <button
                onClick={() => onAccountFilterChange?.("")}
                className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium transition-all border ${
                  !selectedAccountFilter
                    ? "bg-blue-500/15 text-blue-700 border-blue-500/40"
                    : "bg-zinc-100/30 text-zinc-600 border-zinc-300/60 hover:text-zinc-700"
                }`}
              >
                All {CHANNEL_DISPLAY[selectedChannel] || selectedChannel}
              </button>
              {channelAccounts
                .filter((a) => a.channel === selectedChannel)
                .map((acc) => (
                  <button
                    key={acc.name}
                    onClick={() => onAccountFilterChange?.(acc.name)}
                    className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium transition-all border truncate max-w-[120px] ${
                      selectedAccountFilter === acc.name
                        ? "bg-blue-500/15 text-blue-700 border-blue-500/40"
                        : "bg-zinc-100/30 text-zinc-600 border-zinc-300/60 hover:text-zinc-700"
                    }`}
                    title={acc.label}
                  >
                    {acc.label}
                  </button>
                ))}
            </div>
          )}
        </div>

        <div className="flex gap-2">
          {onNewConversation && (
            <Button
              size="sm"
              onClick={onNewConversation}
              className="flex-1 bg-gradient-to-r from-blue-500 to-purple-600 hover:from-blue-600 hover:to-purple-700 text-white"
              title="New Conversation"
            >
              <Plus className="w-3.5 h-3.5 mr-1.5" />
              New Chat
            </Button>
          )}
        </div>
      </div>

      <div className="shrink-0 p-3 border-b border-zinc-200">
        <div className="grid grid-cols-2 gap-3">
          <div className="bg-zinc-100/50 rounded-lg p-3">
            <div className="text-2xl font-semibold text-zinc-900">
              {totalConversations}
            </div>
            <div className="text-xs text-zinc-600">Total Chats</div>
          </div>
          <div className="bg-zinc-100/50 rounded-lg p-3">
            <div className="text-2xl font-semibold text-blue-700">
              {totalUnread}
            </div>
            <div className="text-xs text-zinc-600">Unread</div>
          </div>
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto p-3 space-y-3">
        {allTags.length > 0 && (
          <div>
            <div className="flex items-center gap-1.5 mb-2">
              <Tag className="w-3.5 h-3.5 text-zinc-600" />
              <span className="text-xs font-medium text-zinc-600">Filter by Tag</span>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {allTags.map((tag) => {
                const isSelected = selectedTags.includes(tag.name);
                return (
                  <button
                    key={tag.name}
                    onClick={() => {
                      if (!onTagFilterChange) return;
                      if (isSelected) {
                        onTagFilterChange(selectedTags.filter((t) => t !== tag.name));
                      } else {
                        onTagFilterChange([...selectedTags, tag.name]);
                      }
                    }}
                    className="inline-flex items-center gap-1 px-2 py-1 rounded text-[11px] font-medium transition-all"
                    style={{
                      backgroundColor: isSelected ? `${tag.color}30` : `${tag.color}10`,
                      color: tag.color,
                      border: `1px solid ${isSelected ? tag.color : `${tag.color}30`}`,
                    }}
                  >
                    <span
                      className="w-2 h-2 rounded-full"
                      style={{ backgroundColor: tag.color }}
                    />
                    {tag.tag_name}
                    {isSelected && <X className="w-3 h-3 ml-0.5" />}
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {onDateFromChange && onDateToChange && (
          <div>
            <button
              onClick={() => setShowDateFilter(!showDateFilter)}
              className="flex items-center gap-1.5 mb-2 w-full"
            >
              <Calendar className="w-3.5 h-3.5 text-blue-700" />
              <span className="text-xs font-medium text-zinc-600">Date Filter</span>
              {(dateFrom || dateTo) && (
                <span className="ml-1.5 px-1.5 py-0.5 rounded text-[10px] bg-blue-500/20 text-blue-700 border border-blue-500/30 shrink-0">
                  active
                </span>
              )}
              {(dateFrom || dateTo) && (
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    onDateFromChange("");
                    onDateToChange("");
                  }}
                  className="ml-auto mr-0.5"
                  title="Clear date filter"
                >
                  <X className="w-3 h-3 text-zinc-600 hover:text-zinc-700" />
                </button>
              )}
              <ChevronDown className={`w-3 h-3 text-zinc-600 ${!dateFrom && !dateTo ? "ml-auto" : ""} transition-transform ${showDateFilter ? "rotate-180" : ""}`} />
            </button>
            {showDateFilter && (
              <div className="space-y-2">
                {/* Quick presets */}
                <div className="flex flex-wrap gap-1">
                  {[
                    { label: "Today", from: today(), to: today() },
                    { label: "Yesterday", from: daysAgo(1), to: daysAgo(1) },
                    { label: "7 days", from: daysAgo(7), to: today() },
                    { label: "30 days", from: daysAgo(30), to: today() },
                    { label: "This month", from: firstOfMonth(), to: today() },
                  ].map((preset) => {
                    const isActive = dateFrom === preset.from && dateTo === preset.to;
                    return (
                      <button
                        key={preset.label}
                        onClick={() => {
                          if (isActive) {
                            onDateFromChange("");
                            onDateToChange("");
                          } else {
                            onDateFromChange(preset.from);
                            onDateToChange(preset.to);
                          }
                        }}
                        className={`px-2 py-0.5 rounded text-[10px] font-medium transition-all border ${
                          isActive
                            ? "bg-blue-500/20 text-blue-700 border-blue-500"
                            : "bg-zinc-100/50 text-zinc-600 border-zinc-300 hover:text-zinc-700"
                        }`}
                      >
                        {preset.label}
                      </button>
                    );
                  })}
                </div>
                {/* Custom range */}
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="text-[10px] text-zinc-600 mb-0.5 block">From</label>
                    <input
                      type="date"
                      value={dateFrom}
                      max={dateTo || undefined}
                      onChange={(e) => onDateFromChange(e.target.value)}
                      className="w-full bg-zinc-100/50 border border-zinc-300 rounded px-2 py-1 text-xs text-zinc-700 focus:outline-none focus:border-blue-500"
                    />
                  </div>
                  <div>
                    <label className="text-[10px] text-zinc-600 mb-0.5 block">To</label>
                    <input
                      type="date"
                      value={dateTo}
                      min={dateFrom || undefined}
                      max={today()}
                      onChange={(e) => onDateToChange(e.target.value)}
                      className="w-full bg-zinc-100/50 border border-zinc-300 rounded px-2 py-1 text-xs text-zinc-700 focus:outline-none focus:border-blue-500"
                    />
                  </div>
                </div>
              </div>
            )}
          </div>
        )}

        {myTeams.length > 0 && onTeamFilterChange && (
          <div>
            <div className="flex items-center gap-1.5 mb-2">
              <Shield className="w-3.5 h-3.5 text-zinc-600" />
              <span className="text-xs font-medium text-zinc-600">Team Filter</span>
            </div>
            <div className="flex flex-wrap gap-1.5">
              <button
                onClick={() => onTeamFilterChange("")}
                className={`px-2 py-1 rounded text-[11px] font-medium transition-all border ${
                  selectedTeamFilter === ""
                    ? "bg-blue-500/20 text-blue-700 border-blue-500"
                    : "bg-zinc-100/50 text-zinc-600 border-zinc-300 hover:text-zinc-700"
                }`}
              >
                All
              </button>
              {myTeams.map((t) => (
                <button
                  key={t.name}
                  onClick={() => onTeamFilterChange(t.name === "General" ? "__general__" : t.name)}
                  className={`px-2 py-1 rounded text-[11px] font-medium transition-all border ${
                    (t.name === "General" ? "__general__" : t.name) === selectedTeamFilter
                      ? t.name === "General"
                        ? "bg-amber-500/20 text-amber-700 border-amber-500"
                        : "bg-blue-500/20 text-blue-700 border-blue-500"
                      : "bg-zinc-100/50 text-zinc-600 border-zinc-300 hover:text-zinc-700"
                  }`}
                >
                  {t.team_name}
                </button>
              ))}
            </div>
          </div>
        )}

        {recentBroadcasts.length > 0 && onBroadcastFilterChange && (
          <div>
            <button
              onClick={() => setShowBroadcastFilter(!showBroadcastFilter)}
              className="flex items-center gap-1.5 mb-2 w-full"
            >
              <Radio className="w-3.5 h-3.5 text-emerald-700" />
              <span className="text-xs font-medium text-zinc-600">Broadcast Filter</span>
              <ChevronDown className={`w-3 h-3 text-zinc-600 ml-auto transition-transform ${showBroadcastFilter ? "rotate-180" : ""}`} />
            </button>
            {showBroadcastFilter && (
              <div className="space-y-1.5">
                <button
                  onClick={() => { onBroadcastFilterChange(""); onBroadcastStatusChange?.(""); }}
                  className={`w-full text-left px-2 py-1.5 rounded text-[11px] font-medium transition-all border ${
                    !selectedBroadcast
                      ? "bg-emerald-500/20 text-emerald-700 border-emerald-500"
                      : "bg-zinc-100/50 text-zinc-600 border-zinc-300 hover:text-zinc-700"
                  }`}
                >
                  None (show all)
                </button>
                {recentBroadcasts.map((b) => (
                  <button
                    key={b.name}
                    onClick={() => { onBroadcastFilterChange(b.name); onBroadcastStatusChange?.(""); }}
                    className={`w-full text-left px-2 py-1.5 rounded text-[11px] transition-all border ${
                      selectedBroadcast === b.name
                        ? "bg-emerald-500/20 text-emerald-700 border-emerald-500"
                        : "bg-zinc-100/50 text-zinc-600 border-zinc-300 hover:text-zinc-700"
                    }`}
                  >
                    <div className="font-medium truncate">{b.broadcast_name}</div>
                    <div className="flex items-center gap-2 mt-0.5 text-[10px] text-zinc-600">
                      <span>{b.channel}</span>
                      <span>{b.sent_count}/{b.total_recipients} sent</span>
                      {b.failed_count > 0 && <span className="text-red-700">{b.failed_count} failed</span>}
                    </div>
                  </button>
                ))}
                {selectedBroadcast && onBroadcastStatusChange && (
                  <div className="flex flex-wrap gap-1 pt-1 border-t border-zinc-200">
                    {["", "Sent", "Failed"].map((st) => (
                      <button
                        key={st || "all"}
                        onClick={() => onBroadcastStatusChange(st)}
                        className={`px-2 py-0.5 rounded text-[10px] font-medium transition-all border ${
                          selectedBroadcastStatus === st
                            ? st === "Failed"
                              ? "bg-red-500/20 text-red-700 border-red-500"
                              : "bg-emerald-500/20 text-emerald-700 border-emerald-500"
                            : "bg-zinc-100/50 text-zinc-600 border-zinc-300 hover:text-zinc-600"
                        }`}
                      >
                        {st || "All"}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        <div className="space-y-0.5">
          {onNavigateToSubscribers && (
            <button
              onClick={onNavigateToSubscribers}
              className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm text-zinc-700 hover:bg-zinc-100 hover:text-zinc-900 transition-colors"
            >
              <Users className="w-4 h-4 text-blue-700" />
              Subscriber Lists
            </button>
          )}

          {onNavigateToTeams && (
            <button
              onClick={onNavigateToTeams}
              className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm text-zinc-700 hover:bg-zinc-100 hover:text-zinc-900 transition-colors"
            >
              <Shield className="w-4 h-4 text-purple-700" />
              Teams
            </button>
          )}

          {onNavigateToMergeSuggestions && (
            <button
              onClick={onNavigateToMergeSuggestions}
              className="w-full flex items-center justify-between px-3 py-2 rounded-lg text-sm text-zinc-700 hover:bg-zinc-100 hover:text-zinc-900 transition-colors"
            >
              <span className="flex items-center gap-2.5">
                <GitMerge className="w-4 h-4 text-green-700" />
                Merge Suggestions
              </span>
              {mergeBadge > 0 && (
                <span className="bg-green-500/20 text-green-700 text-[10px] font-semibold px-1.5 py-0.5 rounded-full">
                  {mergeBadge}
                </span>
              )}
            </button>
          )}

          {onNavigateToSubscriberRules && (
            <button
              onClick={onNavigateToSubscriberRules}
              className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm text-zinc-700 hover:bg-zinc-100 hover:text-zinc-900 transition-colors"
            >
              <Cog className="w-4 h-4 text-amber-700" />
              Subscriber Rules
            </button>
          )}

          {onNavigateToBroadcasts && (
            <button
              onClick={onNavigateToBroadcasts}
              className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm text-zinc-700 hover:bg-zinc-100 hover:text-zinc-900 transition-colors"
            >
              <Radio className="w-4 h-4 text-emerald-700" />
              Broadcasts
            </button>
          )}

          {onNavigateToAnalytics && (
            <button
              onClick={onNavigateToAnalytics}
              className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm text-zinc-700 hover:bg-zinc-100 hover:text-zinc-900 transition-colors"
            >
              <BarChart3 className="w-4 h-4 text-blue-700" />
              Analytics
            </button>
          )}

          {onNavigateToSettings && (
            <button
              onClick={onNavigateToSettings}
              className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm text-zinc-700 hover:bg-zinc-100 hover:text-zinc-900 transition-colors"
            >
              <Settings className="w-4 h-4 text-zinc-600" />
              Settings
            </button>
          )}

          <a
            href="https://github.com/sagarrgarg/frappe_excom/issues"
            target="_blank"
            rel="noopener noreferrer"
            className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm text-zinc-600 hover:bg-zinc-100 hover:text-zinc-900 transition-colors"
          >
            <Bug className="w-4 h-4" />
            Report Issue
          </a>
        </div>

        <div className="bg-gradient-to-br from-blue-500/10 to-purple-500/10 rounded-lg p-3 border border-blue-500/20">
          <h3 className="text-sm font-medium text-zinc-900 mb-2">
            AI-Powered Communication
          </h3>
          <p className="text-xs text-zinc-600 leading-relaxed">
            Conversations are automatically handled by AI until a team member
            takes over. Real-time status tracking ensures seamless handoffs.
          </p>
        </div>
      </div>
    </div>
  );
}
