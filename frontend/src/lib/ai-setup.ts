/** Pure helpers for the AI setup page (HLD-010), kept free of React so they can be unit-tested. */

export type StepState = "done" | "attention" | "todo" | "optional" | "not_needed";
export type Severity = "required" | "recommended" | "info";
export type SetupAccent = "green" | "amber" | "rose" | "neutral" | "blue";

export interface SetupItem { label: string; ok: boolean; detail: string; fix: string | null; severity: Severity }
export interface ToolCard {
  key: string; label: string; tier?: string | null; enabled: number; needs_key: boolean; has_key: boolean | null;
  monthly_budget: number; used_this_month: number; last_error?: string | null; notes?: string | null; needed: boolean; can_test: boolean;
}
export interface SetupAgent { name: string; agent_name: string; agent_type: string; status: string; blockers: number; readiness_checked_on?: string | null }
export interface SetupStep {
  id: string; title: string; state: StepState; items: SetupItem[];
  system_manager_only?: boolean; provider?: string | null; tools?: ToolCard[]; agents?: SetupAgent[]; needed?: boolean;
}
export interface SetupStatus { steps: SetupStep[]; enabled: 0 | 1; missing: string[]; ready_to_go_live: boolean; is_system_manager: boolean }

const LABELS: Record<StepState, string> = { done: "Done", attention: "Needs attention", todo: "Not started", optional: "Optional", not_needed: "Not needed" };
const ACCENTS: Record<StepState, SetupAccent> = { done: "green", attention: "amber", todo: "rose", optional: "blue", not_needed: "neutral" };

export function stepLabel(state: StepState): string { return LABELS[state] ?? state; }
export function stepAccent(state: StepState): SetupAccent { return ACCENTS[state] ?? "neutral"; }

/** A step that still asks something of the admin: not started or needing attention. */
export function isPending(state: StepState): boolean { return state === "todo" || state === "attention"; }

/**
 * "6 of 9 steps done". Not needed counts as done (nothing to do); an optional step that is not
 * done is left out of the total, since nothing waits for it.
 */
export function progress(steps: Pick<SetupStep, "state">[]): { done: number; total: number; pending: number } {
  const done = steps.filter((s) => s.state === "done" || s.state === "not_needed").length;
  const total = steps.filter((s) => s.state !== "optional").length;
  return { done, total, pending: steps.filter((s) => isPending(s.state)).length };
}

/** The step to open first: the first one that still needs something, or none. */
export function firstOpenStep(steps: Pick<SetupStep, "id" | "state">[]): string | null {
  return steps.find((s) => isPending(s.state))?.id ?? null;
}

/** ✓ for a passing check, ! for a missing required one, ○ for a missing recommended one; info rows are neutral. */
export function itemMark(item: Pick<SetupItem, "ok" | "severity">): { mark: string; accent: SetupAccent; label: string } {
  if (item.severity === "info") return { mark: "•", accent: "neutral", label: "For information" };
  if (item.ok) return { mark: "✓", accent: "green", label: "Done" };
  if (item.severity === "required") return { mark: "!", accent: "rose", label: "Missing" };
  return { mark: "○", accent: "amber", label: "Recommended" };
}

export type FixAction =
  | { kind: "desk"; href: string }
  | { kind: "app"; to: string }
  | { kind: "dns"; agent: string; row: string };

/**
 * What an item's fix is: a Desk or outside link (new tab), a page inside this app (/excom/…), or a
 * DNS check for one agent mailbox ("check_dns:<agent>:<row>"). Anything else is ignored.
 */
export function fixAction(fix: string | null | undefined): FixAction | null {
  if (!fix) return null;
  if (fix.startsWith("check_dns:")) {
    const [agent, ...rest] = fix.slice("check_dns:".length).split(":");
    const row = rest.join(":");
    return agent && row ? { kind: "dns", agent, row } : null;
  }
  if (fix === "/excom" || fix.startsWith("/excom/")) return { kind: "app", to: fix.slice("/excom".length) || "/inbox" };
  // a path on this site ("/app/…"), never "//other-host" (a link to another site)
  if (/^\/(?!\/)/.test(fix) || /^https?:\/\//.test(fix)) return { kind: "desk", href: fix };
  return null;
}

/** Keeps only the named fields, in the schema's order, and drops the sections left empty. No list → the sections as given. */
export function pickSections<S extends { fields: { fieldname: string }[] }>(sections: S[], fields?: string[]): S[] {
  if (!fields) return sections;
  const want = new Set(fields);
  return sections.map((s) => ({ ...s, fields: s.fields.filter((f) => want.has(f.fieldname)) })).filter((s) => s.fields.length > 0);
}

/* ─── endpoints and the fields each step edits ─── */

const SG = "excom.excom_ai.setup_guide.";
export const SETUP_METHODS = {
  status: `${SG}status`,
  testProvider: `${SG}test_provider`,
  checkDns: `${SG}check_dns`,
  testTool: `${SG}test_tool`,
  makeVoiceKey: `${SG}make_voice_key`,
  checkVoice: `${SG}check_voice`,
  goLive: `${SG}go_live`,
} as const;

/** One SWR key, so the Agents home banner and the setup page share one copy of the status. */
export const SETUP_STATUS_KEY = "ai-setup-status";

/** The Excom AI Settings (and Excom AI Tool) fields each step shows; the server allow-lists the same ones. */
export const FIELDS = {
  budgets: ["daily_budget_total", "review_sample_percent"],
  basics: ["postal_addresses", "alert_users", "digest_time", "quiet_dates", "dnc_domains", "handover_line"],
  whatsapp: ["templates_enabled", "whatsapp_auto_submit", "template_first_use_review", "template_submissions_per_day", "template_max_attempts"],
  voice: ["voice_service_url", "voice_max_seconds", "voice_turn_limit", "voice_recording_notice"],
  people: ["people_per_company", "people_titles", "people_exclude_titles", "people_on_demand", "people_daily_per_user"],
  tool: ["enabled", "api_key", "account_id", "monthly_budget", "rate_limit"],
} satisfies Record<string, string[]>;
