/**
 * Excom AI agents (HLD-007): the shapes excom.excom_ai.builder.api and excom.excom_ai.api return.
 * Field names are the server's; the screens put them in plain words.
 */

export type AgentStatus = "Draft" | "Preparing" | "Shadow" | "Live" | "On Hold" | "Retired";
export type ReadinessState = "Not checked" | "Blocked" | "Warnings" | "Ready";
export type Health = "green" | "amber" | "red";
export type Mode = "Off" | "Shadow" | "Review" | "Auto";

/** agents(): one card per agent. */
export interface AgentCard {
  name: string;
  agent_name: string;
  title?: string | null;
  agent_type: string;
  status: AgentStatus;
  owner_user: string;
  acting_owner?: string | null;
  readiness_state?: ReadinessState | null;
  consecutive_failures?: number;
  avatar?: string | null;
  team?: string | null;
  colour?: string | null;
  owner_name?: string | null;
  waiting: number;
  sent_today: number;
  replies_today: number;
  health: Health;
}

/** types(): the agent types a builder may choose. */
export interface AgentType {
  name: string;
  pack: string;
  description?: string | null;
  colour?: string | null;
  icon?: string | null;
}

export type ResolvedAs = "Vocabulary" | "Concept" | "Region" | "Language" | "AI proposal" | "Unmatched";

/** resolve_keywords(): one row per keyword (builder/resolve.py resolve_one). */
export interface KeywordRow {
  keyword: string;
  resolved_as: ResolvedAs;
  label: string;
  link_doctype?: string | null;
  link_name?: string | null;
  concept?: string | null;
  countries: string[];
  language?: string | null;
  matches: number;
  detail: string;
  proposal?: { item_group: string; items: number; in_group: number; group_items: number; label: string; term: string } | null;
  alternative?: { link_doctype: string; link_name: string } | null;
  included?: number | boolean;
}

export type FactLevel = "info" | "warning" | "blocker";

export interface Fact {
  text: string;
  source: string;
  doctype?: string | null;
  names?: string[];
  level?: FactLevel;
}

/** One panel of the knowledge pack (builder/recipes.py). */
export interface KnowledgeSection {
  section: string;
  title: string;
  facts: Fact[];
  privacy?: string | null;
  skipped?: boolean;
  data?: Record<string, unknown>;
}

/** preview(): what the ERP shows for these keywords. */
export interface BuilderPreview {
  rows: KeywordRow[];
  scope: Record<string, unknown>;
  company: string;
  currency: string;
  sections: KnowledgeSection[];
  left_out: string[];
}

export type Excluded = Record<string, string[]>;

export interface ChannelRow {
  name?: string;
  channel_account: string;
  channel?: string | null;
  purpose?: string | null;
  daily_cap?: number | null;
  warmup_start?: string | null;
  signature_mode?: string | null;
  paused?: number;
  pause_reason?: string | null;
}

export interface PolicyRow {
  name?: string;
  action: string;
  mode: Mode;
  sample_percent?: number | null;
  daily_cap?: number | null;
  condition?: string | null;
  /** The most this action may be set to for this agent and condition. */
  ceiling: Mode;
  /** The most the action may ever be set to. */
  hard_ceiling: Mode;
}

export interface ReadinessGap {
  name: string;
  rule: string;
  severity: "Blocker" | "Warning" | "Info";
  status: "Open" | "Fixed" | "Waived";
  detail?: string | null;
  todo?: string | null;
  waived_by?: string | null;
  waive_reason?: string | null;
  checked_on?: string | null;
  title?: string | null;
  fix_hint?: string | null;
  fix_route?: string | null;
}

export interface Proposal {
  name: string;
  action: string;
  status: string;
  risk?: "Low" | "Medium" | "High" | null;
  subject_doctype?: string | null;
  subject_name?: string | null;
  thread?: string | null;
  preview_html?: string | null;
  creation: string;
  hold_reason?: string | null;
}

export interface AgentTask {
  name: string;
  kind: string;
  status: string;
  outcome?: string | null;
  error?: string | null;
  creation: string;
  ended_on?: string | null;
  ai_cost?: number | null;
}

export const BRIEF_FIELDS = ["agent_name", "title", "what_it_is", "mission", "audience", "tone", "tone_examples", "rules_do", "rules_dont"] as const;
export type BriefField = (typeof BRIEF_FIELDS)[number];

export interface AgentRecord extends Partial<Record<BriefField, string | null>> {
  name: string;
  agent_name: string;
  agent_type: string;
  company?: string | null;
  status: AgentStatus;
  hold_reason?: string | null;
  service_user?: string | null;
  avatar?: string | null;
  owner_user: string;
  backup_user?: string | null;
  team?: string | null;
  escalation_user?: string | null;
  reviewers?: string | null;
  acting_owner?: string | null;
  owner_last_seen?: string | null;
  keywords?: string | null;
  brief_version?: number | null;
  brief_approved_by?: string | null;
  brief_approved_on?: string | null;
  readiness_state?: ReadinessState | null;
  readiness_checked_on?: string | null;
  daily_ai_budget?: number | null;
  monthly_ai_budget?: number | null;
  office_hours?: string | null;
  model?: string | null;
}

/** agent(name): everything the agent page shows, tab by tab. */
export interface AgentDetail {
  agent: AgentRecord;
  pack: string;
  brief_sources: Record<string, string>;
  keyword_rows: { keyword: string; resolved_as: string; link_doctype?: string | null; link_name?: string | null; matches?: number; included?: number }[];
  markets: { country: string; language?: string | null; timezone?: string | null; priority?: number | null }[];
  products: { item_group?: string | null; item?: string | null; price_list?: string | null }[];
  channels: ChannelRow[];
  kpi_targets: { metric: string; value: number; period: string }[];
  policies: PolicyRow[];
  playbooks: ({ name: string; title?: string | null; status?: string | null; version?: number | null } | null)[];
  knowledge: {
    name: string;
    version: number;
    generated_on?: string | null;
    changes?: string | null;
    approx_tokens?: number | null;
    sections: KnowledgeSection[];
  } | null;
  gaps: ReadinessGap[];
  needs_you: Proposal[];
  activity: AgentTask[];
  usage: { today: number; month: number; calls_month: number };
  can_change: boolean;
  modes: Mode[];
}

/** redraft(name): a fresh draft of the brief, not saved. */
export interface BriefDraft {
  fields: Partial<Record<BriefField, string>>;
  sources: Record<string, string>;
  left_out?: string[];
}

export interface ReadinessResult {
  state: ReadinessState;
  blockers: number;
  warnings: number;
  infos: number;
}

/* ─── Needs you, Shadow, Research, inbox (HLD-007 §15.4, §15.5; excom_ai/needs_you.py) ─── */

export type CheckResult = { ok: boolean; why?: string | null };
/** checks.check(): one entry per code check, plus `ok` for all of them. */
export type Checks = Record<string, CheckResult | boolean> & { ok?: boolean };
export type CheckerVerdict = "Pass" | "Fixed" | "Fail" | "" | null;

export interface ProposalPayload {
  subject?: string;
  body?: string;
  language?: string;
  [k: string]: unknown;
}

/** queue(): a buyer an agent handed to a person. */
export interface WaitingBuyer {
  name: string;
  display_name?: string | null;
  channel?: string | null;
  ai_agent: string;
  ai_intent?: string | null;
  last_inbound_at?: string | null;
  last_message_preview?: string | null;
  assigned_to?: string | null;
  waited_hours?: number | null;
  late: boolean;
}

/** queue(): a Pending or Held proposal, with its checks. */
export interface QueueProposal {
  name: string;
  agent: string;
  action: string;
  status: string;
  risk?: "Low" | "Medium" | "High" | null;
  step?: number | null;
  kind?: string | null;
  to_email?: string | null;
  thread?: string | null;
  subject_doctype?: string | null;
  subject_name?: string | null;
  preview_html?: string | null;
  payload?: ProposalPayload | null;
  checks?: Checks | null;
  checker_verdict?: CheckerVerdict;
  checker_notes?: string | null;
  checks_ok?: boolean;
  creation: string;
  hold_reason?: string | null;
  enrollment?: string | null;
  send_at?: string | null;
}

export interface QueueBlocker {
  name: string;
  agent: string;
  rule: string;
  detail?: string | null;
  title?: string | null;
}

export interface NeedsYouQueue {
  waiting: WaitingBuyer[];
  proposals: QueueProposal[];
  held: QueueProposal[];
  blockers: QueueBlocker[];
  agents: Record<string, string>;
  reply_target_hours?: number;
}

export type ShadowVerdict = "Agent better" | "About the same" | "Person better";

/** shadow_comparison(): a Shadow draft next to what a person sent on the same conversation. */
export interface ShadowRow {
  name: string;
  action: string;
  thread?: string | null;
  to_email?: string | null;
  payload?: ProposalPayload | null;
  preview_html?: string | null;
  creation: string;
  shadow_verdict?: ShadowVerdict | "" | null;
  shadow_note?: string | null;
  checker_verdict?: CheckerVerdict;
  person_sent?: { name: string; content_text?: string | null; created_by_user?: string | null; creation: string } | null;
}

/** thread_proposals(): payload and checks arrive as JSON text. */
export interface ThreadProposal {
  name: string;
  agent: string;
  action: string;
  status: "Pending" | "Held" | "Shadow" | string;
  preview_html?: string | null;
  payload?: ProposalPayload | string | null;
  checks?: Checks | string | null;
  checker_verdict?: CheckerVerdict;
  creation: string;
}

export interface ResearchSummary {
  companies: Record<string, number>;
  people: Record<string, number>;
  total: number;
}

export interface AIPerson {
  name: string;
  email?: string | null;
  full_name?: string | null;
  email_status?: string | null;
  status?: string | null;
  company?: string | null;
}
