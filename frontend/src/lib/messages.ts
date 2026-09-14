import type { Message } from "@/types";

/**
 * The one place a backend message type or delivery status becomes a frontend one.
 *
 * There used to be two copies of this — `useMessages` and `useIdentityMessages` each kept their
 * own. Adding `Call` to one and not the other made every call render as a plain text bubble with a
 * delivery spinner ticking upward for ever, because a call never becomes "Delivered". The repo's
 * own guardrail warns about exactly this: a mapping with a `|| "text"` fallback swallows anything
 * it does not know, so the bug is silent.
 *
 * One map, imported by both. Adding a backend type means editing this file and nothing else.
 */

export type MessageTypeKey = NonNullable<Message["type"]>;

export const MESSAGE_TYPES: Record<string, MessageTypeKey> = {
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
  Call: "call",
};

export const DELIVERY_STATUSES: Record<string, Message["status"]> = {
  Sent: "sent",
  Delivered: "delivered",
  Read: "read",
  Failed: "failed",
  Queued: "queued",
  Scheduled: "scheduled",
};

export function mapMessageType(type: string): MessageTypeKey {
  return MESSAGE_TYPES[type] || "text";
}

export function mapDeliveryStatus(status: string): Message["status"] {
  return DELIVERY_STATUSES[status];
}

/**
 * A call is not a message awaiting delivery.
 *
 * The bubble shows a counting-up spinner while a message is "sent" but not yet acknowledged. That
 * is right for WhatsApp and wrong for a call, which has no delivery receipt and would spin until
 * the tab closed.
 */
export function awaitsDeliveryReceipt(type: MessageTypeKey | undefined): boolean {
  return type !== "call";
}
