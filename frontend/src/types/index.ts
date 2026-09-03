export interface ThreadTag {
  tag: string;
  tag_name: string;
  color: string;
}

export interface ExcomThread {
  name: string;
  display_name: string;
  primary_phone: string;
  last_message_at: string;
  last_message_preview: string;
  last_message_direction: "Inbound" | "Outbound";
  unread_count: number;
  status: string;
  assigned_to: string;
  assigned_team?: string;
  assigned_team_name?: string;
  omni_identity: string;
  channel: string;
  account: string;
  primary_email?: string;
  company?: string;
  assigned_to_name?: string;
  assigned_to_avatar?: string;
  tags?: ThreadTag[];
  broadcast_delivery_status?: string;
}

export interface ExcomMessage {
  name: string;
  direction: "Inbound" | "Outbound";
  message_type: string;
  content_text: string;
  media_file: string;
  delivery_status: string;
  creation: string;
  provider_timestamp?: string;
  provider_message_id: string;
  reply_to: string;
  created_by_user: string;
  sender_name: string;
  is_internal?: number;
  content_json?: string;
  is_pinned?: number;
  pinned_by?: string;
  reactions?: Record<string, string[]>;
  reply_to_content?: string;
  reply_to_direction?: string;
  reply_to_sender?: string;
  failure_reason?: string;
}

export interface Account {
  id: string;
  name: string;
  identifier: string;
  channel: string;
  isActive: boolean;
  hasAccess: boolean;
}

export interface Message {
  id: string;
  content: string;
  timestamp: Date;
  sender: "user" | "contact" | "ai";
  status?: "sent" | "delivered" | "read" | "failed" | "queued";
  type?: "text" | "image" | "video" | "audio" | "document" | "sticker" | "location" | "template" | "email" | "interactive" | "flow" | "reaction" | "contact" | "button";
  mediaUrl?: string;
  channel?: string;
  isInternal?: boolean;
  isEmail?: boolean;
  contentJson?: string;
  rawDirection?: "Inbound" | "Outbound";
  sentBy?: {
    name: string;
    avatar: string;
  };
  accountUsed?: {
    id: string;
    name: string;
    identifier: string;
    channel?: string;
  };
  isPinned?: boolean;
  reactions?: Record<string, string[]>;
  replyTo?: {
    id: string;
    content: string;
    sender: string;
    direction: string;
  };
  failureReason?: string;
}

export interface ContactInfo {
  email: string;
  phone: string;
  company?: string;
  erpEntity?: {
    type: string;
    id: string;
    value?: string;
  };
}

export interface UnifiedContact {
  id: string;
  contactName: string;
  contactAvatar: string;
  contactInfo: ContactInfo;
  status: "online" | "offline" | "away";
  lastMessage: string;
  timestamp: Date;
  totalUnreadCount: number;
  aiStatus?: "active" | "human-takeover";
  assignedTo?: {
    name: string;
    avatar: string;
  };
  allAccounts: Account[];
  activeAccountId: string;
  allMessages: Message[];
  channels: string[];
  tags?: ThreadTag[];
  broadcastDeliveryStatus?: string;
  /** Additive fields used by the P1 tree (legacy ignores them). */
  assignedToUser?: string;
  assignedTeam?: string;
  assignedTeamName?: string;
  lastMessageDirection?: "Inbound" | "Outbound";
  threads?: ExcomThread[];
}

export interface Conversation {
  id: string;
  contactName: string;
  contactAvatar: string;
  channel: string;
  lastMessage: string;
  timestamp: Date;
  unreadCount: number;
  status: "online" | "offline" | "away";
  aiStatus?: "active" | "human-takeover";
  assignedTo?: {
    name: string;
    avatar: string;
  };
  contactInfo: ContactInfo;
  activeAccount: Account;
  otherAccounts: Account[];
  messages: Message[];
  hiddenMessageCount?: number;
}
