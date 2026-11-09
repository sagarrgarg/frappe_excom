import { describe, expect, it } from "vitest";
import { bounceRate, inZone, pricedFrom } from "../agents-logic";

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
