import { describe, expect, it } from "vitest";
import { abilitiesText, bounceRate, creditsFor, groupByStatus, inZone, languageName, linkedinHref, needIsFor, needLabel, noteParts, parseAbilities, pricedFrom, splitVariables, templateActions, togglePick } from "../agents-logic";

describe("pricedFrom", () => {
  const quote = { name: "P-1", action: "create_quotation", payload: { price_list: "Export USD" } };

  it("is null for anything but a reply", () => {
    expect(pricedFrom(quote, [quote])).toBeNull();
  });
  it("is null for a plain reply", () => {
    expect(pricedFrom({ name: "P-2", action: "reply", payload: { body: "hi", intent: "thanks" } })).toBeNull();
  });
  it("finds the price list through the quotation the reply carries", () => {
    const reply = { name: "P-2", action: "reply", payload: JSON.stringify({ body: "x", quote_proposal: "P-1" }) };
    expect(pricedFrom(reply, [quote, reply])).toBe("Export USD");
  });
  it("is priced from an unknown list when only policy_action says so", () => {
    expect(pricedFrom({ name: "P-3", action: "reply", policy_action: "prices_and_terms", payload: {} })).toBe("");
  });
});

describe("inZone", () => {
  const d = new Date(Date.UTC(2026, 10, 10, 9, 30));
  it("formats in the buyer's zone", () => {
    expect(inZone(d, "UTC")).toMatch(/9:30/);
    expect(inZone(d, "Asia/Kolkata")).toMatch(/3:00/);
  });
  it("is null for a zone the browser does not know", () => {
    expect(inZone(d, "Not/AZone")).toBeNull();
  });
});

describe("bounceRate", () => {
  it("is a percentage to one decimal", () => { expect(bounceRate(3, 200)).toBe(1.5); });
  it("is null when nothing was sent", () => { expect(bounceRate(0, 0)).toBeNull(); });
});

describe("abilitiesText", () => {
  it("joins in the server's order, once each", () => {
    expect(abilitiesText(["draft", "watch", "draft"])).toBe("watch, draft");
    expect(abilitiesText(["chase", "answer", "watch", "draft"])).toBe("watch, answer, chase, draft");
  });
  it("drops anything that is not an ability", () => {
    expect(abilitiesText([" Chase ", "send"])).toBe("chase");
  });
  it("is empty when nothing is chosen", () => { expect(abilitiesText([])).toBe(""); });
});

describe("parseAbilities", () => {
  it("reads the saved field", () => { expect(parseAbilities("draft, Answer")).toEqual(["answer", "draft"]); });
  it("is watch and draft when the field is empty, as the server assumes", () => {
    expect(parseAbilities(null)).toEqual(["watch", "draft"]);
    expect(parseAbilities(" , ")).toEqual(["watch", "draft"]);
  });
  it("round-trips with abilitiesText", () => {
    expect(parseAbilities(abilitiesText(["chase", "watch"]))).toEqual(["watch", "chase"]);
  });
});

describe("noteParts", () => {
  it("reads a Custom draft's title and text, as JSON text too", () => {
    expect(noteParts(JSON.stringify({ title: "Letter to ACME", text: "Dear ACME" }))).toEqual({ title: "Letter to ACME", text: "Dear ACME" });
  });
  it("reads a note without a title", () => { expect(noteParts({ text: "I am Lucía." })).toEqual({ title: "", text: "I am Lucía." }); });
  it("is null when there is nothing to show", () => {
    expect(noteParts({ body: "hi" })).toBeNull();
    expect(noteParts("not json")).toBeNull();
  });
});

describe("needLabel", () => {
  it("reads a need key as what it is for, then its area", () => {
    expect(needLabel("accounts.payment_reminder")).toBe("Payment reminder (Accounts)");
    expect(needLabel("sales.quote_follow_up")).toBe("Quote follow up (Sales)");
  });
  it("keeps short words in capitals", () => {
    expect(needLabel("purchase.po_chase")).toBe("PO chase (Purchase)");
    expect(needLabel("purchase.rfq_reminder")).toBe("RFQ reminder (Purchase)");
  });
  it("reads a key with no area, and nothing as nothing", () => {
    expect(needLabel("order_update")).toBe("Order update");
    expect(needLabel("")).toBe("");
    expect(needLabel(null)).toBe("");
  });
});

describe("groupByStatus", () => {
  const rows = [
    { name: "a", status: "Met" }, { name: "b", status: "In review" }, { name: "c", status: "Failed" },
    { name: "d", status: "Met" }, { name: "e", status: "Paused" },
  ];
  it("puts what waits on a person first, leaves empty groups out and keeps row order", () => {
    const g = groupByStatus(rows);
    expect(g.map((x) => x.status)).toEqual(["In review", "Failed", "Met", "Paused"]);
    expect(g[2].items.map((r) => r.name)).toEqual(["a", "d"]);
    expect(g[0].label).toBe("Waiting for your approval");
  });
  it("keeps a status it does not know, under its own name, last", () => {
    expect(groupByStatus(rows).at(-1)).toMatchObject({ status: "Paused", label: "Paused", items: [{ name: "e" }] });
  });
  it("is empty for no rows", () => { expect(groupByStatus([])).toEqual([]); });
});

describe("splitVariables", () => {
  it("splits the words around numbered variables", () => {
    expect(splitVariables("Hello {{1}}, you owe {{2}}.")).toEqual([
      { text: "Hello " }, { variable: "1" }, { text: ", you owe " }, { variable: "2" }, { text: "." },
    ]);
  });
  it("finds an email's {{content}}, side by side variables and spaces inside the braces", () => {
    expect(splitVariables("{{ 1 }}{{2}}\n{{content}}")).toEqual([{ variable: "1" }, { variable: "2" }, { text: "\n" }, { variable: "content" }]);
  });
  it("leaves single braces and plain words alone", () => {
    expect(splitVariables("a {b} c")).toEqual([{ text: "a {b} c" }]);
    expect(splitVariables("")).toEqual([]);
    expect(splitVariables(null)).toEqual([]);
  });
});

describe("languageName", () => {
  it("names a language from Meta's code", () => {
    expect(languageName("es")).toBe("Spanish");
    expect(languageName("pt_BR")).toMatch(/Portuguese/);
  });
  it("is the code when it is not a language", () => {
    expect(languageName("zz_not")).toBe("zz_not");
    expect(languageName("")).toBe("");
  });
});

describe("templateActions", () => {
  it("offers Withdraw on what is used or with Meta, to someone who may change it", () => {
    expect(templateActions({ status: "Met", can_change: true }).withdraw).toBe(true);
    expect(templateActions({ status: "Submitted", can_change: 1 }).withdraw).toBe(true);
    expect(templateActions({ status: "Met", can_change: false }).withdraw).toBe(false);
    expect(templateActions({ status: "Open", can_change: true }).withdraw).toBe(false);
  });
  it("offers Try again on what failed or was withdrawn", () => {
    expect(templateActions({ status: "Failed", can_change: true }).tryAgain).toBe(true);
    expect(templateActions({ status: "Withdrawn", can_change: true }).tryAgain).toBe(true);
    expect(templateActions({ status: "Withdrawn", can_change: false }).tryAgain).toBe(false);
  });
  it("offers Open review on a need in review that has its proposal", () => {
    expect(templateActions({ status: "In review", proposal: "P-1" }).review).toBe(true);
    expect(templateActions({ status: "In review", proposal: null }).review).toBe(false);
  });
});

describe("needIsFor", () => {
  it("is the first agent's and every agent's that shares it", () => {
    const n = { agent: "A1", agents: "A1, A2" };
    expect(needIsFor(n, "A1")).toBe(true);
    expect(needIsFor(n, "A2")).toBe(true);
    expect(needIsFor(n, "A")).toBe(false);
    expect(needIsFor({ agent: "A3", agents: null }, "A3")).toBe(true);
  });
});

describe("togglePick", () => {
  it("ticks and unticks", () => {
    expect(togglePick([], "c")).toEqual(["c"]);
    expect(togglePick(["a", "c"], "c")).toEqual(["a"]);
  });
  it("ignores a sixth tick but still unticks", () => {
    const five = ["a", "b", "c", "d", "e"];
    expect(togglePick(five, "g")).toBe(five);
    expect(togglePick(five, "c")).toEqual(["a", "b", "d", "e"]);
  });
});

describe("creditsFor", () => {
  const cands = [{ pick: "a", known_email: "a@x.com" }, { pick: "b", known_email: null }, { pick: "c" }];
  it("counts only ticked people whose address is not known", () => {
    expect(creditsFor(cands, ["a", "b", "c"])).toBe(2);
    expect(creditsFor(cands, ["a"])).toBe(0);
    expect(creditsFor(cands, [])).toBe(0);
    expect(creditsFor([{ pick: "d", known_email: null, looked_up: true }], ["d"])).toBe(0);
  });
});

describe("linkedinHref", () => {
  it("keeps http(s) links", () => {
    expect(linkedinHref("https://www.linkedin.com/in/jane")).toBe("https://www.linkedin.com/in/jane");
    expect(linkedinHref("http://linkedin.com/in/jane")).toBe("http://linkedin.com/in/jane");
  });
  it("adds https to a bare linkedin.com address", () => {
    expect(linkedinHref("linkedin.com/in/jane")).toBe("https://linkedin.com/in/jane");
    expect(linkedinHref(" www.linkedin.com/in/jane ")).toBe("https://www.linkedin.com/in/jane");
  });
  it("refuses anything that is not a web link", () => {
    expect(linkedinHref("javascript:alert(1)")).toBeNull();
    expect(linkedinHref("data:text/html,x")).toBeNull();
    expect(linkedinHref("jane")).toBeNull();
    expect(linkedinHref("")).toBeNull();
    expect(linkedinHref(null)).toBeNull();
  });
  it("refuses other sites, however the link is dressed", () => {
    expect(linkedinHref("https://evil.example/in/jane")).toBeNull();
    expect(linkedinHref("https://linkedin.com.evil.example/in/jane")).toBeNull();
    expect(linkedinHref("https://es.linkedin.com/in/jane")).toBe("https://es.linkedin.com/in/jane");
  });
});
