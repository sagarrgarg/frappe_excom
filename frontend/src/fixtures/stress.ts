import type { UnifiedContact, ThreadTag } from "../types";
import type { FeedMessage } from "../hooks/useIdentityMessages";

/** Stress record (UX-001 §2.5 rule 6): 48-char company, +91 15-digit number, 6 tags, 4 channels, ₹1,23,45,678, 3-line preview. */
export const STRESS_TAGS: ThreadTag[] = [
  { tag: "priority", tag_name: "Priority", color: "#d9645f" },
  { tag: "export", tag_name: "Export — Middle East & Africa", color: "#3fa37a" },
  { tag: "followup", tag_name: "Follow-up", color: "#d99a3e" },
  { tag: "vip", tag_name: "VIP", color: "#8a7cd8" },
  { tag: "sample", tag_name: "Sample sent", color: "#3e9aa8" },
  { tag: "pricing", tag_name: "Pricing negotiation", color: "#c86ba0" },
];

const now = new Date();
const ago = (m: number) => new Date(now.getTime() - m * 60000);

export const STRESS_CONTACT: UnifiedContact = {
  id: "OI-STRESS-000001",
  contactName: "Muhammad Abdul Rahman Al-Sheikh bin Khalifa",
  contactAvatar: "",
  contactInfo: { email: "very.long.email.address.for.testing@example-corporation-international.com", phone: "+91 987654321012345", company: "Abdullah International Trading & Export Company LLC" },
  status: "offline",
  lastMessage: "Dear team, following up on the quotation you sent last week for the 40ft container of premium compounded asafoetida — we need revised pricing for 3 SKUs, updated HS codes, and confirmation of the shipping schedule before the 15th so that our customs broker can prepare the paperwork.",
  timestamp: ago(95),
  totalUnreadCount: 123,
  allAccounts: [
    { id: "THR-1", name: "GGIL Export (+91 98xxx xxxxx)", identifier: "+91 9876543210", channel: "whatsapp", isActive: true, hasAccess: true },
    { id: "THR-2", name: "sales@hingwala.com", identifier: "sales@hingwala.com", channel: "email", isActive: true, hasAccess: true },
    { id: "THR-3", name: "Hingwala Instagram", identifier: "@hingwala", channel: "instagram", isActive: true, hasAccess: false },
    { id: "THR-4", name: "Main line", identifier: "+91 22 1234 5678", channel: "calls", isActive: true, hasAccess: true },
  ],
  activeAccountId: "THR-1",
  allMessages: [],
  channels: ["whatsapp", "email", "instagram", "calls"],
  tags: STRESS_TAGS,
  broadcastDeliveryStatus: "Failed",
  assignedTo: { name: "Priyanka Venkataraghavan Subramaniam", avatar: "" },
  assignedToUser: "priyanka@example.com",
  assignedTeam: "Export",
  assignedTeamName: "Export — Middle East & Africa",
  lastMessageDirection: "Inbound",
};

const acc = (i: number) => {
  const a = STRESS_CONTACT.allAccounts[i];
  return { id: a.id, name: a.name, identifier: a.identifier, channel: a.channel };
};

export const STRESS_MESSAGES: FeedMessage[] = [
  { id: "m1", threadId: "THR-1", content: STRESS_CONTACT.lastMessage, timestamp: ago(400), sender: "contact", type: "text", channel: "whatsapp", accountUsed: acc(0), rawDirection: "Inbound" },
  { id: "m2", threadId: "THR-1", content: "Noted — revised quotation attached. Please confirm the incoterms.", timestamp: ago(380), sender: "user", status: "read", type: "text", channel: "whatsapp", accountUsed: acc(0), sentBy: { name: "Priyanka Venkataraghavan Subramaniam", avatar: "" }, rawDirection: "Outbound", replyTo: { id: "m1", content: STRESS_CONTACT.lastMessage, sender: "", direction: "Inbound" }, reactions: { "👍": ["a@b.c", "d@e.f"], "🔥": ["x@y.z"] }, isPinned: true },
  { id: "m3", threadId: "THR-1", content: "Internal: customer is price sensitive; hold margin at 12%. Do not share the cost sheet.", timestamp: ago(370), sender: "user", type: "text", channel: "whatsapp", accountUsed: acc(0), isInternal: true, sentBy: { name: "Sagar", avatar: "" }, rawDirection: "Outbound" },
  { id: "m4", threadId: "THR-1", content: "", timestamp: ago(300), sender: "user", status: "failed", type: "image", mediaUrl: "/assets/excom/excom/manifest/android-chrome-192x192.png", channel: "whatsapp", accountUsed: acc(0), failureReason: "Message failed to send because more than 24 hours have passed since the customer last replied to this number (error 131047).", rawDirection: "Outbound" },
  { id: "m5", threadId: "THR-2", content: "Re: Quotation Q-2026-00042 — revised pricing and shipping schedule for 40ft container", timestamp: ago(200), sender: "contact", type: "email", isEmail: true, channel: "email", accountUsed: acc(1), contentJson: JSON.stringify({ subject: "Re: Quotation Q-2026-00042 — revised pricing and shipping schedule for 40ft container (very long subject line to test truncation)", from_name: "Muhammad Abdul Rahman", from_email: "very.long.email.address.for.testing@example-corporation-international.com", to: "sales@hingwala.com", label_ids: ["IMPORTANT", "STARRED"] }), rawDirection: "Inbound" },
  { id: "m6", threadId: "THR-1", content: "Template: order_confirmation_v3", timestamp: ago(120), sender: "user", status: "sent", type: "template", channel: "whatsapp", accountUsed: acc(0), rawDirection: "Outbound" },
  { id: "m7", threadId: "THR-1", content: "Quotation_Q-2026-00042_revised_final_v3_with_hs_codes_and_shipping_schedule.pdf", timestamp: ago(60), sender: "user", status: "delivered", type: "document", mediaUrl: "/files/Quotation_Q-2026-00042_revised_final_v3_with_hs_codes_and_shipping_schedule.pdf", channel: "whatsapp", accountUsed: acc(0), rawDirection: "Outbound" },
  { id: "m8", threadId: "THR-4", content: "Missed call · 2m 14s", timestamp: ago(30), sender: "contact", type: "text", channel: "calls", accountUsed: acc(3), rawDirection: "Inbound" },
  { id: "m9", threadId: "THR-1", content: "Sending now", timestamp: ago(1), sender: "user", status: "queued", type: "text", channel: "whatsapp", accountUsed: acc(0), rawDirection: "Outbound" },
];

export const STRESS_WIDTHS = [360, 390, 414, 640, 768, 834, 1024, 1280, 1366, 1440, 1920];
