import { describe, expect, it } from "vitest";
import { firstOpenStep, fixAction, isPending, itemMark, pickSections, progress, stepAccent, stepLabel, type StepState } from "../ai-setup";

const steps = (states: StepState[]) => states.map((state, i) => ({ id: `s${i}`, state }));

describe("stepLabel / stepAccent", () => {
  it("names every state in plain words", () => {
    expect(stepLabel("done")).toBe("Done");
    expect(stepLabel("attention")).toBe("Needs attention");
    expect(stepLabel("todo")).toBe("Not started");
    expect(stepLabel("optional")).toBe("Optional");
    expect(stepLabel("not_needed")).toBe("Not needed");
  });
  it("colours done green, attention amber, not started rose, the rest quiet", () => {
    expect(stepAccent("done")).toBe("green");
    expect(stepAccent("attention")).toBe("amber");
    expect(stepAccent("todo")).toBe("rose");
    expect(stepAccent("optional")).toBe("blue");
    expect(stepAccent("not_needed")).toBe("neutral");
  });
  it("only todo and attention are pending", () => {
    expect((["done", "attention", "todo", "optional", "not_needed"] as StepState[]).filter(isPending)).toEqual(["attention", "todo"]);
  });
});

describe("progress", () => {
  it("counts not needed as done and leaves an unfinished optional step out of the total", () => {
    expect(progress(steps(["done", "done", "not_needed", "todo", "attention", "optional"]))).toEqual({ done: 3, total: 5, pending: 2 });
  });
  it("counts an optional step once it is done", () => {
    expect(progress(steps(["done", "done"]))).toEqual({ done: 2, total: 2, pending: 0 });
  });
  it("is zero of zero for no steps", () => {
    expect(progress([])).toEqual({ done: 0, total: 0, pending: 0 });
  });
});

describe("firstOpenStep", () => {
  it("opens the first step that needs something", () => {
    expect(firstOpenStep(steps(["done", "optional", "attention", "todo"]))).toBe("s2");
  });
  it("is null when nothing is pending", () => {
    expect(firstOpenStep(steps(["done", "not_needed", "optional"]))).toBeNull();
  });
});

describe("itemMark", () => {
  it("ticks a passing check", () => {
    expect(itemMark({ ok: true, severity: "required" }).mark).toBe("✓");
  });
  it("flags a missing required check, circles a missing recommended one", () => {
    expect(itemMark({ ok: false, severity: "required" })).toMatchObject({ mark: "!", accent: "rose" });
    expect(itemMark({ ok: false, severity: "recommended" })).toMatchObject({ mark: "○", accent: "amber" });
  });
  it("keeps info rows neutral whether or not they pass", () => {
    expect(itemMark({ ok: false, severity: "info" }).accent).toBe("neutral");
    expect(itemMark({ ok: true, severity: "info" }).accent).toBe("neutral");
  });
});

describe("fixAction", () => {
  it("reads a DNS check with its agent and mailbox row", () => {
    expect(fixAction("check_dns:AG-0001:abc123")).toEqual({ kind: "dns", agent: "AG-0001", row: "abc123" });
  });
  it("ignores a malformed DNS check", () => {
    expect(fixAction("check_dns:AG-0001")).toBeNull();
  });
  it("opens Desk and full URLs as links", () => {
    expect(fixAction("/app/excom-ai-signal-code?status=Proposed")).toEqual({ kind: "desk", href: "/app/excom-ai-signal-code?status=Proposed" });
    expect(fixAction("https://x.example/app/ai-settings")).toEqual({ kind: "desk", href: "https://x.example/app/ai-settings" });
  });
  it("routes pages of this app inside it", () => {
    expect(fixAction("/excom/agents/AG-1")).toEqual({ kind: "app", to: "/agents/AG-1" });
  });
  it("is null for nothing or an unknown action", () => {
    expect(fixAction(null)).toBeNull();
    expect(fixAction("")).toBeNull();
    expect(fixAction("do_something")).toBeNull();
  });
  it("never links to another site through a protocol-relative path or a script", () => {
    expect(fixAction("//evil.example/app")).toBeNull();
    expect(fixAction("javascript:alert(1)")).toBeNull();
  });
});

describe("pickSections", () => {
  const sections = [
    { label: "Budget", fields: [{ fieldname: "daily_budget_total" }, { fieldname: "enabled" }] },
    { label: "Voice", fields: [{ fieldname: "voice_service_url" }] },
  ];
  it("returns the sections untouched without a list", () => {
    expect(pickSections(sections)).toBe(sections);
  });
  it("keeps only the named fields and drops empty sections", () => {
    expect(pickSections(sections, ["daily_budget_total"])).toEqual([{ label: "Budget", fields: [{ fieldname: "daily_budget_total" }] }]);
  });
  it("keeps the schema's order, not the list's", () => {
    expect(pickSections(sections, ["voice_service_url", "enabled"]).map((s) => s.fields[0].fieldname)).toEqual(["enabled", "voice_service_url"]);
  });
});
