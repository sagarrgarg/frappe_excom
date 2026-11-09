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
