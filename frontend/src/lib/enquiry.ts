/**
 * A web-form enquiry. Intake files it as a comment on the Lead (services/intake.py) whose first line
 * is "Enquiry from <source>" and whose last is "Page: <url>". Shown as what it is — the customer
 * writing in — instead of an orange internal note from Administrator, which is how it used to look
 * and read: as if the shop had written it.
 */
export function parseEnquiry(text: string): { source: string; test: boolean; meta: [string, string][]; body: string; page: string } | null {
  let lines = (text || "").split("\n");
  const test = lines[0]?.startsWith("Platform TEST lead");
  if (test) lines = lines.slice(1);
  const head = /^Enquiry from (.+)$/.exec(lines[0] || "");
  if (!head) return null;
  lines = lines.slice(1);
  let page = "";
  if (lines.length && /^Page: /.test(lines[lines.length - 1])) page = lines.pop()!.slice(6);
  // Leading "Key: value" lines are form fields (Subject, Language, Company Name…), not the message.
  // Each word of a key is capitalised, so "Hello there: is this in stock?" stays in the message.
  const meta: [string, string][] = [];
  while (lines.length && /^[A-Z][A-Za-z]*(?: [A-Z][A-Za-z]*){0,2}: \S/.test(lines[0])) {
    const [k, ...v] = lines.shift()!.split(": ");
    meta.push([k, v.join(": ")]);
  }
  return { source: head[1], test, meta, body: lines.join("\n").trim(), page };
}

/**
 * The inbox preview of a web-form enquiry. Intake stores the enquiry text as the thread's preview,
 * which starts with the form's fields ("Subject: Price Enquiry Language: en From: Website…"). The row
 * shows the subject and then the message, as a mail client would.
 */
export function previewOf(text: string): string {
  if (!text || !/^(Subject|Enquiry from) ?:?/.test(text)) return text;
  const lines = text.split("\n");
  if (/^Enquiry from /.test(lines[0] || "")) lines.shift();
  let subject = "";
  while (lines.length && /^[A-Z][A-Za-z]*(?: [A-Z][A-Za-z]*){0,2}: \S/.test(lines[0])) {
    const [k, ...v] = lines.shift()!.split(": ");
    if (k === "Subject") subject = v.join(": ");
  }
  const body = lines.join(" ").replace(/\s+/g, " ").trim();
  return [subject, body].filter(Boolean).join(" — ") || text;
}
