import { Mail, MessageCircle, Instagram, Phone, Globe, MessageSquareMore, type LucideIcon } from "lucide-react";
import type { Accent } from "../components/primitives/Chip";

export interface ChannelMeta {
  key: string;
  label: string;
  short: string;
  icon: LucideIcon;
  accent: Accent;
}

/** Channel identity = icon + label; colour is reinforcement only (UX-001 §2.2). */
export const CHANNELS: Record<string, ChannelMeta> = {
  whatsapp: { key: "whatsapp", label: "WhatsApp", short: "WA", icon: MessageCircle, accent: "green" },
  email: { key: "email", label: "Email", short: "Email", icon: Mail, accent: "blue" },
  instagram: { key: "instagram", label: "Instagram", short: "IG", icon: Instagram, accent: "plum" },
  messenger: { key: "messenger", label: "Messenger", short: "FB", icon: MessageSquareMore, accent: "blue" },
  calls: { key: "calls", label: "Calls", short: "Calls", icon: Phone, accent: "teal" },
  webchat: { key: "webchat", label: "Web chat", short: "Web", icon: Globe, accent: "violet" },
};

export const CHANNEL_ORDER = ["whatsapp", "email", "instagram", "messenger", "calls", "webchat"];

/** Channels where a reply is only allowed inside Meta's 24h customer-service window. */
export const WINDOWED_CHANNELS = ["whatsapp", "instagram", "messenger"];
export const META_DM_CHANNELS = ["instagram", "messenger"];

export function channelMeta(key?: string): ChannelMeta {
  const k = (key || "").toLowerCase();
  return CHANNELS[k] || { key: k || "unknown", label: key || "Unknown", short: key || "?", icon: MessageCircle, accent: "neutral" };
}
