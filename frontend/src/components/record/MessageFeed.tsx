import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useFrappePostCall } from "frappe-react-sdk";
import { toast } from "sonner";
import { Loader2, Pin, ChevronDown, ChevronUp, Layers, MessageSquare } from "lucide-react";
import { MessageBubble } from "./MessageBubble";
import { EmailMessageCard } from "../EmailMessageCard";
import { MessageContextMenu } from "../MessageContextMenu";
import { Chip, EmptyState } from "../primitives";
import { useEmailBody } from "../../hooks/useEmailBody";
import { usePinnedMessages } from "../../hooks/usePinnedMessages";
import { channelMeta, CHANNEL_ORDER } from "../../lib/channels";
import { cn } from "../ui/utils";
import type { FeedMessage } from "../../hooks/useIdentityMessages";
import type { UnifiedContact } from "../../types";
import { formatServerDateTimeFull, formatServerShortDateTime, parseFrappeDateTime } from "../../utils/datetime";

interface Props {
  contact: UnifiedContact;
  messages: FeedMessage[];
  isLoading: boolean;
  refresh: () => void;
  optimistic: { id: string; content: string; timestamp: Date }[];
  onReply: (m: FeedMessage) => void;
  onReplyEmail: (gmailId: string, subject: string, to: string, threadId: string) => void;
  channelFilter: string;
  setChannelFilter: (c: string) => void;
  viaThreadId: string | null;
  isDragging?: boolean;
  dragHandlers?: { onDragOver: (e: React.DragEvent) => void; onDragLeave: (e: React.DragEvent) => void; onDrop: (e: React.DragEvent) => void };
}

/**
 * MessageFeed (UX-001 §6.1): one chronological feed, channel chips *filter* (never navigate),
 * "Group by channel" toggle (R1), one-line pinned strip, date dividers, optimistic sends.
 */
export function MessageFeed({ contact, messages, isLoading, refresh, optimistic, onReply, onReplyEmail, channelFilter, setChannelFilter, viaThreadId, isDragging, dragHandlers }: Props) {
  const endRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [grouped, setGrouped] = useState<boolean>(() => { try { return localStorage.getItem("excom_group_by_channel") === "true"; } catch { return false; } });
  const [showPinned, setShowPinned] = useState(false);
  const [ctx, setCtx] = useState<{ message: FeedMessage; position: { x: number; y: number } } | null>(null);
  const [retryingId, setRetryingId] = useState<string | null>(null);
  const { bodies, loading: bodyLoading, fetchBody, retryFetch } = useEmailBody();
  const { pinnedMessages, refresh: refreshPinned } = usePinnedMessages(viaThreadId || contact.activeAccountId);
  const { call: retryCall } = useFrappePostCall("excom.excom.api.chat.retry_message");

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const m of messages) c[m.channel || "unknown"] = (c[m.channel || "unknown"] || 0) + 1;
    return c;
  }, [messages]);
  const channels = useMemo(() => CHANNEL_ORDER.filter((k) => counts[k]).concat(Object.keys(counts).filter((k) => !CHANNEL_ORDER.includes(k))), [counts]);

  const visible = useMemo(() => {
    let list = channelFilter ? messages.filter((m) => m.channel === channelFilter) : messages;
    if (grouped && !channelFilter) {
      list = [...list].sort((a, b) => (CHANNEL_ORDER.indexOf(a.channel || "") - CHANNEL_ORDER.indexOf(b.channel || "")) || (a.timestamp.getTime() - b.timestamp.getTime()));
    }
    return list;
  }, [messages, channelFilter, grouped]);

  // Auto-scroll to bottom on new messages unless the user scrolled up.
  const stickRef = useRef(true);
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onScroll = () => { stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80; };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);
  useEffect(() => { if (stickRef.current) endRef.current?.scrollIntoView({ block: "end" }); }, [visible.length, optimistic.length]);
  useEffect(() => { stickRef.current = true; endRef.current?.scrollIntoView({ block: "end" }); }, [contact.id]);

  const handleRefreshAll = useCallback(() => { refresh(); refreshPinned(); }, [refresh, refreshPinned]);
  const handleRetry = useCallback(async (id: string) => {
    setRetryingId(id);
    try { await retryCall({ message_name: id }); toast.success("Message resent"); } catch { toast.error("Retry failed"); } finally { setRetryingId(null); refresh(); }
  }, [retryCall, refresh]);

  const toggleGrouped = () => { setGrouped((g) => { try { localStorage.setItem("excom_group_by_channel", String(!g)); } catch { /* ignore */ } return !g; }); };

  return (
    <div className="flex-1 min-h-0 flex flex-col relative">
      {/* Channel filter chips — filter, never navigate. Only shown with >1 channel. */}
      {channels.length > 1 && (
        <div className="shrink-0 h-9 border-b border-border bg-surface px-3 flex items-center gap-2 min-w-0">
          <div className="chip-row flex-1 h-full">
            <Chip size="sm" label="All" count={messages.length} accent={!channelFilter ? "blue" : "neutral"} onClick={() => setChannelFilter("")} />
            {channels.map((k) => { const m = channelMeta(k); return <Chip key={k} size="sm" icon={<m.icon />} label={m.label} count={counts[k]} accent={channelFilter === k ? m.accent : "neutral"} onClick={() => setChannelFilter(channelFilter === k ? "" : k)} />; })}
          </div>
          <button type="button" onClick={toggleGrouped} title="Group by channel" aria-pressed={grouped} className={cn("shrink-0 inline-flex items-center gap-1 h-6 px-1.5 rounded text-xs text-ink-3 hover:text-ink-1 hover:bg-surface-hover", grouped && "bg-surface-active text-ink-1")}>
            <Layers className="size-3.5" /><span className="hidden tablet:inline">Group</span>
          </button>
        </div>
      )}

      {/* Pinned strip — one line, expands on click (T2) */}
      {pinnedMessages.length > 0 && (
        <div className="shrink-0 border-b border-border bg-crayon-amber-tint">
          <button type="button" onClick={() => setShowPinned((v) => !v)} className="w-full h-7 px-3 flex items-center gap-2 text-xs text-crayon-amber-text min-w-0">
            <Pin className="size-3.5 shrink-0" />
            <span className="font-medium shrink-0 tabular-nums">{pinnedMessages.length} pinned</span>
            {!showPinned && <span className="truncate text-ink-2">{pinnedMessages[0].content_text}</span>}
            {showPinned ? <ChevronUp className="size-3.5 ml-auto shrink-0" /> : <ChevronDown className="size-3.5 ml-auto shrink-0" />}
          </button>
          {showPinned && (
            <div className="px-3 pb-2 space-y-1 max-h-40 overflow-y-auto">
              {pinnedMessages.map((pm) => (
                <div key={pm.name} className="rounded-md bg-surface px-2 py-1.5 text-xs min-w-0">
                  <div className="flex items-center gap-2 text-ink-3 min-w-0"><span className="truncate">{pm.sender_name || contact.contactName}</span><span className="ml-auto tabular-nums shrink-0">{formatServerShortDateTime(parseFrappeDateTime(pm.creation))}</span></div>
                  <p className="text-ink-1 line-clamp-2 break-words">{pm.content_text}</p>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <div
        ref={scrollRef}
        className={cn("flex-1 min-h-0 overflow-y-auto overscroll-contain px-3 py-3", isDragging && "ring-2 ring-inset ring-crayon-blue-base bg-crayon-blue-tint/40")}
        {...dragHandlers}
        aria-live="polite"
      >
        <div className="mx-auto max-w-[900px] space-y-2.5">
          {isLoading && messages.length === 0 ? (
            <div className="flex items-center justify-center h-40 text-ink-3"><Loader2 className="size-5 animate-spin" /></div>
          ) : visible.length === 0 ? (
            <EmptyState icon={<MessageSquare />} title="No messages yet" hint={channelFilter ? `Nothing on ${channelMeta(channelFilter).label}. Clear the chip to see everything.` : `Start the conversation with ${contact.contactName}.`} compact />
          ) : (
            visible.map((m, i) => {
              const prev = visible[i - 1];
              const showDivider = !prev || m.timestamp.getTime() - prev.timestamp.getTime() > 300000 || (grouped && prev.channel !== m.channel);
              const groupHead = grouped && !channelFilter && (!prev || prev.channel !== m.channel);
              return (
                <div key={m.id}>
                  {groupHead && (() => { const cm = channelMeta(m.channel); return <div className="flex items-center gap-2 text-xs text-ink-3 my-3"><cm.icon className={`size-3.5 text-crayon-${cm.accent}-base`} />{cm.label}<span className="flex-1 h-px bg-border" /></div>; })()}
                  {showDivider && !groupHead && <div className="text-center text-xs text-ink-3 my-3 tabular-nums">{formatServerDateTimeFull(m.timestamp)}</div>}
                  {m.isEmail ? (
                    <EmailMessageCard
                      messageId={m.id}
                      direction={m.rawDirection || (m.sender === "user" ? "Outbound" : "Inbound")}
                      snippet={m.content}
                      timestamp={m.timestamp}
                      contentJson={m.contentJson || "{}"}
                      sentBy={m.sentBy}
                      bodyData={bodies[m.id]}
                      bodyLoading={bodyLoading[m.id]}
                      onExpandEmail={fetchBody}
                      onReplyEmail={(gid, subject, to) => onReplyEmail(gid, subject, to, m.threadId)}
                      onRetryFetch={retryFetch}
                      accountName={m.accountUsed?.name}
                    />
                  ) : (
                    <MessageBubble message={m} contactName={contact.contactName} onContextMenu={(e, mm) => { e.preventDefault(); setCtx({ message: mm, position: { x: e.clientX, y: e.clientY } }); }} onReply={onReply} onRetry={handleRetry} onRefresh={handleRefreshAll} retrying={retryingId === m.id} showChannel={channels.length > 1 || contact.allAccounts.length > 1} />
                  )}
                </div>
              );
            })
          )}
          {optimistic.map((o) => (
            <div key={o.id} className="flex justify-end">
              <div className="max-w-[min(85%,640px)]">
                <div className="rounded-xl px-3 py-2 bg-crayon-blue-tint text-ink-1 opacity-70"><p className="text-base whitespace-pre-wrap break-words">{o.content}</p></div>
                <div className="flex items-center justify-end gap-1 mt-0.5 text-xs text-ink-3"><Loader2 className="size-3 animate-spin" />Sending…</div>
              </div>
            </div>
          ))}
          <div ref={endRef} />
        </div>
      </div>

      {ctx && <MessageContextMenu message={ctx.message} position={ctx.position} onClose={() => setCtx(null)} onReply={(m) => onReply(m as FeedMessage)} onRefresh={handleRefreshAll} />}
    </div>
  );
}
