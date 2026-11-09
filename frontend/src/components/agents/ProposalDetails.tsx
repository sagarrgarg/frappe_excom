import { Chip } from "../primitives";
import type { LeadPayload, PlaybookPayload, ProposalPayload, QuotationPayload, TemplatePayload, TodoPayload } from "../../types/agents";
import { noteParts, splitVariables } from "../../lib/agents-logic";
import { HtmlPreview, parseJson } from "./bits";

export { pricedFrom } from "../../lib/agents-logic";

/** Proposal actions shown from their payload instead of the HTML preview. */
const STRUCTURED = new Set(["create_quotation", "update_playbook", "update_lead", "create_todo", "note", "approve_template"]);

const num = (v: unknown, digits = 2) => Number(v || 0).toLocaleString("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits });

export function PricedChip({ priceList }: { priceList: string | null }) {
  if (priceList === null) return null;
  return <Chip size="sm" accent="amber" label={priceList ? `Prices from ${priceList}` : "Prices from the price list"} title="The prices are ERPNext's own, written in by code: the agent does not choose them" />;
}

/**
 * What a proposal would do, in plain words: a quotation as a table, a playbook suggestion, a lead
 * classification, a ToDo, a note (with its title, for a Custom agent's draft) or an email template
 * from its payload; anything else as its preview.
 */
export function ProposalDetails({ action, payload: raw, previewHtml, title }: { action: string; payload?: ProposalPayload | string | null; previewHtml?: string | null; title: string }) {
  const payload = parseJson<Record<string, unknown>>(raw);
  if (payload && STRUCTURED.has(action)) {
    if (action === "create_quotation") return <QuotationTable q={payload as QuotationPayload} />;
    if (action === "update_playbook") return <PlaybookSuggestion p={payload as PlaybookPayload} />;
    if (action === "update_lead") return <LeadClass p={payload as LeadPayload} />;
    if (action === "create_todo") return <Todo p={payload as TodoPayload} />;
    if (action === "approve_template") return <TemplateProposal p={payload as TemplatePayload} />;
    const note = action === "note" ? noteParts(payload) : null;
    if (note) return <Note title={note.title} text={note.text} />;
  }
  if (previewHtml) return <HtmlPreview html={previewHtml} title={title} />;
  if (payload && typeof payload.body === "string") return (
    <div className="min-w-0">
      {typeof payload.title === "string" && payload.title && <p className="text-sm font-medium text-ink-1 break-words">{payload.title}</p>}
      <p className="text-sm text-ink-1 whitespace-pre-wrap break-words">{payload.body}</p>
    </div>
  );
  return <p className="text-sm text-ink-3">No preview.</p>;
}

function QuotationTable({ q }: { q: QuotationPayload }) {
  const lines = q.lines ?? [];
  const total = lines.reduce((n, l) => n + Number(l.amount || 0), 0);
  return (
    <div className="space-y-1.5 min-w-0">
      <p className="text-sm text-ink-2 break-words">
        A draft quotation{q.party ? <> for {q.party_type || "Lead"} <span className="text-ink-1">{q.party}</span></> : null}
        {q.opportunity ? <>, on opportunity <span className="text-ink-1">{q.opportunity}</span></> : null}
        {q.price_list ? <>, prices from <span className="text-ink-1 font-medium">{q.price_list}</span></> : null}.
        {" "}It stays a draft: a person sends it.
      </p>
      {lines.length === 0 ? <p className="text-sm text-ink-3">No items.</p> : (
        <div className="rounded-md border border-border overflow-x-auto">
          <table className="w-full text-sm min-w-0">
            <caption className="sr-only">Quotation items{q.price_list ? `, prices from ${q.price_list}` : ""}</caption>
            <thead className="bg-surface-sunken text-xs text-ink-3">
              <tr>
                <th scope="col" className="text-left font-normal px-2 py-1.5">Item</th>
                <th scope="col" className="text-right font-normal px-2 py-1.5">Qty</th>
                <th scope="col" className="text-right font-normal px-2 py-1.5">Rate</th>
                <th scope="col" className="text-right font-normal px-2 py-1.5">Amount</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {lines.map((l, i) => (
                <tr key={`${l.item_code}-${i}`}>
                  <td className="px-2 py-1.5 text-ink-1 break-words min-w-[8rem]">
                    {l.item_name || l.item_code}
                    {l.item_name && l.item_name !== l.item_code && <span className="block text-xs text-ink-3 break-all">{l.item_code}</span>}
                  </td>
                  <td className="px-2 py-1.5 text-right tabular-nums text-ink-1">{num(l.qty, 0)}</td>
                  <td className={Number(l.rate) ? "px-2 py-1.5 text-right tabular-nums text-ink-1" : "px-2 py-1.5 text-right text-crayon-rose-text"}>{Number(l.rate) ? num(l.rate) : "No price"}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums text-ink-1">{num(l.amount)}</td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="border-t border-border">
                <th scope="row" colSpan={3} className="text-right font-medium text-ink-1 px-2 py-1.5">Total</th>
                <td className="px-2 py-1.5 text-right tabular-nums font-medium text-ink-1">{num(total)}</td>
              </tr>
            </tfoot>
          </table>
        </div>
      )}
      {lines.some((l) => !Number(l.rate)) && <p className="text-xs text-crayon-rose-text">Some items have no price on this price list: add one in ERPNext before approving.</p>}
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return <div className="flex gap-2 min-w-0"><dt className="w-20 shrink-0 text-ink-3">{label}</dt><dd className="min-w-0 flex-1 break-words text-ink-1">{children}</dd></div>;
}

function PlaybookSuggestion({ p }: { p: PlaybookPayload }) {
  return (
    <div className="space-y-1.5 min-w-0">
      <dl className="text-sm space-y-1 min-w-0">
        <Row label="Section">{p.kind || "Process rules"}</Row>
        <Row label="Add">{p.text || "—"}</Row>
        {p.why && <Row label="Why">{p.why}</Row>}
      </dl>
      <p className="text-xs text-ink-3">Approving adds it to the agent's playbook, which then goes back to review before the agent uses it.</p>
    </div>
  );
}

function LeadClass({ p }: { p: LeadPayload }) {
  return (
    <dl className="text-sm space-y-1 min-w-0">
      <Row label="Buyer type">{p.customer_type ? <Chip size="sm" accent="blue" label={p.customer_type} /> : "—"}</Row>
      {p.note && <Row label="Why">{p.note}</Row>}
    </dl>
  );
}

function Todo({ p }: { p: TodoPayload }) {
  return (
    <dl className="text-sm space-y-1 min-w-0">
      {p.title && <Row label="Title">{p.title}</Row>}
      <Row label="For">{p.allocated_to || "The agent's owner"}</Row>
      <Row label="To do">{p.text || "—"}</Row>
    </dl>
  );
}

function Note({ title, text }: { title: string; text: string }) {
  return (
    <div className="space-y-1 min-w-0">
      {title && <p className="text-sm font-medium text-ink-1 break-words">{title}</p>}
      {text ? <p className="text-sm text-ink-1 whitespace-pre-wrap break-words">{text}</p> : <p className="text-sm text-ink-3">No text.</p>}
    </div>
  );
}

/**
 * A template's words with its variables marked: {{1}}… as a highlighted slot, an email's
 * {{content}} as a muted "[lines written by code]". Line breaks are kept.
 */
export function TemplateWords({ subject, body }: { subject?: string | null; body?: string | null }) {
  const parts = splitVariables(body);
  return (
    <div className="space-y-1 min-w-0">
      {subject && <p className="text-sm font-medium text-ink-1 break-words"><TemplateLine text={subject} /></p>}
      {parts.length === 0 ? <p className="text-sm text-ink-3">No words yet.</p> : (
        <p className="text-sm text-ink-1 whitespace-pre-wrap break-words">
          {parts.map((part, i) => "text" in part ? <span key={i}>{part.text}</span> : <Slot key={i} name={part.variable} />)}
        </p>
      )}
    </div>
  );
}

function TemplateLine({ text }: { text: string }) {
  return <>{splitVariables(text).map((part, i) => "text" in part ? <span key={i}>{part.text}</span> : <Slot key={i} name={part.variable} />)}</>;
}

function Slot({ name }: { name: string }) {
  if (name === "content") return <span className="text-ink-3 italic">[lines written by code]</span>;
  return <mark className="rounded px-1 bg-crayon-blue-tint text-crayon-blue-text font-mono text-xs align-baseline" title="Filled in by code when it is sent">{`{{${name}}}`}</mark>;
}

function TemplateProposal({ p }: { p: TemplatePayload }) {
  return (
    <div className="space-y-1.5 min-w-0">
      <TemplateWords subject={p.subject} body={p.body} />
      <p className="text-xs text-ink-3">An email template Excom wrote for your agents. Approving lets them use it; the marked parts are filled in by code each time.</p>
    </div>
  );
}
