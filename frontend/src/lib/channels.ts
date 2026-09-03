import { Mail, MessageCircle, Instagram, Phone, Globe, type LucideIcon } from "lucide-react";
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
  calls: { key: "calls", label: "Calls", short: "Calls", icon: Phone, accent: "teal" },
  webchat: { key: "webchat", label: "Web chat", short: "Web", icon: Globe, accent: "violet" },
};

export const CHANNEL_ORDER = ["whatsapp", "email", "instagram", "calls", "webchat"];

export function channelMeta(key?: string): ChannelMeta {
  const k = (key || "").toLowerCase();
  return CHANNELS[k] || { key: k || "unknown", label: key || "Unknown", short: key || "?", icon: MessageCircle, accent: "neutral" };
}
