import { useState } from "react";
import { Building2, Mail, Phone, Clock, FileText, User, Shield, ShieldAlert, ExternalLink, Link2, Loader2, Receipt, CalendarPlus } from "lucide-react";
import { Chip, Button, SegmentedControl, EmptyState, Avatar } from "./primitives";
import type { Accent } from "./primitives/Chip";
import { useLinkedEntities } from "../hooks/useLinkedEntities";
import { useConversationStats, formatResponseTime } from "../hooks/useConversationStats";
import { useRelatedDocuments, getDocDate, getDocPartyName, type ERPDocument } from "../hooks/useRelatedInvoices";
import { channelMeta } from "../lib/channels";
import { cn } from "./ui/utils";
import type { Conversation } from "../types";

interface OmniIdentityPanelProps {
  conversation: Conversation;
  onAccountSwitch?: (accountId: string) => void;
  /** Rendered inside the P1 details pane: no outer width/border/header. */
  embedded?: boolean;
}

const ENTITY_ACCENT: Record<string, Accent> = { Lead: "amber", Opportunity: "violet", Customer: "green", Supplier: "sand", Contact: "teal" };

export function getFormUrl(doctype: string, docname: string): string {
  const slug = doctype.toLowerCase().replace(/\s+/g, "-");
  return `${window.location.origin}/app/${encodeURIComponent(slug)}/${encodeURIComponent(docname)}`;
}

const DOC_SECTIONS: { key: keyof ReturnType<typeof useRelatedDocuments>["documents"]; label: string; doctype: string; accent: Accent }[] = [
  { key: "quotations", label: "Quotations", doctype: "Quotation", accent: "violet" },
  { key: "sales_orders", label: "Sales Orders", doctype: "Sales Order", accent: "teal" },
  { key: "delivery_notes", label: "Delivery Notes", doctype: "Delivery Note", accent: "teal" },
  { key: "sales_invoices", label: "Sales Invoices", doctype: "Sales Invoice", accent: "green" },
  { key: "rfqs", label: "RFQs", doctype: "Request for Quotation", accent: "sand" },
  { key: "purchase_orders", label: "Purchase Orders", doctype: "Purchase Order", accent: "sand" },
  { key: "purchase_receipts", label: "Purchase Receipts", doctype: "Purchase Receipt", accent: "amber" },
  { key: "purchase_invoices", label: "Purchase Invoices", doctype: "Purchase Invoice", accent: "blue" },
];

function docAccent(status: string): Accent {
  if (["Completed", "Paid", "Delivered", "Submitted"].includes(status)) return "green";
  if (["Overdue", "Cancelled"].includes(status)) return "rose";
  if (status === "Draft") return "neutral";
  return "amber";
}

/**
 * Identity panel — sections: Contact, Channels, Linked ERP, Summary, Quick actions | Transactions.
 * Re-skinned onto tokens; sections reused by the P1 DetailsDrawer (embedded) and the legacy tree.
 */
export function OmniIdentityPanel({ conversation, onAccountSwitch, embedded }: OmniIdentityPanelProps) {
  const { contactInfo } = conversation;
  const [tab, setTab] = useState<"profile" | "invoices">("profile");
  const { linkedEntities, isLoading: linkedLoading } = useLinkedEntities(conversation.id);
  const { stats, isLoading: statsLoading } = useConversationStats(conversation.id);
  const { documents, isLoading: docsLoading } = useRelatedDocuments(conversation.id);
  const allAccounts = [conversation.activeAccount, ...conversation.otherAccounts].filter(Boolean);
  const byChannel = allAccounts.reduce<Record<string, typeof allAccounts>>((acc, a) => { (acc[a.channel] ||= []).push(a); return acc; }, {});
  const totalDocs = Object.values(documents).reduce((s, arr) => s + arr.length, 0);

  const body = (
    <div className="flex flex-col min-h-0 h-full">
      <SegmentedControl
        value={tab}
        onChange={setTab}
        className="px-2 border-b border-border bg-surface"
        segments={[{ value: "profile", label: "Profile", icon: <User /> }, { value: "invoices", label: "Transactions", icon: <Receipt />, count: totalDocs }]}
      />
      <div className="flex-1 min-h-0 overflow-y-auto">
        {tab === "profile" ? (
          <div className="p-3 space-y-4">
            <div className="flex items-center gap-3 min-w-0">
              <Avatar name={conversation.contactName} src={conversation.contactAvatar} size={40} />
              <div className="min-w-0">
                <p className="text-md text-ink-1 truncate">{conversation.contactName}</p>
                {contactInfo.company && <p className="text-xs text-ink-3 truncate">{contactInfo.company}</p>}
              </div>
            </div>

            {conversation.assignedTo && (
              <Section title="Assigned to">
                <div className="flex items-center gap-2 min-w-0"><Avatar name={conversation.assignedTo.name} src={conversation.assignedTo.avatar} size={24} /><span className="text-sm text-ink-1 truncate">{conversation.assignedTo.name}</span></div>
              </Section>
            )}

            <Section title="Channels & accounts" hint={onAccountSwitch ? "Pick an account to reply from it" : undefined}>
              <div className="space-y-2">
                {Object.entries(byChannel).map(([ch, accounts]) => {
                  const m = channelMeta(ch);
                  return (
                    <div key={ch} className="min-w-0">
                      <div className="flex items-center gap-1.5 text-xs text-ink-2 mb-1"><m.icon className={cn("size-3.5", `text-crayon-${m.accent}-base`)} />{m.label}<span className="text-ink-3 tabular-nums ml-auto">{accounts.length}</span></div>
                      <div className="space-y-1">
                        {accounts.map((a) => {
                          const active = a.id === conversation.activeAccount?.id;
                          const canSwitch = Boolean(onAccountSwitch) && a.hasAccess && !active;
                          return (
                            <button key={a.id} type="button" disabled={!canSwitch} onClick={() => canSwitch && onAccountSwitch?.(a.id)}
                              className={cn("w-full flex items-center gap-2 rounded-md px-2 h-9 text-left min-w-0 border", active ? "border-crayon-blue-base/40 bg-crayon-blue-tint" : canSwitch ? "border-transparent hover:bg-surface-hover" : "border-transparent opacity-70")}>
                              <div className="flex-1 min-w-0"><p className="text-sm text-ink-1 truncate">{a.name}</p><p className="text-xs text-ink-3 truncate">{a.identifier}</p></div>
                              {a.hasAccess ? <Shield className="size-3.5 text-crayon-green-base shrink-0" aria-label="Access" /> : <ShieldAlert className="size-3.5 text-crayon-amber-base shrink-0" aria-label="No access" />}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  );
                })}
              </div>
            </Section>

            <Section title="Contact">
              <dl className="space-y-1.5 text-sm">
                <Item icon={<Mail />} label="Email" value={contactInfo.email} href={contactInfo.email ? `mailto:${contactInfo.email}` : undefined} />
                <Item icon={<Phone />} label="Phone" value={contactInfo.phone} href={contactInfo.phone ? `tel:${contactInfo.phone}` : undefined} />
                {contactInfo.company && <Item icon={<Building2 />} label="Company" value={contactInfo.company} />}
              </dl>
            </Section>

            <Section title="Linked ERP">
              {linkedLoading ? <Loading /> : linkedEntities.length === 0 ? (
                <p className="text-xs text-ink-3">No linked Lead, Customer or Contact. <a className="underline" href={getFormUrl("Omni Identity", conversation.id)} target="_blank" rel="noreferrer">Link in Desk</a>.</p>
              ) : (
                <div className="space-y-1">
                  {linkedEntities.map((e) => (
                    <a key={`${e.linked_doctype}-${e.linked_name}`} href={getFormUrl(e.linked_doctype, e.linked_name)} target="_blank" rel="noreferrer" className="flex items-center gap-2 rounded-md px-2 h-10 hover:bg-surface-hover min-w-0 group">
                      <Chip size="sm" accent={ENTITY_ACCENT[e.linked_doctype] || "neutral"} label={e.linked_doctype} />
                      <div className="flex-1 min-w-0"><p className="text-sm text-ink-1 truncate">{e.title}</p><p className="text-xs text-ink-3 truncate">{e.linked_name}{e.role && e.role !== "Unknown" ? ` · ${e.role}` : ""}</p></div>
                      <ExternalLink className="size-4 text-ink-muted group-hover:text-ink-2 shrink-0" />
                    </a>
                  ))}
                </div>
              )}
            </Section>

            <Section title="Summary">
              {statsLoading ? <Loading /> : (
                <dl className="grid grid-cols-2 gap-x-3 gap-y-1.5 text-sm">
                  <Stat label="Messages" value={String(stats.total_messages)} />
                  <Stat label="In / Out" value={`${stats.inbound_count} / ${stats.outbound_count}`} />
                  <Stat label="Team replied" value={stats.erp_users_replied ? "Yes" : "No"} accent={stats.erp_users_replied ? "green" : "rose"} />
                  <Stat label="Avg response" value={formatResponseTime(stats.avg_response_time_seconds)} />
                </dl>
              )}
            </Section>

            <Section title="Quick actions">
              <div className="grid grid-cols-1 gap-1.5">
                <Button size="sm" className="justify-start" onClick={() => { const e = linkedEntities[0]; window.open(e ? getFormUrl(e.linked_doctype, e.linked_name) : getFormUrl("Omni Identity", conversation.id), "_blank"); }}><FileText />View in ERPNext</Button>
                <Button size="sm" className="justify-start" disabled={!contactInfo.email} onClick={() => window.open(`mailto:${contactInfo.email}`, "_blank")}><Mail />Send email</Button>
                <Button size="sm" className="justify-start" onClick={() => { const c = linkedEntities.find((e) => e.linked_doctype === "Contact"); window.open(`${window.location.origin}/app/event/new${c ? `?party_type=Contact&party=${encodeURIComponent(c.linked_name)}` : ""}`, "_blank"); }}><CalendarPlus />Schedule meeting</Button>
              </div>
            </Section>
          </div>
        ) : (
          <div className="p-3 space-y-4">
            {docsLoading ? <Loading /> : totalDocs === 0 ? <EmptyState icon={<Receipt />} title="No transactions" hint="Link a Customer or Supplier to see documents." compact /> : (
              DOC_SECTIONS.filter((s) => documents[s.key].length > 0).map((s) => (
                <Section key={s.key} title={s.label} count={documents[s.key].length}>
                  <div className="space-y-1">
                    {documents[s.key].map((d: ERPDocument) => (
                      <a key={d.name} href={getFormUrl(s.doctype, d.name)} target="_blank" rel="noreferrer" className="block rounded-md border border-border px-2 py-1.5 hover:bg-surface-hover min-w-0">
                        <div className="flex items-center gap-2 min-w-0"><span className="text-sm text-ink-1 truncate">{d.name}</span><Chip size="sm" accent={docAccent(d.status)} label={d.status} className="ml-auto" /></div>
                        <div className="flex items-center justify-between text-xs text-ink-3 tabular-nums mt-0.5"><span>{getDocDate(d)}</span><span className="text-ink-2">{d.currency} {(d.grand_total || 0).toLocaleString("en-IN")}</span></div>
                        {d.outstanding_amount != null && d.outstanding_amount > 0 && <div className="text-xs text-crayon-amber-text text-right tabular-nums">{d.currency} {d.outstanding_amount.toLocaleString("en-IN")} due</div>}
                        {getDocPartyName(d) && <p className="text-xs text-ink-3 truncate">{getDocPartyName(d)}</p>}
                      </a>
                    ))}
                  </div>
                </Section>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  );

  if (embedded) return body;
  return (
    <div className="w-80 bg-surface border-l border-border flex flex-col h-full shrink-0 overflow-hidden">
      <div className="shrink-0 px-3 h-header-h flex items-center border-b border-border"><h2 className="text-md text-ink-1">Details</h2></div>
      {body}
    </div>
  );
}

function Section({ title, hint, count, children }: { title: string; hint?: string; count?: number; children: React.ReactNode }) {
  return (
    <section className="min-w-0">
      <h4 className="text-xs text-ink-3 mb-1.5 flex items-center gap-1.5">{title}{typeof count === "number" && <span className="tabular-nums">· {count}</span>}{hint && <span className="text-ink-muted font-normal truncate">— {hint}</span>}</h4>
      {children}
    </section>
  );
}
function Item({ icon, label, value, href }: { icon: React.ReactNode; label: string; value?: string; href?: string }) {
  return (
    <div className="flex items-center gap-2 min-w-0 [&_svg]:size-4 [&_svg]:text-ink-muted [&_svg]:shrink-0">
      {icon}<dt className="text-xs text-ink-3 w-14 shrink-0">{label}</dt>
      <dd className="text-sm text-ink-1 truncate min-w-0">{href ? <a href={href} className="hover:underline">{value}</a> : value || "—"}</dd>
    </div>
  );
}
function Stat({ label, value, accent }: { label: string; value: string; accent?: Accent }) {
  return <div className="min-w-0"><dt className="text-xs text-ink-3">{label}</dt><dd className={cn("text-sm tabular-nums truncate", accent ? `text-crayon-${accent}-text` : "text-ink-1")}>{value}</dd></div>;
}
function Loading() { return <div className="flex items-center gap-2 text-xs text-ink-3 py-2"><Loader2 className="size-4 animate-spin" />Loading…</div>; }
