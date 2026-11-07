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
