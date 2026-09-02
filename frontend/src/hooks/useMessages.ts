import { useMemo } from "react";
import { useFrappeGetCall } from "frappe-react-sdk";
import type { ExcomMessage, Message } from "../types";
import { parseFrappeDateTime } from "../utils/datetime";

/**
 * Fetches messages for a given thread from the Frappe backend and
 * transforms them into the Message format used by the new UI components.
 */
export function useMessages(threadId: string) {
  const { data, error, isLoading, mutate } = useFrappeGetCall<{
    message: { messages: ExcomMessage[]; auto_claimed_by: string | null };
  }>(
    threadId ? "excom.excom.api.chat.get_messages" : "",
    threadId ? { thread_id: threadId, limit: 100 } : undefined,
    threadId ? undefined : null
  );

  const payload = data?.message;
  const rawMessages: ExcomMessage[] = Array.isArray(payload)
    ? (payload as unknown as ExcomMessage[]) // backwards compat fallback
    : (payload?.messages ?? []);
  const autoClaimedBy: string | null = payload && !Array.isArray(payload)
    ? (payload.auto_claimed_by ?? null)
    : null;

  const messages: Message[] = useMemo(() => {
    return rawMessages.map((msg) => ({
      id: msg.name,
      content: msg.content_text || "",
      timestamp: parseFrappeDateTime(msg.provider_timestamp || msg.creation),
      sender: msg.direction === "Inbound" ? ("contact" as const) : ("user" as const),
      status: mapDeliveryStatus(msg.delivery_status),
      type: mapMessageType(msg.message_type),
      mediaUrl: msg.media_file || undefined,
      isInternal: Boolean(msg.is_internal),
      isEmail: msg.message_type === "Email",
      contentJson: msg.content_json || undefined,
      rawDirection: msg.direction,
      sentBy: msg.sender_name
        ? { name: msg.sender_name, avatar: "" }
        : undefined,
      isPinned: Boolean(msg.is_pinned),
      failureReason: msg.failure_reason || undefined,
      reactions: msg.reactions && typeof msg.reactions === "object" ? msg.reactions : undefined,
      replyTo: msg.reply_to
        ? {
            id: msg.reply_to,
            content: msg.reply_to_content || "",
            sender: msg.reply_to_sender || "",
            direction: msg.reply_to_direction || "",
          }
        : undefined,
    }));
  }, [rawMessages]);

  return {
    rawMessages,
    messages,
    error,
    isLoading,
    refresh: mutate,
    autoClaimedBy,
  };
}

function mapDeliveryStatus(
  status: string
): "sent" | "delivered" | "read" | "failed" | "queued" | undefined {
  const map: Record<string, "sent" | "delivered" | "read" | "failed" | "queued"> = {
    Sent: "sent",
    Delivered: "delivered",
    Read: "read",
    Failed: "failed",
    Queued: "queued",
  };
  return map[status] || undefined;
}

type MessageTypeKey =
  | "text" | "image" | "video" | "audio" | "document"
  | "sticker" | "location" | "template" | "email"
  | "interactive" | "flow" | "reaction" | "contact" | "button";

function mapMessageType(type: string): MessageTypeKey {
  const map: Record<string, MessageTypeKey> = {
    Text: "text",
    Image: "image",
    Video: "video",
    Audio: "audio",
    Document: "document",
    Sticker: "sticker",
    Location: "location",
    Template: "template",
    Email: "email",
    Interactive: "interactive",
    Flow: "flow",
    Reaction: "reaction",
    Contact: "contact",
    Button: "button",
  };
  return map[type] || "text";
}
