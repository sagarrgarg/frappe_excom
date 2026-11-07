import { describe, expect, it } from "vitest";
import { parseEnquiry } from "../enquiry";

describe("parseEnquiry", () => {
  it("reads the comment intake writes for a web-form enquiry", () => {
    const text = [
      "Enquiry from CockColours Website",
      "Subject: Price Enquiry",
      "Language: en",
      "From: Website Contact Page (/contact-us)",
      "",
      "Hello,",
      "",
      "I need 1,000 pieces.",
      "Page: /contact-us",
    ].join("\n");
    const e = parseEnquiry(text)!;
    expect(e.source).toBe("CockColours Website");
    expect(e.test).toBe(false);
    expect(e.meta).toEqual([["Subject", "Price Enquiry"], ["Language", "en"], ["From", "Website Contact Page (/contact-us)"]]);
    expect(e.body).toBe("Hello,\n\nI need 1,000 pieces.");
    expect(e.page).toBe("/contact-us");
  });

  it("knows a platform test lead", () => {
    const e = parseEnquiry("Platform TEST lead (kept out of KPIs)\nEnquiry from Meta Lead Form\nWant a quote")!;
    expect(e.test).toBe(true);
    expect(e.source).toBe("Meta Lead Form");
    expect(e.body).toBe("Want a quote");
  });

  it("leaves an ordinary note alone", () => {
    expect(parseEnquiry("Called, will send the catalogue tomorrow")).toBeNull();
    expect(parseEnquiry("")).toBeNull();
  });

  it("does not take a sentence with a colon for a form field", () => {
    const e = parseEnquiry("Enquiry from Website\nNote: we need it fast, please reply today")!;
    expect(e.meta).toEqual([["Note", "we need it fast, please reply today"]]);
    const f = parseEnquiry("Enquiry from Website\nHello there: is this available?")!;
    expect(f.meta).toEqual([]);
    expect(f.body).toBe("Hello there: is this available?");
  });
});
