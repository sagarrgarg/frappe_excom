import { describe, expect, it } from "vitest";
import { MESSAGE_TYPES, awaitsDeliveryReceipt, mapMessageType } from "../messages";

/**
 * The fence for a bug that shipped: `Call` was added to one of the two message-type maps and not
 * the other, so calls rendered as plain text bubbles with a delivery spinner counting upward for
 * ever. There is one map now; these tests are here so a backend type cannot be half-added again.
 */

// Every option on Excom Message.message_type. Keep in step with the doctype.
const BACKEND_TYPES = [
  "Text",
  "Image",
  "Video",
  "Audio",
  "Document",
  "Sticker",
  "Location",
  "Interactive",
  "Template",
  "Flow",
  "Reaction",
  "Contact",
  "Button",
  "Email",
  "Call",
];

describe("message type mapping", () => {
  it("has an explicit entry for every backend type", () => {
    const missing = BACKEND_TYPES.filter((t) => !(t in MESSAGE_TYPES));
    expect(missing).toEqual([]);
  });

  it("never silently falls back for a known type", () => {
    // "text" is the fallback, so only Text may map to it.
    const wrongly = BACKEND_TYPES.filter((t) => t !== "Text" && mapMessageType(t) === "text");
    expect(wrongly).toEqual([]);
  });

  it("maps a call to its own type", () => {
    expect(mapMessageType("Call")).toBe("call");
  });

  it("still falls back for something it has never seen", () => {
    expect(mapMessageType("SomethingNew")).toBe("text");
  });
});

describe("delivery receipts", () => {
  it("a call does not wait for one", () => {
    expect(awaitsDeliveryReceipt("call")).toBe(false);
  });

  it("a message does", () => {
    expect(awaitsDeliveryReceipt("text")).toBe(true);
    expect(awaitsDeliveryReceipt("email")).toBe(true);
  });
});
