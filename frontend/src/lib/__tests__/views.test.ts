import { describe, it, expect, vi } from "vitest";
vi.hoisted(() => { (globalThis as any).window = { localStorage: { getItem: () => null, setItem: () => undefined, removeItem: () => undefined }, location: { search: "" }, frappe: {} }; });
import { viewPredicate, kindMatches, parseSearchInput, DEFAULT_VIEWS, paramsFromFilters, filtersFromParams, EMPTY_FILTERS } from "../views";
import type { UnifiedContact } from "../../types";

const base = (o: Partial<UnifiedContact>): UnifiedContact => ({
  id: "x", contactName: "X", contactAvatar: "", channels: ["whatsapp"], activeAccountId: "", timestamp: new Date(), lastMessage: "", totalUnreadCount: 0,
  contactInfo: { phone: "", email: "", company: "" }, assignedToUser: undefined, assignedTeam: undefined, assignedTeamName: undefined, assignedTo: undefined, tags: [], threads: [], kinds: [], ...o,
} as unknown as UnifiedContact);

describe("views", () => {
  it("unassigned includes disabled owners", () => {
    const pred = viewPredicate(DEFAULT_VIEWS.find((v) => v.id === "unassigned")!);
    expect(pred(base({ assignedToUser: undefined }))).toBe(true);
    expect(pred(base({ assignedToUser: "u@x", assignedToEnabled: false }))).toBe(true);
    expect(pred(base({ assignedToUser: "u@x", assignedToEnabled: true }))).toBe(false);
  });
  it("kind filter: lead covers Lead and Opportunity, none = no record", () => {
    expect(kindMatches(base({ kinds: [{ doctype: "Opportunity", name: "o" }] }), "lead")).toBe(true);
    expect(kindMatches(base({ kinds: [{ doctype: "Customer", name: "c" }] }), "customer")).toBe(true);
    expect(kindMatches(base({ kinds: [] }), "none")).toBe(true);
    expect(kindMatches(base({ kinds: [{ doctype: "Supplier", name: "s" }] }), "customer")).toBe(false);
  });
  it("chips round-trip through the URL", () => {
    const f = filtersFromParams(new URLSearchParams("q=hi&kind=lead&archived=1&channel=email"));
    expect(f.kind).toBe("lead"); expect(f.archived).toBe("1"); expect(f.channel).toBe("email");
    const sp = paramsFromFilters(f);
    expect(sp.get("kind")).toBe("lead"); expect(sp.get("archived")).toBe("1");
  });
  it("parseChips understands kind: and channel:", () => {
    const r = parseSearchInput("kind:customer channel:whatsapp hello", EMPTY_FILTERS);
    expect(r.kind).toBe("customer"); expect(r.channel).toBe("whatsapp"); expect(r.q.trim()).toBe("hello");
  });
});
