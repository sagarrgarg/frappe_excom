import { memo, useEffect, useState } from "react";
import { Check, CheckCheck, AlertCircle, Loader2, Lock, Pin, Paperclip, FileText, RotateCcw, Reply, SmilePlus, Copy, Bot } from "lucide-react";
import { useFrappePostCall } from "frappe-react-sdk";
import { toast } from "sonner";
import { cn } from "../ui/utils";
import { channelMeta } from "../../lib/channels";
import { ReactionBar } from "../MessageContextMenu";
import type { FeedMessage } from "../../hooks/useIdentityMessages";
import { formatServerTime } from "../../utils/datetime";

const DELIVERY_TIMEOUT_MS = 10 * 60 * 1000;
/** Product rule: a failed message can be resent for 6 hours; after that it is flagged Unsent, no retry. */
const RETRY_WINDOW_MS = 6 * 60 * 60 * 1000;

export function DeliveryIcon({ status }: { status?: string }) {
  switch (status) {
    case "queued": return <Loader2 className="size-3.5 text-ink-muted animate-spin" />;
    case "sent": return <Check className="size-3.5 text-ink-muted" />;
    case "delivered": return <CheckCheck className="size-3.5 text-ink-muted" />;
    case "read": return <CheckCheck className="size-3.5 text-crayon-blue-base" />;
    case "failed": return <AlertCircle className="size-3.5 text-crayon-rose-base" />;
    default: return null;
  }
}

/** 10-minute "checking delivery" timer — icon + tabular time in the meta line (T1). */
function DeliveryTimer({ sentAt }: { sentAt: Date }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const id = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(id); }, []);
  const remaining = Math.max(0, DELIVERY_TIMEOUT_MS - (now - sentAt.getTime()));
  if (remaining <= 0) return null;
  const m = Math.floor(remaining / 60000);
  const s = Math.floor((remaining % 60000) / 1000);
  return <span className="inline-flex items-center gap-1 text-xs text-ink-3 tabular-nums"><Loader2 className="size-3 animate-spin" />{m}:{s.toString().padStart(2, "0")}</span>;
}

interface Props {
  message: FeedMessage;
  contactName: string;
  onContextMenu: (e: React.MouseEvent, m: FeedMessage) => void;
  onReply: (m: FeedMessage) => void;
  onRetry: (id: string) => void;
  onRefresh: () => void;
  retrying: boolean;
  showChannel?: boolean;
}

/**
 * MessageBubble — flat tints only. Outbound = blue tint, inbound = sunken, note = amber tint with lock,
 * failed = rose tint. Meta line carries channel icon + account (merged feed), time, delivery.
 * T2 actions (reply/react/pin/copy) reveal on hover / focus-within; context menu preserved.
 */
export const MessageBubble = memo(function MessageBubble({ message: m, contactName, onContextMenu, onReply, onRetry, onRefresh, retrying, showChannel = true }: Props) {
  const isUser = m.sender === "user" || m.sender === "ai";
  const isNote = Boolean(m.isInternal);
  const failed = m.status === "failed";
  const ch = channelMeta(m.channel);
  const { call: reactCall } = useFrappePostCall("excom.excom.api.chat.toggle_reaction");
  const { call: pinCall } = useFrappePostCall("excom.excom.api.chat.pin_message");
  const { call: unpinCall } = useFrappePostCall("excom.excom.api.chat.unpin_message");

  const quickReact = async (emoji: string) => {
    try { await reactCall({ message_name: m.id, emoji }); onRefresh(); } catch { toast.error("Failed to react"); }
  };
  const togglePin = async () => {
    try { await (m.isPinned ? unpinCall : pinCall)({ message_name: m.id }); onRefresh(); toast.success(m.isPinned ? "Unpinned" : "Pinned"); } catch { toast.error("Failed"); }
  };

  if (isNote) {
    return (
      <div className="flex justify-center t2-host" onContextMenu={(e) => onContextMenu(e, m)}>
        <div className="w-full max-w-[85%] rounded-lg bg-crayon-amber-tint border-l-[3px] border-crayon-amber-base px-3 py-2">
          <div className="flex items-center gap-1.5 text-xs text-crayon-amber-text min-w-0">
            <Lock className="size-3.5 shrink-0" />
            <span className="font-medium">Internal note</span>
            {m.sentBy && <span className="truncate">· {m.sentBy.name}</span>}
            <span className="ml-auto tabular-nums shrink-0">{formatServerTime(m.timestamp)}</span>
          </div>
          <p className="text-base text-ink-1 whitespace-pre-wrap break-words mt-1">{m.content}</p>
        </div>
      </div>
    );
  }

  const body = (() => {
    if (m.type === "template") {
      return (
        <div className="space-y-2">
          {m.mediaUrl && (/\.(jpg|jpeg|png|gif|webp)$/i.test(m.mediaUrl)
            ? <img src={m.mediaUrl} alt="" className="rounded-md max-w-full max-h-72 object-contain" />
            : <a href={m.mediaUrl} target="_blank" rel="noreferrer" className="flex items-center gap-2 text-sm underline min-w-0"><FileText className="size-4 shrink-0" /><span className="truncate">Attachment</span></a>)}
          {m.content && <p className="text-base whitespace-pre-wrap break-words">{m.content}</p>}
          <span className="inline-flex items-center rounded-full bg-black/5 px-2 h-5 text-xs">Template</span>
        </div>
      );
    }
    if (m.type === "document" && m.mediaUrl) {
      const name = m.content || m.mediaUrl.split("/").pop() || "Document";
      return (
        <a href={m.mediaUrl} target="_blank" rel="noreferrer" className="flex items-center gap-2 min-w-0 max-w-full">
          <span className="size-9 rounded-md bg-black/5 flex items-center justify-center shrink-0"><Paperclip className="size-4" /></span>
          <span className="min-w-0"><span className="block text-sm font-medium truncate">{name}</span><span className="block text-xs opacity-70">Document</span></span>
        </a>
      );
    }
    if (m.type === "image" && m.mediaUrl) return <img src={m.mediaUrl} alt="" className="rounded-md max-w-full max-h-80 object-contain" loading="lazy" />;
    if (m.type === "video" && m.mediaUrl) return <video src={m.mediaUrl} controls className="rounded-md max-w-full max-h-80" />;
    if (m.type === "audio" && m.mediaUrl) return <audio src={m.mediaUrl} controls className="max-w-full" />;
    if (m.type === "sticker" && m.mediaUrl) return <img src={m.mediaUrl} alt="Sticker" className="size-32 object-contain" />;
    return <p className="text-base whitespace-pre-wrap break-words">{m.content}</p>;
  })();

  return (
    <div className={cn("flex t2-host", isUser ? "justify-end" : "justify-start")} onContextMenu={(e) => onContextMenu(e, m)}>
      <div className={cn("flex flex-col min-w-0 max-w-[min(85%,640px)]", isUser ? "items-end" : "items-start")}>
        {m.replyTo && (
          <div className={cn("mb-1 max-w-full rounded-md px-2 py-1 border-l-[3px] bg-surface-sunken text-xs min-w-0", m.replyTo.direction === "Outbound" ? "border-crayon-blue-base" : "border-border-strong")}>
            <p className="text-ink-3 truncate">{m.replyTo.sender || (m.replyTo.direction === "Outbound" ? "You" : contactName)}</p>
            <p className="text-ink-2 line-clamp-2 break-words">{m.replyTo.content}</p>
          </div>
        )}

        <div className="relative flex items-end gap-1 max-w-full min-w-0">
          {/* T2 quick actions */}
          <div className={cn("t2-reveal flex items-center gap-0.5 shrink-0 self-center", isUser ? "order-first" : "order-last")}>
            <button type="button" title="Reply" aria-label="Reply" className="size-7 rounded-md text-ink-3 hover:bg-surface-hover hover:text-ink-1 flex items-center justify-center" onClick={() => onReply(m)}><Reply className="size-4" /></button>
            <button type="button" title="React 👍" aria-label="React" className="size-7 rounded-md text-ink-3 hover:bg-surface-hover hover:text-ink-1 flex items-center justify-center" onClick={() => quickReact("👍")}><SmilePlus className="size-4" /></button>
            <button type="button" title={m.isPinned ? "Unpin" : "Pin"} aria-label="Pin" className="size-7 rounded-md text-ink-3 hover:bg-surface-hover hover:text-ink-1 flex items-center justify-center" onClick={togglePin}><Pin className={cn("size-4", m.isPinned && "text-crayon-amber-text")} /></button>
            {m.content && <button type="button" title="Copy" aria-label="Copy" className="size-7 rounded-md text-ink-3 hover:bg-surface-hover hover:text-ink-1 flex items-center justify-center" onClick={() => navigator.clipboard?.writeText(m.content).then(() => toast.success("Copied"))}><Copy className="size-4" /></button>}
          </div>

          <div
            className={cn(
              "rounded-xl px-3 py-2 min-w-0 max-w-full",
              failed ? "bg-crayon-rose-tint text-crayon-rose-text border border-crayon-rose-base/40"
                : m.sender === "ai" ? "bg-crayon-violet-tint text-ink-1"
                : isUser ? "bg-crayon-blue-tint text-ink-1"
                : "bg-surface-sunken text-ink-1"
            )}
          >
            {body}
          </div>
        </div>

        {m.reactions && Object.keys(m.reactions).length > 0 && <ReactionBar reactions={m.reactions} messageId={m.id} onRefresh={onRefresh} />}

        <div className={cn("flex items-center gap-1.5 mt-0.5 text-xs text-ink-3 min-w-0 max-w-full", isUser && "flex-row-reverse")}>
          <span className="tabular-nums shrink-0">{formatServerTime(m.timestamp)}</span>
          {isUser && <DeliveryIcon status={m.status} />}
          {m.isPinned && <Pin className="size-3 text-crayon-amber-text shrink-0" />}
          {showChannel && m.accountUsed && (
            <span className="inline-flex items-center gap-1 min-w-0" title={`${ch.label} · ${m.accountUsed.name} (${m.accountUsed.identifier})`} data-detail={`Sent via ${ch.label} | Account: ${m.accountUsed.name} (${m.accountUsed.identifier}) | Status: ${m.status} · ${formatServerTime(m.timestamp)}`}>
              <ch.icon className={cn("size-3.5 shrink-0", `text-crayon-${ch.accent}-base`)} />
              <span className="truncate max-w-[160px]">{m.accountUsed.name}</span>
            </span>
          )}
          {isUser && m.sentBy && (m.sender === "ai"
            ? <span className="inline-flex items-center gap-0.5 text-crayon-violet-text"><Bot className="size-3" />AI</span>
            : <span className="truncate max-w-[140px]">{m.sentBy.name}</span>)}
          {isUser && (m.status === "sent" || m.status === "queued") && <DeliveryTimer sentAt={m.timestamp} />}
        </div>

        {failed && isUser && (
          <div className="flex items-center gap-2 mt-0.5 min-w-0 max-w-full">
            <span className="text-xs text-crayon-rose-text truncate" title={m.failureReason}>{m.failureReason || "Delivery failed"}</span>
            {Date.now() - m.timestamp.getTime() <= RETRY_WINDOW_MS ? (
              <button type="button" onClick={() => onRetry(m.id)} disabled={retrying} className="inline-flex items-center gap-1 text-xs font-medium text-crayon-rose-text hover:underline disabled:opacity-50 shrink-0" title="Resend is available for 6 hours after a failure">
                {retrying ? <Loader2 className="size-3 animate-spin" /> : <RotateCcw className="size-3" />}Retry
              </button>
            ) : (
              <span className="inline-flex items-center gap-1 text-xs font-medium text-ink-3 shrink-0 rounded-full bg-surface-sunken px-1.5 h-5" title="Failed more than 6 hours ago — resend window closed. Send a new message.">Unsent</span>
            )}
          </div>
        )}
      </div>
    </div>
  );
});
