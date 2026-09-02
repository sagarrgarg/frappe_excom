import { CallMessageCard } from "./CallMessageCard";
import { useState, useMemo, useEffect, useRef, useCallback } from "react";
import { createPortal } from "react-dom";
import {
  Send,
  Paperclip,
  Image as ImageIcon,
  Smile,
  Bot,
  UserCheck,
  Check,
  CheckCheck,
  MessageCircle,
  Mail,
  Instagram,
  Phone,
  Loader2,
  Lock,
  StickyNote,
  Pin,
  ChevronDown,
  ChevronUp,
  X,
  Reply,
  ArrowRightLeft,
  FileText,
  Sticker,
  AlertCircle,
  RotateCcw,
  PanelRightClose,
  PanelRightOpen,
} from "lucide-react";
import { useFrappePostCall } from "frappe-react-sdk";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Badge } from "./ui/badge";
import { Tabs, TabsList, TabsTrigger } from "./ui/tabs";
import type { UnifiedContact, Message } from "../types";
import { useMessages } from "../hooks/useMessages";
import { useRealtimeMessages } from "../hooks/useRealtimeMessages";
import { useFileUpload } from "../hooks/useFileUpload";
import { usePinnedMessages } from "../hooks/usePinnedMessages";
import { CannedResponsePopover } from "./CannedResponsePopover";
import { MessageContextMenu, ReactionBar } from "./MessageContextMenu";
import { TagManager } from "./TagManager";
import { EmailMessageCard } from "./EmailMessageCard";
import { EmailCompose } from "./EmailCompose";
import { WhatsAppTemplatePicker } from "./WhatsAppTemplatePicker";
import { StickerPicker } from "./StickerPicker";
import { useEmailBody } from "../hooks/useEmailBody";
import { toast } from "sonner";
import {
  formatServerTime,
  formatServerDateTimeFull,
  formatServerLastSeen,
  formatServerShortDateTime,
  parseFrappeDateTime,
} from "../utils/datetime";

interface ChannelTabsViewProps {
  contact: UnifiedContact;
  onOpenAIAssistant: () => void;
  activeAccountId?: string;
  onAccountSwitch?: (accountId: string) => void;
  onRefreshThreads?: () => void;
  isIdentityPanelCollapsed?: boolean;
  onToggleIdentityPanel?: () => void;
}

const CHANNEL_ICONS: Record<string, React.ReactElement> = {
  whatsapp: <MessageCircle className="w-4 h-4" />,
  email: <Mail className="w-4 h-4" />,
  instagram: <Instagram className="w-4 h-4" />,
  calls: <Phone className="w-4 h-4" />,
};

const CHANNEL_LABELS: Record<string, string> = {
  whatsapp: "WhatsApp",
  email: "Email",
  instagram: "Instagram",
  calls: "Calls",
};

function getChannelIcon(channel?: string) {
  if (!channel) return null;
  return CHANNEL_ICONS[channel] || null;
}

function DeliveryIcon({ status }: { status?: string }) {
  switch (status) {
    case "queued":
      return <Loader2 className="w-3 h-3 text-zinc-600 animate-spin" />;
    case "sent":
      return <Check className="w-3 h-3 text-zinc-600" />;
    case "delivered":
      return <CheckCheck className="w-3 h-3 text-zinc-600" />;
    case "read":
      return <CheckCheck className="w-3 h-3 text-blue-700" />;
    case "failed":
      return <AlertCircle className="w-3 h-3 text-red-700" />;
    default:
      return null;
  }
}

const DELIVERY_TIMEOUT_MS = 10 * 60 * 1000;

function DeliveryTimer({ sentAt }: { sentAt: Date }) {
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const elapsed = now - sentAt.getTime();
  const remaining = Math.max(0, DELIVERY_TIMEOUT_MS - elapsed);

  if (remaining <= 0) return null;

  const mins = Math.floor(remaining / 60000);
  const secs = Math.floor((remaining % 60000) / 1000);
  const display = `${mins}:${secs.toString().padStart(2, "0")}`;

  return (
    <span className="text-[10px] text-zinc-600 flex items-center gap-1">
      <Loader2 className="w-2.5 h-2.5 animate-spin" />
      Checking delivery… {display}
    </span>
  );
}

export function ChannelTabsView({
  contact,
  onOpenAIAssistant,
  activeAccountId: parentAccountId,
  onAccountSwitch,
  onRefreshThreads,
  isIdentityPanelCollapsed,
  onToggleIdentityPanel,
}: ChannelTabsViewProps) {
  const [isHeaderCollapsed, setIsHeaderCollapsed] = useState(false);
  const [messageInput, setMessageInput] = useState("");
  const [selectedChannel, setSelectedChannel] = useState(contact.channels[0]);
  const [selectedAccountId, setSelectedAccountId] = useState(
    parentAccountId || contact.activeAccountId
  );
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setSelectedChannel(contact.channels[0]);
    setSelectedAccountId(parentAccountId || contact.activeAccountId);
  }, [contact.id]);

  useEffect(() => {
    if (parentAccountId && parentAccountId !== selectedAccountId) {
      setSelectedAccountId(parentAccountId);
      const account = contact.allAccounts.find((a) => a.id === parentAccountId);
      if (account) setSelectedChannel(account.channel);
    }
  }, [parentAccountId]);

  const { messages: threadMessages, isLoading: messagesLoading, refresh, autoClaimedBy } =
    useMessages(selectedAccountId || "");

  const handleRealtimeMessage = useCallback(() => {
    refresh();
  }, [refresh]);
  useRealtimeMessages(selectedAccountId, handleRealtimeMessage);

  // Toast and refresh when this thread open auto-assigns the thread to the current user
  const prevAutoClaimedRef = useRef<string | null>(null);
  useEffect(() => {
    if (autoClaimedBy && autoClaimedBy !== prevAutoClaimedRef.current) {
      prevAutoClaimedRef.current = autoClaimedBy;
      toast.success("Thread assigned to you", { duration: 3000 });
      onRefreshThreads?.();
    }
  }, [autoClaimedBy, onRefreshThreads]);

  const [isNoteMode, setIsNoteMode] = useState(false);
  const [showCannedPopover, setShowCannedPopover] = useState(false);
  const [cannedSearch, setCannedSearch] = useState("");

  const [contextMenu, setContextMenu] = useState<{
    message: Message;
    position: { x: number; y: number };
  } | null>(null);
  const [replyingTo, setReplyingTo] = useState<Message | null>(null);
  const [showPinned, setShowPinned] = useState(false);

  const {
    pinnedMessages,
    refresh: refreshPinned,
  } = usePinnedMessages(selectedAccountId || "");

  const handleContextMenu = useCallback(
    (e: React.MouseEvent, message: Message) => {
      e.preventDefault();
      setContextMenu({ message, position: { x: e.clientX, y: e.clientY } });
    },
    []
  );

  const handleReply = useCallback((message: Message) => {
    setReplyingTo(message);
    setIsNoteMode(false);
  }, []);

  const handleRefreshAll = useCallback(() => {
    refresh();
    refreshPinned();
  }, [refresh, refreshPinned]);

  const isEmailChannel = selectedChannel === "email";
  const { bodies: emailBodies, loading: emailBodyLoading, fetchBody: fetchEmailBody, retryFetch: retryEmailFetch } = useEmailBody();
  const [emailCompose, setEmailCompose] = useState<{
    show: boolean;
    to: string;
    subject: string;
    inReplyToGmailId: string;
  }>({ show: false, to: "", subject: "", inReplyToGmailId: "" });

  const handleReplyEmail = useCallback(
    (gmailMsgId: string, subject: string, to: string) => {
      setEmailCompose({ show: true, to, subject, inReplyToGmailId: gmailMsgId });
    },
    [],
  );

  const { call: sendMessageCall, loading: isSending } = useFrappePostCall(
    "excom.excom.api.chat.send_message"
  );

  const { call: sendNoteCall, loading: isSendingNote } = useFrappePostCall(
    "excom.excom.api.chat.send_internal_note"
  );

  const { call: assignThreadCall } = useFrappePostCall(
    "excom.excom.api.chat.assign_thread"
  );

  const { call: transferThreadCall } = useFrappePostCall(
    "excom.excom.api.chat.transfer_thread"
  );

  const { call: retryMessageCall } = useFrappePostCall(
    "excom.excom.api.chat.retry_message"
  );

  const [retryingMsgId, setRetryingMsgId] = useState<string | null>(null);

  const handleRetryMessage = useCallback(async (messageId: string) => {
    setRetryingMsgId(messageId);
    try {
      await retryMessageCall({ message_name: messageId });
      toast.success("Message resent");
      refresh();
    } catch {
      toast.error("Retry failed");
      refresh();
    } finally {
      setRetryingMsgId(null);
    }
  }, [retryMessageCall, refresh]);

  const { call: fetchAllTeams } = useFrappePostCall(
    "excom.excom.api.teams.get_all_teams"
  );

  const { call: fetchTeamMembers } = useFrappePostCall(
    "excom.excom.api.teams.get_team_members"
  );

  const [showTransferModal, setShowTransferModal] = useState(false);
  const [transferTeams, setTransferTeams] = useState<{ name: string; team_name: string }[]>([]);
  const [transferTarget, setTransferTarget] = useState("");
  const [transferUser, setTransferUser] = useState("");
  const [transferMembers, setTransferMembers] = useState<{ user: string; full_name: string; user_image: string }[]>([]);
  const [transferNote, setTransferNote] = useState("");
  const [showTemplatePicker, setShowTemplatePicker] = useState(false);
  const [showStickerPicker, setShowStickerPicker] = useState(false);
  const stickerBtnRef = useRef<HTMLButtonElement>(null);
  const [stickerPickerPos, setStickerPickerPos] = useState({ bottom: 0, right: 0 });

  const openTransferModal = useCallback(async () => {
    try {
      const res = await fetchAllTeams({});
      setTransferTeams((res as any)?.message || []);
      setTransferTarget("");
      setTransferUser("");
      setTransferMembers([]);
      setTransferNote("");
      setShowTransferModal(true);
    } catch {
      toast.error("Failed to load teams");
    }
  }, [fetchAllTeams]);

  const onTeamSelected = useCallback(async (teamName: string) => {
    setTransferTarget(teamName);
    setTransferUser("");
    setTransferMembers([]);
    if (!teamName) return;
    try {
      const res = await fetchTeamMembers({ team: teamName });
      setTransferMembers((res as any)?.message || []);
    } catch { /* keep empty */ }
  }, [fetchTeamMembers]);

  const handleFileReady = useCallback(
    async (fileUrl: string, messageType: string, _fileName: string) => {
      if (!selectedAccountId) return;
      try {
        await sendMessageCall({
          thread_id: selectedAccountId,
          message: "",
          message_type: messageType,
          media_url: fileUrl,
        });
        await refresh();
      } catch (err) {
        toast.error("Failed to send attachment");
      }
    },
    [selectedAccountId, sendMessageCall, refresh],
  );

  const {
    inputRef: fileInputRef,
    openFilePicker,
    handleFileChange,
    uploading,
    isDragging,
    handleDragOver,
    handleDragLeave,
    handleDrop,
    acceptedTypes,
  } = useFileUpload(handleFileReady);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [threadMessages]);

  const [optimisticMessages, setOptimisticMessages] = useState<
    { id: string; content: string; timestamp: Date }[]
  >([]);

  const handleSendMessage = async () => {
    const text = messageInput.trim();
    if (!text || !selectedAccountId || isSending || isSendingNote) return;

    if (isNoteMode) {
      setMessageInput("");
      try {
        await sendNoteCall({ thread_id: selectedAccountId, content: text });
        await refresh();
        toast.success("Internal note added");
      } catch (err) {
        setMessageInput(text);
        toast.error("Failed to add note");
      }
      return;
    }

    const tempId = `opt_${Date.now()}`;
    const currentReplyTo = replyingTo?.id || "";
    setMessageInput("");
    setReplyingTo(null);
    setOptimisticMessages((prev) => [
      ...prev,
      { id: tempId, content: text, timestamp: new Date() },
    ]);

    try {
      await sendMessageCall({
        thread_id: selectedAccountId,
        message: text,
        reply_to: currentReplyTo,
      });
      setOptimisticMessages((prev) => prev.filter((m) => m.id !== tempId));
      await refresh();
    } catch (err) {
      setOptimisticMessages((prev) => prev.filter((m) => m.id !== tempId));
      setMessageInput(text);
      toast.error("Failed to send message");
    }
  };

  const handleInputChange = (value: string) => {
    if (value.length > CHAR_LIMIT) return;
    setMessageInput(value);

    if (!isNoteMode && value.startsWith("/") && value.length > 1) {
      setShowCannedPopover(true);
      setCannedSearch(value.slice(1));
    } else if (!value.startsWith("/")) {
      setShowCannedPopover(false);
      setCannedSearch("");
    }
  };

  const handleCannedSelect = (content: string) => {
    setMessageInput(content);
    setShowCannedPopover(false);
    setCannedSearch("");
  };

  const CHAR_LIMIT = 4096;

  const accountsByChannel = useMemo(() => {
    const grouped: Record<string, typeof contact.allAccounts> = {};
    contact.allAccounts.forEach((account) => {
      if (!grouped[account.channel]) {
        grouped[account.channel] = [];
      }
      grouped[account.channel].push(account);
    });
    return grouped;
  }, [contact.allAccounts]);

  const selectedAccount = contact.allAccounts.find(
    (a) => a.id === selectedAccountId
  );
  const channelAccounts = accountsByChannel[selectedChannel] || [];
  const hasMultipleAccounts = channelAccounts.length > 1;

  return (
    <div className="flex-1 flex flex-col h-full min-w-0 overflow-hidden bg-white">
      {/* Header */}
      <div className="shrink-0 bg-zinc-50/80 backdrop-blur-sm border-b border-zinc-200">
        <div className={isHeaderCollapsed ? "px-3 py-1.5" : "p-3"}>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3 min-w-0">
              <div className="relative shrink-0">
                {contact.contactAvatar ? (
                  <img
                    src={contact.contactAvatar}
                    alt={contact.contactName}
                    className={`rounded-full object-cover ring-2 ring-zinc-300 ${
                      isHeaderCollapsed ? "w-7 h-7" : "w-10 h-10"
                    }`}
                  />
                ) : (
                  <div
                    className={`rounded-full bg-gradient-to-br from-blue-500 to-purple-600 ring-2 ring-zinc-300 flex items-center justify-center text-white text-sm font-medium ${
                      isHeaderCollapsed ? "w-7 h-7 text-xs" : "w-10 h-10"
                    }`}
                  >
                    {contact.contactName
                      .split(" ")
                      .map((w) => w[0])
                      .join("")
                      .slice(0, 2)
                      .toUpperCase()}
                  </div>
                )}
                <div
                  className={`absolute bottom-0 right-0 w-3 h-3 rounded-full border-2 border-zinc-200 ${
                    contact.status === "online"
                      ? "bg-green-500"
                      : contact.status === "away"
                      ? "bg-yellow-500"
                      : "bg-zinc-300"
                  }`}
                />
              </div>
              <div className="min-w-0">
                <h3 className="font-semibold text-zinc-900 truncate">
                  {contact.contactName}
                </h3>
                {!isHeaderCollapsed && (
                  <div className="flex items-center gap-2">
                    <p className="text-xs text-zinc-600 truncate">
                      {contact.status === "online"
                        ? "Active now"
                        : formatServerLastSeen(contact.timestamp)}
                    </p>
                    {contact.assignedTo && (
                      <>
                        <span className="text-zinc-500 shrink-0">&bull;</span>
                        <div className="flex items-center gap-1 shrink-0">
                          {contact.assignedTo.avatar && (
                            <img
                              src={contact.assignedTo.avatar}
                              alt={contact.assignedTo.name}
                              className="w-4 h-4 rounded-full"
                            />
                          )}
                          <span className="text-xs text-zinc-600">
                            Assigned to {contact.assignedTo.name}
                          </span>
                        </div>
                      </>
                    )}
                  </div>
                )}
              </div>
            </div>

            <div className="flex items-center gap-2 shrink-0">
              <TagManager threadId={selectedAccountId || ""} />
              {contact.aiStatus === "active" ? (
                <Badge className="bg-blue-500/10 text-blue-700 border-blue-500/20 border">
                  <Bot className="w-3 h-3 mr-1" />
                  AI Active
                </Badge>
              ) : (
                <Badge className="bg-green-500/10 text-green-700 border-green-500/20 border">
                  <UserCheck className="w-3 h-3 mr-1" />
                  Human Control
                </Badge>
              )}
                            <Button
                size="sm"
                className="bg-emerald-600 hover:bg-emerald-700 text-white font-medium shadow-sm"
                onClick={async () => {
                  const num = contact.contactInfo?.phone;
                  if (!num) {
                    toast.error("Contact phone number missing");
                    return;
                  }
                  try {
                    const res = await (window as any).frappe?.call?.({
                      method: "excom.excom.api.voice.initiate_call",
                      args: { to_number: num, thread_id: selectedAccountId }
                    });
                    if (res?.message?.status === "success") {
                      toast.success("Call initiated to " + num);
                    }
                  } catch (e: any) {
                    let msg = "Call failed";
                    if (e?._server_messages) {
                      try {
                        const parsed = JSON.parse(e._server_messages);
                        const inner = JSON.parse(parsed[0]);
                        msg = inner?.message || parsed[0];
                      } catch {}
                    } else if (e?.message) {
                      msg = e.message;
                    }
                    toast.error(msg);
                  }
                }}
              >
                <Phone className="w-4 h-4 mr-1.5" />
                Call
              </Button>
<Button
                size="sm"
                variant="outline"
                className="border-zinc-300 text-zinc-700 hover:bg-zinc-100 hover:text-zinc-900"
                onClick={openTransferModal}
              >
                <ArrowRightLeft className="w-4 h-4 mr-1.5" />
                Transfer
              </Button>
              <Button
                onClick={onOpenAIAssistant}
                className="bg-gradient-to-r from-blue-500 to-purple-600 hover:from-blue-600 hover:to-purple-700 text-white"
              >
                <Smile className="w-4 h-4 mr-2" />
                AI Assist
              </Button>
              <div className="w-px h-6 bg-zinc-200 mx-0.5" />
              <button
                type="button"
                onClick={() => setIsHeaderCollapsed((v) => !v)}
                title={isHeaderCollapsed ? "Expand header" : "Collapse header"}
                aria-label={isHeaderCollapsed ? "Expand header" : "Collapse header"}
                className="p-1.5 rounded-lg text-zinc-500 hover:text-zinc-900 hover:bg-zinc-100 transition-colors"
              >
                {isHeaderCollapsed ? (
                  <ChevronDown className="w-4 h-4" />
                ) : (
                  <ChevronUp className="w-4 h-4" />
                )}
              </button>
              {onToggleIdentityPanel && (
                <button
                  type="button"
                  onClick={onToggleIdentityPanel}
                  title={
                    isIdentityPanelCollapsed
                      ? "Show details panel"
                      : "Hide details panel for a bigger chat"
                  }
                  aria-label={
                    isIdentityPanelCollapsed ? "Show details panel" : "Hide details panel"
                  }
                  className="p-1.5 rounded-lg text-zinc-500 hover:text-zinc-900 hover:bg-zinc-100 transition-colors"
                >
                  {isIdentityPanelCollapsed ? (
                    <PanelRightOpen className="w-4 h-4" />
                  ) : (
                    <PanelRightClose className="w-4 h-4" />
                  )}
                </button>
              )}
            </div>
          </div>
        </div>
      </div>

      {showTransferModal && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={() => setShowTransferModal(false)}>
          <div className="bg-zinc-50 border border-zinc-300 rounded-xl w-full max-w-md p-4" onClick={(e) => e.stopPropagation()}>
            <h2 className="text-lg font-semibold text-zinc-900 mb-1">Transfer Conversation</h2>
            <p className="text-xs text-zinc-600 mb-3">
              Move this thread to another team, or assign to a specific member.
            </p>
            <div className="space-y-3">
              <div>
                <label className="text-xs text-zinc-600 mb-1 block">Target Team</label>
                <select
                  value={transferTarget}
                  onChange={(e) => onTeamSelected(e.target.value)}
                  className="w-full bg-zinc-100 border border-zinc-300 rounded-lg px-3 py-2 text-sm text-zinc-900 focus:outline-none focus:border-zinc-300"
                >
                  <option value="">Select team...</option>
                  {transferTeams.map((t) => (
                    <option key={t.name} value={t.name}>
                      {t.team_name}
                    </option>
                  ))}
                </select>
              </div>
              {transferTarget && transferMembers.length > 0 && (
                <div>
                  <label className="text-xs text-zinc-600 mb-1 block">
                    Assign to Member <span className="text-zinc-500">(optional — leave empty for team pickup)</span>
                  </label>
                  <select
                    value={transferUser}
                    onChange={(e) => setTransferUser(e.target.value)}
                    className="w-full bg-zinc-100 border border-zinc-300 rounded-lg px-3 py-2 text-sm text-zinc-900 focus:outline-none focus:border-zinc-300"
                  >
                    <option value="">Anyone in the team</option>
                    {transferMembers.map((m) => (
                      <option key={m.user} value={m.user}>
                        {m.full_name || m.user}
                      </option>
                    ))}
                  </select>
                </div>
              )}
              <div>
                <label className="text-xs text-zinc-600 mb-1 block">Note (optional)</label>
                <Input
                  value={transferNote}
                  onChange={(e) => setTransferNote(e.target.value)}
                  placeholder="Reason for transfer..."
                  className="bg-zinc-100 border-zinc-300 text-zinc-900"
                />
              </div>
            </div>
            <div className="flex justify-end gap-2 mt-4">
              <Button
                variant="outline"
                onClick={() => setShowTransferModal(false)}
                className="border-zinc-300 text-zinc-700"
              >
                Cancel
              </Button>
              <Button
                disabled={!transferTarget}
                className="bg-blue-600 hover:bg-blue-700 text-white"
                onClick={async () => {
                  try {
                    await transferThreadCall({
                      thread_id: selectedAccountId,
                      target_team: transferTarget,
                      target_user: transferUser,
                      note: transferNote,
                    });
                    toast.success(
                      transferUser
                        ? "Thread transferred and assigned"
                        : "Thread transferred to team"
                    );
                    setShowTransferModal(false);
                  } catch {
                    toast.error("Transfer failed");
                  }
                }}
              >
                Transfer
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Channel Tabs */}
      <div className="shrink-0 bg-zinc-50/60 border-b border-zinc-200">
        <Tabs
          value={selectedChannel}
          onValueChange={setSelectedChannel}
          className="w-full"
        >
          <TabsList className="w-full justify-start bg-transparent border-0 p-0 h-auto rounded-none">
            {contact.channels.map((channel) => {
              const accounts = accountsByChannel[channel] || [];
              return (
                <TabsTrigger
                  key={channel}
                  value={channel}
                  className="rounded-none border-b-2 border-transparent data-[state=active]:border-blue-500 data-[state=active]:bg-zinc-100/50 px-4 py-2 data-[state=active]:shadow-none"
                >
                  <div className="flex items-center gap-2">
                    {getChannelIcon(channel)}
                    <span>{CHANNEL_LABELS[channel] || channel}</span>
                    {accounts.length > 1 && (
                      <Badge className="ml-1 text-[9px] px-1.5 h-4 bg-blue-500/20 text-blue-700 border-0">
                        {accounts.length}
                      </Badge>
                    )}
                  </div>
                </TabsTrigger>
              );
            })}
          </TabsList>
        </Tabs>
      </div>

      {/* Account Selector */}
      {hasMultipleAccounts && (
        <div className="shrink-0 bg-zinc-50/40 border-b border-zinc-200 px-3 py-2">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs text-zinc-600">Account:</span>
            {channelAccounts.map((account) => (
              <button
                key={account.id}
                onClick={() => {
                  setSelectedAccountId(account.id);
                  onAccountSwitch?.(account.id);
                }}
                disabled={!account.hasAccess}
                className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
                  account.id === selectedAccountId
                    ? "bg-blue-500 text-white"
                    : account.hasAccess
                    ? "bg-zinc-100 text-zinc-700 hover:bg-zinc-200"
                    : "bg-zinc-100/50 text-zinc-500 cursor-not-allowed"
                }`}
              >
                <div className="flex items-center gap-1.5">
                  {getChannelIcon(account.channel)}
                  <span>{account.name}</span>
                  <span className="text-[10px] opacity-75">
                    ({account.identifier})
                  </span>
                </div>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Active Account Banner */}
      {selectedAccount && (
        <div className="shrink-0 px-3 py-2 bg-zinc-50/60 border-b border-zinc-200">
          <div className="bg-gradient-to-r from-blue-500/10 to-purple-500/10 border border-blue-500/30 rounded-lg p-2">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-xs min-w-0">
                {getChannelIcon(selectedAccount.channel)}
                <span className="text-zinc-600 shrink-0">Viewing & replying via:</span>
                <span className="font-medium text-zinc-900 truncate">
                  {selectedAccount.name} ({selectedAccount.identifier})
                </span>
              </div>
              {!selectedAccount.hasAccess && (
                <Badge className="text-[9px] px-2 h-5 bg-orange-500/20 text-orange-700 border-orange-500/30 shrink-0">
                  No Access
                </Badge>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Hidden file input */}
      <input
        ref={fileInputRef}
        type="file"
        accept={acceptedTypes}
        onChange={handleFileChange}
        className="hidden"
      />

      {/* Pinned Messages */}
      {pinnedMessages.length > 0 && (
        <div className="shrink-0 border-b border-zinc-200">
          <button
            onClick={() => setShowPinned(!showPinned)}
            className="w-full px-3 py-2 flex items-center gap-2 text-xs text-amber-700 hover:bg-zinc-100/50 transition-colors"
          >
            <Pin className="w-3.5 h-3.5" />
            <span className="font-medium">{pinnedMessages.length} pinned message{pinnedMessages.length > 1 ? "s" : ""}</span>
            {showPinned ? <ChevronUp className="w-3.5 h-3.5 ml-auto" /> : <ChevronDown className="w-3.5 h-3.5 ml-auto" />}
          </button>
          {showPinned && (
            <div className="px-3 pb-2 space-y-2 max-h-48 overflow-y-auto">
              {pinnedMessages.map((pm) => (
                <div key={pm.name} className="rounded-lg bg-amber-500/5 border border-amber-500/20 p-2">
                  <div className="flex items-center gap-1.5 mb-1">
                    <Pin className="w-3 h-3 text-amber-700" />
                    <span className="text-[10px] text-amber-700/70">{pm.sender_name || "Unknown"}</span>
                    <span className="text-[10px] text-zinc-600 ml-auto">
                      {formatServerShortDateTime(parseFrappeDateTime(pm.creation))}
                    </span>
                  </div>
                  <p className="text-xs text-zinc-700 line-clamp-2">{pm.content_text}</p>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Messages - scrollable area */}
      <div
        className={`flex-1 min-h-0 overflow-y-auto p-4 ${isDragging ? "ring-2 ring-blue-500 ring-inset bg-blue-500/5" : ""}`}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        <div className="space-y-3 max-w-4xl mx-auto">
          {messagesLoading ? (
            <div className="flex flex-col items-center justify-center h-64 text-center">
              <Loader2 className="w-8 h-8 text-blue-700 animate-spin mb-3" />
              <p className="text-zinc-600 text-sm">Loading messages...</p>
            </div>
          ) : threadMessages.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-64 text-center">
              <div className="w-16 h-16 rounded-full bg-zinc-100/50 flex items-center justify-center mb-3">
                {getChannelIcon(selectedChannel)}
              </div>
              <p className="text-zinc-600 text-sm">No messages yet</p>
              <p className="text-zinc-500 text-xs mt-1">
                Start a conversation with {contact.contactName}
              </p>
            </div>
          ) : (
            threadMessages.map((message, index) => {
              const isUser = message.sender === "user";
              const isAI = message.sender === "ai";
              const isNote = message.isInternal;
              const showTimestamp =
                index === 0 ||
                message.timestamp.getTime() -
                  threadMessages[index - 1].timestamp.getTime() >
                  300000;

              return (
                <div key={message.id}>
                  {showTimestamp && (
                    <div className="text-center text-xs text-zinc-600 my-4">
                      {formatServerDateTimeFull(message.timestamp)}
                    </div>
                  )}

                  {message.isEmail ? (
                    <EmailMessageCard
                      messageId={message.id}
                      direction={message.rawDirection || (message.sender === "user" ? "Outbound" : "Inbound")}
                      snippet={message.content}
                      timestamp={message.timestamp}
                      contentJson={message.contentJson || "{}"}
                      sentBy={message.sentBy}
                      bodyData={emailBodies[message.id]}
                      bodyLoading={emailBodyLoading[message.id]}
                      onExpandEmail={fetchEmailBody}
                      onReplyEmail={handleReplyEmail}
                      onRetryFetch={retryEmailFetch}
                    />
                  ) : isNote ? (
                    <div className="flex justify-center my-2">
                      <div className="max-w-[80%] w-full">
                        <div className="rounded-xl p-3 bg-amber-500/10 border border-amber-500/20 shadow-lg">
                          <div className="flex items-center gap-2 mb-1.5">
                            <Lock className="w-3 h-3 text-amber-700" />
                            <Badge className="text-[10px] px-1.5 h-4 bg-amber-500/20 text-amber-700 border-amber-500/30 border">
                              Internal Note
                            </Badge>
                            {message.sentBy && (
                              <span className="text-[10px] text-amber-700/70 ml-auto">
                                {message.sentBy.name}
                              </span>
                            )}
                          </div>
                          <p className="text-sm text-amber-100/90 leading-relaxed">
                            {message.content}
                          </p>
                          <div className="flex items-center gap-1 mt-2 text-[10px] text-amber-700/50">
                            <span>{formatServerTime(message.timestamp)}</span>
                          </div>
                        </div>
                      </div>
                    </div>
                  ) : (
                    <div
                      className={`flex ${
                        isUser || isAI ? "justify-end" : "justify-start"
                      }`}
                      onContextMenu={(e) => handleContextMenu(e, message)}
                    >
                      <div
                        className={`flex gap-2 max-w-[70%] ${
                          isUser || isAI ? "flex-row-reverse" : "flex-row"
                        }`}
                      >
                        {!isUser && !isAI && contact.contactAvatar && (
                          <img
                            src={contact.contactAvatar}
                            alt={contact.contactName}
                            className="w-8 h-8 rounded-full object-cover flex-shrink-0"
                          />
                        )}

                        <div>
                          {message.replyTo && (
                            <div className={`mb-1 rounded-lg p-2 border-l-2 ${
                              message.replyTo.direction === "Outbound"
                                ? "border-blue-500 bg-blue-500/10"
                                : "border-zinc-300 bg-zinc-100/50"
                            }`}>
                              <p className="text-[10px] text-zinc-600 mb-0.5">
                                {message.replyTo.sender || (message.replyTo.direction === "Outbound" ? "You" : contact.contactName)}
                              </p>
                              <p className="text-xs text-zinc-700 line-clamp-2">{message.replyTo.content}</p>
                            </div>
                          )}

                          <div
                            className={`rounded-2xl p-3 shadow-lg relative ${
                              message.status === "failed"
                                ? "bg-red-50 border border-red-200 text-red-700"
                                : isAI
                                ? "bg-gradient-to-br from-purple-500/10 to-blue-500/10 border border-purple-500/30 text-zinc-900"
                                : isUser
                                ? "bg-gradient-to-br from-blue-500 to-purple-600 text-white"
                                : "bg-zinc-100 text-zinc-900"
                            }`}
                          >
                            {message.isPinned && (
                              <Pin className="absolute -top-1.5 -right-1.5 w-3.5 h-3.5 text-amber-700" />
                            )}
                                                        {message.type === "call" || message.type === "Call" ? (
                              <CallMessageCard
                                message={{
                                  name: message.id,
                                  direction: isUser ? "Outbound" : "Inbound",
                                  content_text: message.content,
                                  creation: message.timestamp,
                                  delivery_status: message.status,
                                  call_id: (message as any).call_id,
                                  duration: (message as any).duration,
                                  recording_url: (message as any).recording_url || message.mediaUrl
                                }}
                              />
                            ) : message.type === "template" ? (
                              <div className="space-y-2">
                                {message.mediaUrl && (
                                  /\.(jpg|jpeg|png|gif|webp)$/i.test(message.mediaUrl) ? (
                                    <img
                                      src={message.mediaUrl}
                                      alt="Template media"
                                      className="rounded-lg max-w-sm"
                                    />
                                  ) : (
                                    <a
                                      href={message.mediaUrl}
                                      target="_blank"
                                      rel="noopener noreferrer"
                                      className="flex items-center gap-3 p-2 bg-zinc-200/50 rounded-lg hover:bg-zinc-200 transition-colors"
                                    >
                                      <div className="w-10 h-10 bg-zinc-300 rounded-lg flex items-center justify-center">
                                        <FileText className="w-5 h-5 text-zinc-700" />
                                      </div>
                                      <div>
                                        <p className="text-sm font-medium">Attachment</p>
                                        <p className="text-xs text-zinc-600">Document</p>
                                      </div>
                                    </a>
                                  )
                                )}
                                {message.content && (
                                  <p className="text-sm leading-relaxed">{message.content}</p>
                                )}
                                <span className="inline-block text-[10px] text-zinc-600 bg-zinc-200/50 px-2 py-0.5 rounded-full">
                                  Template
                                </span>
                              </div>
                            ) : message.type === "document" && message.mediaUrl ? (
                              <div className="flex items-center gap-3 p-2">
                                <div className="w-10 h-10 bg-zinc-200 rounded-lg flex items-center justify-center">
                                  <Paperclip className="w-5 h-5 text-zinc-700" />
                                </div>
                                <div>
                                  <p className="text-sm font-medium">
                                    {message.mediaUrl}
                                  </p>
                                  <p className="text-xs text-zinc-600">
                                    Document
                                  </p>
                                </div>
                              </div>
                            ) : message.type === "image" && message.mediaUrl ? (
                              <img
                                src={message.mediaUrl}
                                alt="Attached media"
                                className="rounded-lg max-w-sm"
                              />
                            ) : message.type === "sticker" && message.mediaUrl ? (
                              <img
                                src={message.mediaUrl}
                                alt="Sticker"
                                className="w-32 h-32 object-contain"
                              />
                            ) : (
                              <p className="text-sm leading-relaxed">
                                {message.content}
                              </p>
                            )}
                          </div>

                          {message.reactions && Object.keys(message.reactions).length > 0 && (
                            <ReactionBar
                              reactions={message.reactions}
                              messageId={message.id}
                              onRefresh={handleRefreshAll}
                            />
                          )}

                          <div
                            className={`flex items-center gap-2 mt-1 text-xs ${
                              isUser || isAI
                                ? "justify-end flex-row-reverse"
                                : "justify-start"
                            }`}
                          >
                            <div className="flex items-center gap-1 text-zinc-600">
                              <span>{formatServerTime(message.timestamp)}</span>
                              {(isUser || isAI) && (
                                <DeliveryIcon status={message.status} />
                              )}
                            </div>
                            {(isUser || isAI) && message.sentBy && (
                              <div className="flex items-center gap-1.5">
                                {message.sentBy.avatar && (
                                  <img
                                    src={message.sentBy.avatar}
                                    alt={message.sentBy.name}
                                    className="w-4 h-4 rounded-full"
                                  />
                                )}
                                {isAI ? (
                                  <Badge className="text-[10px] px-1.5 py-0 h-4 bg-purple-500/20 text-purple-700 border-purple-500/30">
                                    <Bot className="w-2.5 h-2.5 mr-0.5" />
                                    AI
                                  </Badge>
                                ) : (
                                  <span className="text-zinc-600 text-[10px]">
                                    {message.sentBy.name}
                                  </span>
                                )}
                              </div>
                            )}
                          </div>

                          {message.status === "failed" && isUser && (
                            <div className="flex items-center gap-2 mt-1">
                              <span className="text-[10px] text-red-700 truncate max-w-48">
                                {message.failureReason || "Delivery failed"}
                              </span>
                              <button
                                onClick={() => handleRetryMessage(message.id)}
                                disabled={retryingMsgId === message.id}
                                className="flex items-center gap-1 text-[11px] font-medium text-red-700 hover:text-red-700 transition-colors disabled:opacity-50"
                              >
                                {retryingMsgId === message.id ? (
                                  <Loader2 className="w-3 h-3 animate-spin" />
                                ) : (
                                  <RotateCcw className="w-3 h-3" />
                                )}
                                Retry
                              </button>
                            </div>
                          )}

                          {(message.status === "sent" || message.status === "queued") && isUser && (
                            <div className="mt-1 flex justify-end">
                              <DeliveryTimer sentAt={message.timestamp} />
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              );
            })
          )}
          {/* Optimistic (pending) messages */}
          {optimisticMessages.map((msg) => (
            <div key={msg.id} className="flex justify-end">
              <div className="max-w-[70%]">
                <div className="rounded-2xl p-3 shadow-lg bg-gradient-to-br from-blue-500 to-purple-600 text-white opacity-70">
                  <p className="text-sm leading-relaxed">{msg.content}</p>
                </div>
                <div className="flex items-center gap-1 mt-1 justify-end text-xs text-zinc-600">
                  <Loader2 className="w-3 h-3 animate-spin" />
                  <span>Sending...</span>
                </div>
              </div>
            </div>
          ))}
          <div ref={messagesEndRef} />
        </div>
      </div>

      {/* Email Compose */}
      {isEmailChannel && emailCompose.show && selectedAccountId && (
        <EmailCompose
          threadId={selectedAccountId}
          defaultTo={emailCompose.to}
          defaultSubject={emailCompose.subject}
          inReplyToGmailId={emailCompose.inReplyToGmailId}
          onClose={() => setEmailCompose({ show: false, to: "", subject: "", inReplyToGmailId: "" })}
          onSent={() => refresh()}
        />
      )}

      {/* Context Menu */}
      {contextMenu && (
        <MessageContextMenu
          message={contextMenu.message}
          position={contextMenu.position}
          onClose={() => setContextMenu(null)}
          onReply={handleReply}
          onRefresh={handleRefreshAll}
        />
      )}

      {/* WhatsApp Template Picker */}
      {showTemplatePicker && selectedAccountId && (
        <WhatsAppTemplatePicker
          threadId={selectedAccountId}
          onClose={() => setShowTemplatePicker(false)}
          onSent={() => refresh()}
        />
      )}

      {/* Reply Bar */}
      {replyingTo && (
        <div className="shrink-0 px-3 py-2 bg-zinc-50/80 border-t border-zinc-200">
          <div className="max-w-4xl mx-auto flex items-center gap-3 rounded-lg bg-blue-500/10 border border-blue-500/20 p-2">
            <Reply className="w-4 h-4 text-blue-700 shrink-0" />
            <div className="flex-1 min-w-0">
              <p className="text-[10px] text-blue-700 font-medium">
                Replying to {replyingTo.sentBy?.name || (replyingTo.sender === "user" ? "You" : contact.contactName)}
              </p>
              <p className="text-xs text-zinc-600 truncate">{replyingTo.content}</p>
            </div>
            <button
              onClick={() => setReplyingTo(null)}
              className="p-1 rounded hover:bg-zinc-200 text-zinc-600 hover:text-zinc-900 transition-colors shrink-0"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>
      )}

      {/* Input Area */}
      <div className={`shrink-0 backdrop-blur-sm border-t p-3 ${isNoteMode ? "bg-amber-500/5 border-amber-500/20" : "bg-zinc-50/80 border-zinc-200"}`}>
        <div className="max-w-4xl mx-auto">
          {/* Email compose button for email channels */}
          {isEmailChannel && !emailCompose.show && (
            <div className="mb-3 flex items-center gap-2">
              <button
                onClick={() => {
                  const contactEmail = contact.contactInfo.email || "";
                  setEmailCompose({ show: true, to: contactEmail, subject: "", inReplyToGmailId: "" });
                }}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-blue-500/10 text-blue-700 hover:bg-blue-500/20 text-xs font-medium rounded-lg border border-blue-500/20 transition-colors"
              >
                <Mail className="w-3.5 h-3.5" />
                Compose Email
              </button>
            </div>
          )}

          {contact.aiStatus === "active" && (
            <div className="mb-3 flex items-center gap-2 text-xs text-zinc-600">
              <Bot className="w-4 h-4 text-blue-700 shrink-0" />
              <span>
                AI is actively monitoring this conversation. Messages will be
                sent by AI unless you take over.
              </span>
              <Button
                size="sm"
                variant="outline"
                className="ml-auto border-blue-500/50 text-blue-700 hover:bg-blue-500/10 shrink-0"
                onClick={async () => {
                  if (!selectedAccountId) return;
                  try {
                    await assignThreadCall({ thread_id: selectedAccountId });
                    toast.success("Thread assigned to you");
                  } catch (err) {
                    toast.error("Failed to take over");
                  }
                }}
              >
                Take Over
              </Button>
            </div>
          )}

          {/* Message / Note Toggle */}
          <div className="flex items-center gap-1 mb-2">
            <button
              onClick={() => { setIsNoteMode(false); setShowCannedPopover(false); }}
              className={`px-3 py-1 rounded-lg text-xs font-medium transition-all ${
                !isNoteMode
                  ? "bg-blue-500 text-white"
                  : "bg-zinc-100 text-zinc-600 hover:bg-zinc-200 hover:text-zinc-900"
              }`}
            >
              <MessageCircle className="w-3 h-3 inline mr-1" />
              Message
            </button>
            <button
              onClick={() => { setIsNoteMode(true); setShowCannedPopover(false); }}
              className={`px-3 py-1 rounded-lg text-xs font-medium transition-all ${
                isNoteMode
                  ? "bg-amber-500 text-white"
                  : "bg-zinc-100 text-zinc-600 hover:bg-zinc-200 hover:text-zinc-900"
              }`}
            >
              <StickyNote className="w-3 h-3 inline mr-1" />
              Note
            </button>
            {isNoteMode && (
              <span className="text-[10px] text-amber-700/70 ml-2 flex items-center gap-1">
                <Lock className="w-3 h-3" />
                Only visible to your team
              </span>
            )}
          </div>

          <div className="flex items-end gap-3">
            {!isNoteMode && (
              <div className="flex gap-2 shrink-0">
                <Button
                  variant="ghost"
                  size="icon"
                  className="text-zinc-600 hover:text-zinc-900"
                  onClick={openFilePicker}
                  disabled={uploading}
                >
                  {uploading ? (
                    <Loader2 className="w-5 h-5 animate-spin" />
                  ) : (
                    <Paperclip className="w-5 h-5" />
                  )}
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  className="text-zinc-600 hover:text-zinc-900"
                  onClick={openFilePicker}
                  disabled={uploading}
                >
                  <ImageIcon className="w-5 h-5" />
                </Button>
                {selectedChannel === "whatsapp" && (
                  <>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="text-green-700 hover:text-green-700"
                      onClick={() => setShowTemplatePicker(true)}
                      title="Send WhatsApp Template"
                    >
                      <FileText className="w-5 h-5" />
                    </Button>
                    <div className="relative">
                      <Button
                        ref={stickerBtnRef}
                        variant="ghost"
                        size="icon"
                        className="text-yellow-700 hover:text-yellow-700"
                        onClick={() => {
                          if (!showStickerPicker && stickerBtnRef.current) {
                            const rect = stickerBtnRef.current.getBoundingClientRect();
                            setStickerPickerPos({
                              bottom: window.innerHeight - rect.top + 8,
                              right: window.innerWidth - rect.right,
                            });
                          }
                          setShowStickerPicker((v) => !v);
                        }}
                        title="Send Sticker"
                      >
                        <Sticker className="w-5 h-5" />
                      </Button>
                    </div>
                    {showStickerPicker && selectedAccountId && createPortal(
                      <div
                        style={{
                          position: "fixed",
                          bottom: stickerPickerPos.bottom,
                          right: stickerPickerPos.right,
                          zIndex: 100,
                        }}
                      >
                        <StickerPicker
                          threadId={selectedAccountId}
                          onClose={() => setShowStickerPicker(false)}
                          onSent={() => refresh()}
                        />
                      </div>,
                      document.body
                    )}
                  </>
                )}
              </div>
            )}

            <div className="flex-1 min-w-0 relative">
              <CannedResponsePopover
                isOpen={showCannedPopover}
                searchText={cannedSearch}
                channel={selectedAccount?.channel}
                onSelect={handleCannedSelect}
                onClose={() => { setShowCannedPopover(false); setCannedSearch(""); }}
              />
              <div className={`rounded-xl border transition-colors ${
                isNoteMode
                  ? "bg-amber-500/10 border-amber-500/30 focus-within:border-amber-400"
                  : "bg-zinc-100 border-zinc-300 focus-within:border-blue-500"
              }`}>
                <Input
                  placeholder={
                    isNoteMode
                      ? "Write an internal note..."
                      : `Type your message to ${selectedAccount?.identifier || "..."}... (type / for canned responses)`
                  }
                  value={messageInput}
                  onChange={(e) => handleInputChange(e.target.value)}
                  onKeyDown={(e) => {
                    if (showCannedPopover) return;
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      handleSendMessage();
                    }
                  }}
                  className={`border-0 bg-transparent focus-visible:ring-0 ${
                    isNoteMode
                      ? "text-amber-100 placeholder:text-amber-700/40"
                      : "text-zinc-900 placeholder:text-zinc-600"
                  }`}
                  disabled={!selectedAccount?.hasAccess}
                />
              </div>
              {messageInput.length > CHAR_LIMIT * 0.9 && (
                <div className={`text-right text-[10px] mt-1 ${messageInput.length >= CHAR_LIMIT ? "text-red-700" : "text-zinc-600"}`}>
                  {messageInput.length}/{CHAR_LIMIT}
                </div>
              )}
            </div>

            <Button
              onClick={handleSendMessage}
              className={`shrink-0 ${
                isNoteMode
                  ? "bg-amber-500 hover:bg-amber-600 text-white"
                  : "bg-gradient-to-r from-blue-500 to-purple-600 hover:from-blue-600 hover:to-purple-700 text-white"
              }`}
              disabled={!selectedAccount?.hasAccess || isSending || isSendingNote}
            >
              {isSending || isSendingNote ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : isNoteMode ? (
                <StickyNote className="w-4 h-4" />
              ) : (
                <Send className="w-4 h-4" />
              )}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
