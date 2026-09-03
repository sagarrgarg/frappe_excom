import { useState, useMemo, useEffect, useRef, useCallback } from "react";
import { ArrowLeft, Phone, Video, Info, Send, Paperclip, Smile, Check, CheckCheck, MessageCircle, Mail, Instagram, Bot, UserCheck, Loader2, Lock, StickyNote, FileText, Sticker as StickerIcon, AlertCircle, RotateCcw } from "lucide-react";
import { useFrappePostCall } from "frappe-react-sdk";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Badge } from "../ui/badge";
import { Tabs, TabsList, TabsTrigger } from "../ui/tabs";
import type { UnifiedContact } from "../../types";
import { useMessages } from "../../hooks/useMessages";
import { useRealtimeMessages } from "../../hooks/useRealtimeMessages";
import { useFileUpload } from "../../hooks/useFileUpload";
import { CannedResponsePopover } from "../CannedResponsePopover";
import { StickerPicker } from "../StickerPicker";
import { toast } from "sonner";
import { formatServerTime, formatServerShortDateTime } from "../../utils/datetime";

interface MobileChannelViewProps {
  contact: UnifiedContact;
  onBack: () => void;
  onCall: (type: "voice" | "video") => void;
  onOpenContact: () => void;
  onOpenAI: () => void;
}

const CHANNEL_ICONS: Record<string, React.ReactElement> = {
  whatsapp: <MessageCircle className="w-3 h-3" />,
  email: <Mail className="w-3 h-3" />,
  instagram: <Instagram className="w-3 h-3" />,
  calls: <Phone className="w-3 h-3" />,
};

const CHANNEL_LABELS: Record<string, string> = {
  whatsapp: "WhatsApp",
  email: "Email",
  instagram: "IG",
  calls: "Calls",
};

function getChannelIcon(channel?: string) {
  if (!channel) return null;
  return CHANNEL_ICONS[channel] || null;
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

export function MobileChannelView({ contact, onBack, onCall, onOpenContact, onOpenAI }: MobileChannelViewProps) {
  const [messageInput, setMessageInput] = useState("");
  const [selectedChannel, setSelectedChannel] = useState(contact.channels[0]);
  const [selectedAccountId, setSelectedAccountId] = useState(contact.activeAccountId);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setSelectedChannel(contact.channels[0]);
    setSelectedAccountId(contact.activeAccountId);
  }, [contact.id]);

  const { messages: threadMessages, isLoading: messagesLoading, refresh } =
    useMessages(selectedAccountId || "");

  const handleRealtimeMessage = useCallback(() => {
    refresh();
  }, [refresh]);
  useRealtimeMessages(selectedAccountId, handleRealtimeMessage);

  const [isNoteMode, setIsNoteMode] = useState(false);
  const [showCannedPopover, setShowCannedPopover] = useState(false);
  const [cannedSearch, setCannedSearch] = useState("");
  const [showStickerPicker, setShowStickerPicker] = useState(false);

  const { call: sendMessageCall, loading: isSending } = useFrappePostCall(
    "excom.excom.api.chat.send_message"
  );
  const { call: sendNoteCall, loading: isSendingNote } = useFrappePostCall(
    "excom.excom.api.chat.send_internal_note"
  );
  const { call: assignThreadCall } = useFrappePostCall(
    "excom.excom.api.chat.assign_thread"
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
    acceptedTypes,
  } = useFileUpload(handleFileReady);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [threadMessages]);

  const [optimisticMessages, setOptimisticMessages] = useState<
    { id: string; content: string; timestamp: Date }[]
  >([]);
  const CHAR_LIMIT = 4096;

  const handleSendMessage = async () => {
    const text = messageInput.trim();
    if (!text || !selectedAccountId || isSending || isSendingNote) return;

    if (isNoteMode) {
      setMessageInput("");
      try {
        await sendNoteCall({ thread_id: selectedAccountId, content: text });
        await refresh();
        toast.success("Note added");
      } catch {
        setMessageInput(text);
        toast.error("Failed to add note");
      }
      return;
    }

    const tempId = `opt_${Date.now()}`;
    setMessageInput("");
    setOptimisticMessages((prev) => [
      ...prev,
      { id: tempId, content: text, timestamp: new Date() },
    ]);

    try {
      await sendMessageCall({ thread_id: selectedAccountId, message: text });
      setOptimisticMessages((prev) => prev.filter((m) => m.id !== tempId));
      await refresh();
    } catch (err) {
      setOptimisticMessages((prev) => prev.filter((m) => m.id !== tempId));
      setMessageInput(text);
      toast.error("Failed to send message");
    }
  };

  const handleMobileInputChange = (value: string) => {
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

  const accountsByChannel = useMemo(() => {
    const grouped: Record<string, typeof contact.allAccounts> = {};
    contact.allAccounts.forEach((account) => {
      if (!grouped[account.channel]) grouped[account.channel] = [];
      grouped[account.channel].push(account);
    });
    return grouped;
  }, [contact.allAccounts]);

  const selectedAccount = contact.allAccounts.find((a) => a.id === selectedAccountId);
  const channelAccounts = accountsByChannel[selectedChannel] || [];
  const hasMultipleAccounts = channelAccounts.length > 1;

  return (
    <div className="flex flex-col h-full overflow-hidden bg-white">
      {/* Header */}
      <div className="shrink-0 bg-zinc-50 border-b border-zinc-200 pb-2">
        <div className="p-3 flex items-center justify-between">
          <div className="flex items-center gap-3 flex-1 min-w-0">
            <Button onClick={onBack} variant="ghost" size="icon" className="text-zinc-600 hover:text-zinc-900 flex-shrink-0 h-9 w-9">
              <ArrowLeft className="w-5 h-5" />
            </Button>
            <div className="flex items-center gap-2 flex-1 min-w-0">
              <div className="relative flex-shrink-0">
                {contact.contactAvatar ? (
                  <img src={contact.contactAvatar} alt={contact.contactName} className="w-9 h-9 rounded-full object-cover" />
                ) : (
                  <div className="w-9 h-9 rounded-full bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center text-white text-xs font-medium">
                    {contact.contactName.split(" ").map((w) => w[0]).join("").slice(0, 2).toUpperCase()}
                  </div>
                )}
                <div className={`absolute bottom-0 right-0 w-2.5 h-2.5 rounded-full border-2 border-zinc-200 ${contact.status === "online" ? "bg-green-500" : contact.status === "away" ? "bg-yellow-500" : "bg-zinc-300"}`} />
              </div>
              <div className="flex-1 min-w-0">
                <h3 className="font-medium text-zinc-900 text-sm truncate">{contact.contactName}</h3>
                <p className="text-xs text-zinc-600 truncate">{contact.status === "online" ? "Active now" : "Offline"}</p>
              </div>
            </div>
          </div>
          <div className="flex items-center gap-1 flex-shrink-0">
            <Button onClick={() => onCall("voice")} variant="ghost" size="icon" className="text-zinc-600 hover:text-zinc-900 h-9 w-9"><Phone className="w-4 h-4" /></Button>
            <Button onClick={() => onCall("video")} variant="ghost" size="icon" className="text-zinc-600 hover:text-zinc-900 h-9 w-9"><Video className="w-4 h-4" /></Button>
            <Button onClick={onOpenContact} variant="ghost" size="icon" className="text-zinc-600 hover:text-zinc-900 h-9 w-9"><Info className="w-4 h-4" /></Button>
          </div>
        </div>

        <div className="px-3 flex items-center gap-2">
          {contact.aiStatus === "active" ? (
            <Badge className="bg-blue-500/10 text-blue-700 border-blue-500/20 border text-xs"><Bot className="w-3 h-3 mr-1" />AI Active</Badge>
          ) : (
            <Badge className="bg-green-500/10 text-green-700 border-green-500/20 border text-xs"><UserCheck className="w-3 h-3 mr-1" />Human Control</Badge>
          )}
          {contact.assignedTo && (
            <div className="flex items-center gap-1.5">
              {contact.assignedTo.avatar && <img src={contact.assignedTo.avatar} alt={contact.assignedTo.name} className="w-4 h-4 rounded-full" />}
              <span className="text-xs text-zinc-600">{contact.assignedTo.name}</span>
            </div>
          )}
        </div>

        <div className="px-3 mt-2 overflow-x-auto">
          <Tabs value={selectedChannel} onValueChange={setSelectedChannel}>
            <TabsList className="bg-zinc-100/50 border border-zinc-300 h-8">
              {contact.channels.map((channel) => {
                const accounts = accountsByChannel[channel] || [];
                return (
                  <TabsTrigger key={channel} value={channel} className="data-[state=active]:bg-zinc-200 text-xs px-3 h-7">
                    <div className="flex items-center gap-1">
                      {getChannelIcon(channel)}
                      <span>{CHANNEL_LABELS[channel] || channel}</span>
                      {accounts.length > 1 && (
                        <Badge className="ml-1 text-[9px] px-1 h-3.5 bg-blue-500/20 text-blue-700 border-0">{accounts.length}</Badge>
                      )}
                    </div>
                  </TabsTrigger>
                );
              })}
            </TabsList>
          </Tabs>
        </div>

        {hasMultipleAccounts && (
          <div className="px-3 mt-2">
            <div className="bg-zinc-100/30 rounded-lg p-2 space-y-1.5">
              <span className="text-[10px] text-zinc-600">Select Account:</span>
              <div className="flex flex-col gap-1.5">
                {channelAccounts.map((account) => (
                  <button
                    key={account.id}
                    onClick={() => setSelectedAccountId(account.id)}
                    disabled={!account.hasAccess}
                    className={`w-full px-2.5 py-2 rounded-md text-xs font-medium transition-all text-left ${
                      account.id === selectedAccountId ? "bg-blue-500 text-white" : account.hasAccess ? "bg-zinc-200/50 text-zinc-700 active:bg-zinc-200" : "bg-zinc-100/50 text-zinc-500"
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-1.5 flex-1 min-w-0">
                        {getChannelIcon(account.channel)}
                        <span className="truncate">{account.name}</span>
                      </div>
                      {account.id === selectedAccountId && <Badge className="text-[8px] px-1 h-3.5 bg-white/20 text-zinc-900 border-0 flex-shrink-0">ACTIVE</Badge>}
                      {!account.hasAccess && <Badge className="text-[8px] px-1 h-3.5 bg-orange-500/20 text-orange-700 border-0 flex-shrink-0">No Access</Badge>}
                    </div>
                    <p className="text-[10px] opacity-75 mt-0.5 truncate">{account.identifier}</p>
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}

        {selectedAccount && (
          <div className="px-3 mt-2">
            <div className="bg-gradient-to-r from-blue-500/10 to-purple-500/10 border border-blue-500/30 rounded-lg p-2">
              <div className="flex items-center gap-2 text-[10px]">
                {getChannelIcon(selectedAccount.channel)}
                <span className="text-zinc-600">Via:</span>
                <span className="font-medium text-zinc-900 truncate flex-1">{selectedAccount.identifier}</span>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Hidden file input */}
      <input
        ref={fileInputRef}
        type="file"
        accept={acceptedTypes}
        onChange={handleFileChange}
        className="hidden"
      />

      {/* Messages */}
      <div className="flex-1 min-h-0 overflow-y-auto px-3 py-2">
        <div className="space-y-3">
          {messagesLoading ? (
            <div className="flex flex-col items-center justify-center h-64 text-center">
              <Loader2 className="w-8 h-8 text-blue-700 animate-spin mb-3" />
              <p className="text-zinc-600 text-sm">Loading messages...</p>
            </div>
          ) : threadMessages.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-64 text-center px-3">
              <div className="w-16 h-16 rounded-full bg-zinc-100/50 flex items-center justify-center mb-3">
                {getChannelIcon(selectedChannel)}
              </div>
              <p className="text-zinc-600 text-sm">No messages yet</p>
              <p className="text-zinc-500 text-xs mt-1">Start a conversation with {contact.contactName}</p>
            </div>
          ) : (
            threadMessages.map((message, index) => {
              const isUser = message.sender === "user";
              const isAI = message.sender === "ai";
              const isNote = message.isInternal;
              const showTimestamp = index === 0 || threadMessages[index - 1].timestamp.getTime() - message.timestamp.getTime() > 300000;

              return (
                <div key={message.id}>
                  {showTimestamp && (
                    <div className="text-center text-xs text-zinc-600 my-3">{formatServerShortDateTime(message.timestamp)}</div>
                  )}
                  {isNote ? (
                    <div className="flex justify-center my-1">
                      <div className="max-w-[90%] w-full">
                        <div className="rounded-xl px-3 py-2 bg-amber-500/10 border border-amber-500/20">
                          <div className="flex items-center gap-1.5 mb-1">
                            <Lock className="w-2.5 h-2.5 text-amber-700" />
                            <Badge className="text-[9px] px-1 h-3.5 bg-amber-500/20 text-amber-700 border-amber-500/30 border">Note</Badge>
                            {message.sentBy && <span className="text-[9px] text-amber-700/70 ml-auto">{message.sentBy.name}</span>}
                          </div>
                          <p className="text-sm text-amber-100/90 leading-relaxed">{message.content}</p>
                          <span className="text-[9px] text-amber-700/50">{formatServerTime(message.timestamp)}</span>
                        </div>
                      </div>
                    </div>
                  ) : (
                    <div className={`flex ${isUser || isAI ? "justify-end" : "justify-start"}`}>
                      <div className={`flex gap-2 max-w-[85%] ${isUser || isAI ? "flex-row-reverse" : "flex-row"}`}>
                        {!isUser && !isAI && contact.contactAvatar && (
                          <img src={contact.contactAvatar} alt={contact.contactName} className="w-7 h-7 rounded-full object-cover flex-shrink-0" />
                        )}
                        <div>
                          <div className={`rounded-2xl px-3 py-2 shadow-lg ${
                            message.status === "failed"
                            ? "bg-red-50 border border-red-200 text-red-700"
                            : isAI ? "bg-gradient-to-br from-purple-500/10 to-blue-500/10 border border-purple-500/30 text-zinc-900"
                            : isUser ? "bg-gradient-to-br from-blue-500 to-purple-600 text-white"
                            : "bg-zinc-100 text-zinc-900"
                          }`}>
                            {message.type === "template" ? (
                              <div className="space-y-1.5">
                                {message.mediaUrl && (
                                  /\.(jpg|jpeg|png|gif|webp)$/i.test(message.mediaUrl) ? (
                                    <img src={message.mediaUrl} alt="Template media" className="rounded-lg max-w-[200px]" />
                                  ) : (
                                    <a href={message.mediaUrl} target="_blank" rel="noopener noreferrer" className="flex items-center gap-2 p-1 bg-zinc-200/50 rounded-lg">
                                      <div className="w-8 h-8 bg-zinc-300 rounded-lg flex items-center justify-center"><FileText className="w-4 h-4 text-zinc-700" /></div>
                                      <div><p className="text-xs font-medium">Attachment</p><p className="text-[10px] text-zinc-600">Document</p></div>
                                    </a>
                                  )
                                )}
                                {message.content && <p className="text-sm leading-relaxed">{message.content}</p>}
                                <span className="inline-block text-[10px] text-zinc-600 bg-zinc-200/50 px-1.5 py-0.5 rounded-full">Template</span>
                              </div>
                            ) : message.type === "document" && message.mediaUrl ? (
                              <div className="flex items-center gap-2 p-1">
                                <div className="w-8 h-8 bg-zinc-200 rounded-lg flex items-center justify-center"><Paperclip className="w-4 h-4 text-zinc-700" /></div>
                                <div><p className="text-xs font-medium">{message.mediaUrl}</p><p className="text-[10px] text-zinc-600">Document</p></div>
                              </div>
                            ) : message.type === "image" && message.mediaUrl ? (
                              <img src={message.mediaUrl} alt="Attached media" className="rounded-lg max-w-[200px]" />
                            ) : message.type === "sticker" && message.mediaUrl ? (
                              <img src={message.mediaUrl} alt="Sticker" className="w-24 h-24 object-contain" />
                            ) : (
                              <p className="text-sm leading-relaxed">{message.content}</p>
                            )}
                          </div>
                          <div className={`flex items-center gap-1.5 mt-1 text-[10px] ${isUser || isAI ? "justify-end flex-row-reverse" : "justify-start"}`}>
                            <div className="flex items-center gap-1 text-zinc-600">
                              <span>{formatServerTime(message.timestamp)}</span>
                              {(isUser || isAI) && (
                                message.status === "failed" ? <AlertCircle className="w-3 h-3 text-red-700" /> :
                                message.status === "queued" ? <Loader2 className="w-3 h-3 text-zinc-600 animate-spin" /> :
                                message.status === "read" ? <CheckCheck className="w-3 h-3 text-blue-700" /> :
                                message.status === "delivered" ? <CheckCheck className="w-3 h-3 text-zinc-600" /> :
                                message.status === "sent" ? <Check className="w-3 h-3 text-zinc-600" /> : null
                              )}
                            </div>
                            {(isUser || isAI) && message.sentBy && (
                              <div className="flex items-center gap-1">
                                {message.sentBy.avatar && <img src={message.sentBy.avatar} alt={message.sentBy.name} className="w-3 h-3 rounded-full" />}
                                {isAI ? (
                                  <Badge className="text-[9px] px-1 py-0 h-3.5 bg-purple-500/20 text-purple-700 border-purple-500/30">AI</Badge>
                                ) : (
                                  <span className="text-zinc-600 text-[9px]">{message.sentBy.name}</span>
                                )}
                              </div>
                            )}
                          </div>
                          {message.status === "failed" && isUser && (
                            <div className="flex items-center gap-2 mt-1">
                              <span className="text-[10px] text-red-700 truncate max-w-40">
                                {message.failureReason || "Delivery failed"}
                              </span>
                              {Date.now() - message.timestamp.getTime() > 6 * 60 * 60 * 1000 ? (
                                <span className="text-[11px] font-medium text-zinc-500" title="Resend window (6h) closed">Unsent</span>
                              ) : (
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
                              )}
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
          {optimisticMessages.map((msg) => (
            <div key={msg.id} className="flex justify-end">
              <div className="max-w-[85%]">
                <div className="rounded-2xl px-3 py-2 shadow-lg bg-gradient-to-br from-blue-500 to-purple-600 text-white opacity-70">
                  <p className="text-sm leading-relaxed">{msg.content}</p>
                </div>
                <div className="flex items-center gap-1 mt-1 justify-end text-[10px] text-zinc-600">
                  <Loader2 className="w-3 h-3 animate-spin" />
                  <span>Sending...</span>
                </div>
              </div>
            </div>
          ))}
          <div ref={messagesEndRef} />
        </div>
      </div>

      {/* Input */}
      <div className={`shrink-0 border-t p-3 safe-area-bottom ${isNoteMode ? "bg-amber-500/5 border-amber-500/20" : "bg-zinc-50 border-zinc-200"}`}>
        {contact.aiStatus === "active" && (
          <div className="mb-2 flex items-center gap-2 text-[10px] text-zinc-600 bg-zinc-100/50 rounded-lg p-2">
            <Bot className="w-3 h-3 text-blue-700 flex-shrink-0" />
            <span className="flex-1">AI monitoring</span>
            <Button
              size="sm"
              className="h-6 text-[10px] px-2 bg-blue-500 hover:bg-blue-600 text-white"
              onClick={async () => {
                if (!selectedAccountId) return;
                try {
                  await assignThreadCall({ thread_id: selectedAccountId });
                  toast.success("Thread assigned to you");
                } catch {
                  toast.error("Failed to take over");
                }
              }}
            >Take Over</Button>
          </div>
        )}

        <div className="flex items-center gap-1 mb-2">
          <button
            onClick={() => { setIsNoteMode(false); setShowCannedPopover(false); }}
            className={`px-2.5 py-1 rounded-lg text-[10px] font-medium transition-all ${!isNoteMode ? "bg-blue-500 text-white" : "bg-zinc-100 text-zinc-600"}`}
          >
            <MessageCircle className="w-3 h-3 inline mr-0.5" />Msg
          </button>
          <button
            onClick={() => { setIsNoteMode(true); setShowCannedPopover(false); }}
            className={`px-2.5 py-1 rounded-lg text-[10px] font-medium transition-all ${isNoteMode ? "bg-amber-500 text-white" : "bg-zinc-100 text-zinc-600"}`}
          >
            <StickyNote className="w-3 h-3 inline mr-0.5" />Note
          </button>
          {isNoteMode && (
            <span className="text-[9px] text-amber-700/70 ml-1 flex items-center gap-0.5">
              <Lock className="w-2.5 h-2.5" />Team only
            </span>
          )}
        </div>

        <div className="flex items-center gap-2 relative">
          {!isNoteMode && (
            <Button onClick={onOpenAI} variant="ghost" size="icon" className="text-zinc-600 hover:text-zinc-900 h-9 w-9 flex-shrink-0">
              <Smile className="w-5 h-5" />
            </Button>
          )}
          <div className="flex-1 min-w-0 relative">
            <CannedResponsePopover
              isOpen={showCannedPopover}
              searchText={cannedSearch}
              channel={selectedAccount?.channel}
              onSelect={handleCannedSelect}
              onClose={() => { setShowCannedPopover(false); setCannedSearch(""); }}
            />
            <div className={`rounded-full border transition-colors flex items-center px-3 ${
              isNoteMode
                ? "bg-amber-500/10 border-amber-500/30 focus-within:border-amber-400"
                : "bg-zinc-100 border-zinc-300 focus-within:border-blue-500"
            }`}>
              <Input
                placeholder={isNoteMode ? "Write a note..." : `Message ${selectedAccount?.identifier || "..."}...`}
                value={messageInput}
                onChange={(e) => handleMobileInputChange(e.target.value)}
                onKeyDown={(e) => {
                  if (showCannedPopover) return;
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    handleSendMessage();
                  }
                }}
                className={`border-0 bg-transparent focus-visible:ring-0 h-9 px-0 text-sm ${
                  isNoteMode ? "text-amber-100 placeholder:text-amber-700/40" : "text-zinc-900 placeholder:text-zinc-600"
                }`}
                disabled={!selectedAccount?.hasAccess}
              />
            </div>
          </div>
          {messageInput.trim() ? (
            <Button
              onClick={handleSendMessage}
              size="icon"
              className={`h-9 w-9 rounded-full flex-shrink-0 ${
                isNoteMode ? "bg-amber-500 hover:bg-amber-600 text-white" : "bg-gradient-to-r from-blue-500 to-purple-600 hover:from-blue-600 hover:to-purple-700 text-white"
              }`}
              disabled={!selectedAccount?.hasAccess || isSending || isSendingNote}
            >
              {isSending || isSendingNote ? <Loader2 className="w-4 h-4 animate-spin" /> : isNoteMode ? <StickyNote className="w-4 h-4" /> : <Send className="w-4 h-4" />}
            </Button>
          ) : !isNoteMode ? (
            <div className="flex gap-1 flex-shrink-0">
              <Button variant="ghost" size="icon" className="text-zinc-600 hover:text-zinc-900 h-9 w-9" onClick={openFilePicker} disabled={uploading}>
                {uploading ? <Loader2 className="w-5 h-5 animate-spin" /> : <Paperclip className="w-5 h-5" />}
              </Button>
              {selectedChannel === "whatsapp" && (
                <div className="relative">
                  <Button variant="ghost" size="icon" className="text-yellow-700 hover:text-yellow-700 h-9 w-9" onClick={() => setShowStickerPicker((v) => !v)} title="Send Sticker">
                    <StickerIcon className="w-5 h-5" />
                  </Button>
                  {showStickerPicker && selectedAccountId && (
                    <div className="absolute bottom-full right-0 mb-2 z-50">
                      <StickerPicker
                        threadId={selectedAccountId}
                        onClose={() => setShowStickerPicker(false)}
                        onSent={() => refresh()}
                      />
                    </div>
                  )}
                </div>
              )}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
