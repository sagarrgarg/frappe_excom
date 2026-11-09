/** Pure helpers for the Excom AI screens (HLD-007), kept free of React so they can be unit-tested. */

type Payload = Record<string, unknown>;
type Minimal = { name: string; action: string; payload?: Payload | string | null; policy_action?: string | null };

function payloadOf(v: Payload | string | null | undefined): Payload {
  if (v == null || v === "") return {};
  if (typeof v !== "string") return v;
  try { return (JSON.parse(v) as Payload) || {}; } catch { return {}; }
}

/**
 * A priced reply: the server marks it with policy_action "prices_and_terms". Until the lists send
 * that field, a reply that carries its quotation proposal is priced too. The price list comes from
 * that quotation, found among the proposals already on screen.
 * Returns null when the reply is not priced, "" when it is priced from a list not known here.
 */
export function pricedFrom(p: Minimal, all: Minimal[] = []): string | null {
  if (p.action !== "reply") return null;
  const payload = payloadOf(p.payload);
  const quote = typeof payload.quote_proposal === "string" ? payload.quote_proposal : "";
  if (p.policy_action !== "prices_and_terms" && !quote) return null;
  if (typeof payload.price_list === "string" && payload.price_list) return payload.price_list;
  const q = quote ? all.find((x) => x.name === quote) : undefined;
  const list = q ? payloadOf(q.payload).price_list : null;
  return typeof list === "string" ? list : "";
}

/** "Tue 10 Nov, 9:30 am" in a given IANA zone; null when the browser does not know the zone. */
export function inZone(d: Date, zone: string): string | null {
  try {
    return new Intl.DateTimeFormat("en-GB", { timeZone: zone, weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit", hour12: true }).format(d);
  } catch { return null; }
}

/** Hard bounces as a share of what was sent, to one decimal; null when nothing was sent. */
export function bounceRate(bounced?: number | null, sent?: number | null): number | null {
  const s = Number(sent || 0);
  return s ? Math.round((Number(bounced || 0) / s) * 1000) / 10 : null;
}

/* ─── Custom agents (packs/custom.py) ─── */

export type Ability = "watch" | "answer" | "chase" | "draft";

/** A Custom agent's four abilities, in the server's order, in plain words. */
export const ABILITIES: { value: Ability; label: string; hint: string }[] = [
  { value: "watch", label: "Watch", hint: "Tells you when new records match its keywords." },
  { value: "answer", label: "Answer", hint: "Drafts replies to questions on its channel, from its playbook." },
  { value: "chase", label: "Chase", hint: "Reminds you weekly about each record its keywords find." },
  { value: "draft", label: "Draft", hint: "Writes documents or messages when you ask." },
];

/** What a Custom agent starts with, and what the server assumes when the field is empty. */
export const DEFAULT_ABILITIES: Ability[] = ["watch", "draft"];

const KNOWN = ABILITIES.map((a) => a.value);

/** The abilities as create_agent takes them: "watch, draft", known ones only, once each, in order. */
export function abilitiesText(chosen: string[]): string {
  const set = new Set(chosen.map((a) => a.trim().toLowerCase()));
  return KNOWN.filter((a) => set.has(a)).join(", ");
}

/** The abilities field read back, as packs/custom.py abilities() reads it: empty means watch and draft. */
export function parseAbilities(value?: string | null): Ability[] {
  const set = new Set((value || "").split(",").map((a) => a.trim().toLowerCase()).filter(Boolean));
  return set.size ? KNOWN.filter((a) => set.has(a)) : [...DEFAULT_ABILITIES];
}

/** A note's title and text (a Custom draft carries both; other notes carry text only). */
export function noteParts(payload: Payload | string | null | undefined): { title: string; text: string } | null {
  const p = payloadOf(payload);
  const title = typeof p.title === "string" ? p.title.trim() : "";
  const text = typeof p.text === "string" ? p.text : "";
  return title || text ? { title, text } : null;
}

/* ─── Template Steward (HLD-008) ─── */

/** Short words kept in capitals when a need key is read out. */
const ABBREVIATIONS: Record<string, string> = { po: "PO", rfq: "RFQ", sms: "SMS", erp: "ERP" };

function words(part: string): string {
  return part.split(/[_\s-]+/).filter(Boolean).map((w) => ABBREVIATIONS[w.toLowerCase()] ?? w.toLowerCase()).join(" ");
}

function sentence(text: string): string {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : "";
}

/** "accounts.payment_reminder" read as "Payment reminder (Accounts)"; a key with no area as just its words. */
export function needLabel(key?: string | null): string {
  const k = (key || "").trim();
  if (!k) return "";
  const dot = k.indexOf(".");
  if (dot < 0) return sentence(words(k));
  const area = sentence(words(k.slice(0, dot)));
  const what = sentence(words(k.slice(dot + 1)));
  return what ? (area ? `${what} (${area})` : what) : area;
}

export type TemplateStatus = "Open" | "Drafting" | "Submitted" | "In review" | "Met" | "Failed" | "Withdrawn";

/** The need statuses in the order a person reads them: what waits on them first, then what is moving, then what is done. */
export const TEMPLATE_GROUPS: { status: TemplateStatus; label: string; hint: string }[] = [
  { status: "In review", label: "Waiting for your approval", hint: "Email templates: read and approve them in Needs you." },
  { status: "Failed", label: "Failed", hint: "Excom stopped trying. The reason is on each one; try again once it is fixed." },
  { status: "Submitted", label: "With Meta", hint: "WhatsApp templates Meta is reviewing. It usually answers within a day." },
  { status: "Drafting", label: "Being written", hint: "" },
  { status: "Open", label: "Waiting to be written", hint: "Written when an agent that needs it is live." },
  { status: "Met", label: "Ready to use", hint: "Agents use these now." },
  { status: "Withdrawn", label: "Withdrawn", hint: "Taken back by a person. Agents do not use them." },
];

/** Rows grouped by status in TEMPLATE_GROUPS order, empty groups left out; an unknown status goes last, under its own name. */
export function groupByStatus<T extends { status: string }>(rows: T[]): { status: string; label: string; hint: string; items: T[] }[] {
  const known = TEMPLATE_GROUPS.map((g) => ({ ...g, items: rows.filter((r) => r.status === g.status) as T[] }));
  const other = new Map<string, T[]>();
  for (const r of rows) {
    if (TEMPLATE_GROUPS.some((g) => g.status === r.status)) continue;
    const s = r.status || "Unknown";
    other.set(s, [...(other.get(s) ?? []), r]);
  }
  return [...known, ...[...other].map(([status, items]) => ({ status, label: status, hint: "", items }))].filter((g) => g.items.length > 0);
}

export type TemplatePart = { text: string } | { variable: string };

/**
 * A template's words split around its variables: {{1}}, {{2}}… and, in an email, {{content}}.
 * Joining the parts back gives the same words (variables as written, spaces inside the braces dropped).
 */
export function splitVariables(body?: string | null): TemplatePart[] {
  const text = body || "";
  const parts: TemplatePart[] = [];
  const re = /\{\{\s*([A-Za-z0-9_]+)\s*\}\}/g;
  let last = 0;
  for (let m = re.exec(text); m; m = re.exec(text)) {
    if (m.index > last) parts.push({ text: text.slice(last, m.index) });
    parts.push({ variable: m[1] });
    last = m.index + m[0].length;
  }
  if (last < text.length) parts.push({ text: text.slice(last) });
  return parts;
}

/** A language code as a name ("es" → "Spanish", "pt_BR" → "Portuguese (Brazil)"); the code itself when the browser does not know it. */
export function languageName(code?: string | null): string {
  const c = (code || "").trim();
  if (!c) return "";
  try {
    const name = new Intl.DisplayNames(["en"], { type: "language" }).of(c.replace(/_/g, "-"));
    return name && name.toLowerCase() !== c.toLowerCase().replace(/_/g, "-") ? name : c;
  } catch { return c; }
}

/** Which buttons a need offers, from its status and whether this person may change it. */
export function templateActions(n: { status: string; can_change?: boolean | number | null; proposal?: string | null }): { withdraw: boolean; tryAgain: boolean; review: boolean } {
  const can = Boolean(n.can_change);
  return {
    withdraw: can && (n.status === "Met" || n.status === "Submitted"),
    tryAgain: can && (n.status === "Failed" || n.status === "Withdrawn"),
    review: n.status === "In review" && Boolean(n.proposal),
  };
}

/** A need belongs to an agent when it is the first to ask for it or one of the others that share it. */
export function needIsFor(n: { agent?: string | null; agents?: string | null }, agent: string): boolean {
  if (n.agent === agent) return true;
  return (n.agents || "").split(",").map((a) => a.trim()).includes(agent);
}
