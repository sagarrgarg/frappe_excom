import { useState } from "react";
import {
  Building2,
  Mail,
  Phone,
  DollarSign,
  Clock,
  FileText,
  User,
  MessageCircle,
  Instagram,
  Shield,
  ShieldAlert,
  ArrowRight,
  ExternalLink,
  Link2,
  Loader2,
  Receipt,
} from "lucide-react";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Separator } from "./ui/separator";
import { useLinkedEntities } from "../hooks/useLinkedEntities";
import {
  useConversationStats,
  formatResponseTime,
} from "../hooks/useConversationStats";
import {
  useRelatedDocuments,
  getDocDate,
  getDocPartyName,
  type ERPDocument,
} from "../hooks/useRelatedInvoices";
import type { Conversation } from "../types";

interface OmniIdentityPanelProps {
  conversation: Conversation;
  onAccountSwitch?: (accountId: string) => void;
}

const CHANNEL_ICONS: Record<string, React.ReactElement> = {
  whatsapp: <MessageCircle className="w-4 h-4 text-green-700" />,
  email: <Mail className="w-4 h-4 text-blue-700" />,
  instagram: <Instagram className="w-4 h-4 text-pink-700" />,
  calls: <Phone className="w-4 h-4 text-purple-700" />,
};

const CHANNEL_LABELS: Record<string, string> = {
  whatsapp: "WhatsApp",
  email: "Email",
  instagram: "Instagram",
  calls: "Phone",
};

const ENTITY_COLORS: Record<string, string> = {
  Lead: "bg-yellow-500/10 text-yellow-700 border-yellow-500/20",
  Customer: "bg-green-500/10 text-green-700 border-green-500/20",
  Supplier: "bg-blue-500/10 text-blue-700 border-blue-500/20",
  Contact: "bg-cyan-500/10 text-cyan-700 border-cyan-500/20",
};

function getFormUrl(doctype: string, docname: string): string {
  const base = typeof window !== "undefined" ? window.location.origin : "";
  const slug = doctype.toLowerCase().replace(/\s+/g, "-");
  return `${base}/app/${encodeURIComponent(slug)}/${encodeURIComponent(docname)}`;
}

export function OmniIdentityPanel({
  conversation,
  onAccountSwitch,
}: OmniIdentityPanelProps) {
  const { contactInfo } = conversation;
  const [activeTab, setActiveTab] = useState<"profile" | "invoices">("profile");

  const handleCall = async (phoneNum?: string) => {
    const num = phoneNum || contactInfo?.phone;
    if (!num) {
      toast.error("No phone number available for this contact");
      return;
    }
    try {
      const res = await (window as any).frappe?.call?.({
        method: "excom.excom.api.voice.initiate_call",
        args: { to_number: num, thread_id: conversation.id }
      });
      if (res?.message?.status === "success") {
        toast.success("Call initiated to " + num);
      }
    } catch (e: any) {
      console.error("Call error:", e);
      let msg = "Call failed";
      if (e?._server_messages) {
        try {
          const parsed = JSON.parse(e._server_messages);
          const inner = JSON.parse(parsed[0]);
          msg = inner?.message || parsed[0];
        } catch {}
      } else if (e?.message) {
        msg = e.message;
      }
      toast.error(msg);
    }
  };


  const { linkedEntities, isLoading: linkedLoading } = useLinkedEntities(
    conversation.id
  );

  const { stats, isLoading: statsLoading } = useConversationStats(
    conversation.id
  );

  const { documents, isLoading: docsLoading } = useRelatedDocuments(
    conversation.id
  );

  const allAccounts = [
    conversation.activeAccount,
    ...conversation.otherAccounts,
  ];
  const accountsByChannel = allAccounts.reduce((acc, account) => {
    if (!acc[account.channel]) {
      acc[account.channel] = [];
    }
    acc[account.channel].push(account);
    return acc;
  }, {} as Record<string, typeof allAccounts>);

  const badgeClasses: Record<string, string> = {
    indigo: "bg-indigo-500/20 text-indigo-700",
    cyan: "bg-cyan-500/20 text-cyan-700",
    teal: "bg-teal-500/20 text-teal-700",
    green: "bg-green-500/20 text-green-700",
    violet: "bg-violet-500/20 text-violet-700",
    sky: "bg-sky-500/20 text-sky-700",
    amber: "bg-amber-500/20 text-amber-700",
    blue: "bg-blue-500/20 text-blue-700",
  };

  const docSections: { key: keyof typeof documents; label: string; icon: React.ReactElement; color: string; doctype: string }[] = [
    { key: "quotations", label: "Quotations", icon: <FileText className="w-3.5 h-3.5 text-indigo-700" />, color: "indigo", doctype: "Quotation" },
    { key: "sales_orders", label: "Sales Orders", icon: <FileText className="w-3.5 h-3.5 text-cyan-700" />, color: "cyan", doctype: "Sales Order" },
    { key: "delivery_notes", label: "Delivery Notes", icon: <FileText className="w-3.5 h-3.5 text-teal-700" />, color: "teal", doctype: "Delivery Note" },
    { key: "sales_invoices", label: "Sales Invoices", icon: <DollarSign className="w-3.5 h-3.5 text-green-700" />, color: "green", doctype: "Sales Invoice" },
    { key: "rfqs", label: "RFQs", icon: <FileText className="w-3.5 h-3.5 text-violet-700" />, color: "violet", doctype: "Request for Quotation" },
    { key: "purchase_orders", label: "Purchase Orders", icon: <FileText className="w-3.5 h-3.5 text-sky-700" />, color: "sky", doctype: "Purchase Order" },
    { key: "purchase_receipts", label: "Purchase Receipts", icon: <Receipt className="w-3.5 h-3.5 text-amber-700" />, color: "amber", doctype: "Purchase Receipt" },
    { key: "purchase_invoices", label: "Purchase Invoices", icon: <Receipt className="w-3.5 h-3.5 text-blue-700" />, color: "blue", doctype: "Purchase Invoice" },
  ];

  const totalDocs = Object.values(documents).reduce((s, arr) => s + arr.length, 0);
  const hasDocs = totalDocs > 0;

  return (
    <div className="w-80 bg-gradient-to-b from-zinc-100 to-white border-l border-zinc-200 flex flex-col h-full shrink-0 overflow-hidden">
      <div className="shrink-0 p-3 border-b border-zinc-200">
        <h2 className="font-semibold text-zinc-900">Omni Identity</h2>
        <p className="text-xs text-zinc-600 mt-1">Unified contact profile</p>
      </div>

      {/* Tab Header */}
      <div className="shrink-0 flex border-b border-zinc-200">
        <button
          onClick={() => setActiveTab("profile")}
          className={`flex-1 py-2.5 text-xs font-medium text-center transition-colors border-b-2 ${
            activeTab === "profile"
              ? "border-blue-500 text-zinc-900 bg-zinc-100/30"
              : "border-transparent text-zinc-600 hover:text-zinc-900"
          }`}
        >
          <User className="w-3.5 h-3.5 inline mr-1" />
          Profile
        </button>
        <button
          onClick={() => setActiveTab("invoices")}
          className={`flex-1 py-2.5 text-xs font-medium text-center transition-colors border-b-2 ${
            activeTab === "invoices"
              ? "border-blue-500 text-zinc-900 bg-zinc-100/30"
              : "border-transparent text-zinc-600 hover:text-zinc-900"
          }`}
        >
          <Receipt className="w-3.5 h-3.5 inline mr-1" />
          Transactions
          {totalDocs > 0 && (
            <Badge className="ml-1.5 text-[9px] px-1.5 h-4 bg-blue-500/20 text-blue-700 border-0">
              {totalDocs}
            </Badge>
          )}
        </button>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto">
        {activeTab === "profile" ? (
        <div className="p-3 space-y-4">
          {/* Contact Card */}
          <div className="bg-zinc-100/50 rounded-xl p-3 border border-zinc-300">
            <div className="flex flex-col items-center text-center">
              {conversation.contactAvatar ? (
                <img
                  src={conversation.contactAvatar}
                  alt={conversation.contactName}
                  className="w-20 h-20 rounded-full object-cover ring-4 ring-zinc-300 mb-3"
                />
              ) : (
                <div className="w-20 h-20 rounded-full bg-gradient-to-br from-blue-500 to-purple-600 ring-4 ring-zinc-300 mb-3 flex items-center justify-center text-white text-xl font-medium">
                  {conversation.contactName
                    .split(" ")
                    .map((w) => w[0])
                    .join("")
                    .slice(0, 2)
                    .toUpperCase()}
                </div>
              )}
              <h3 className="font-semibold text-zinc-900 mb-1">
                {conversation.contactName}
              </h3>
              {contactInfo.company && (
                <p className="text-sm text-zinc-600 mb-3">
                  {contactInfo.company}
                </p>
              )}
              {contactInfo.erpEntity && (
                <Badge
                  className={`${
                    ENTITY_COLORS[contactInfo.erpEntity.type] ||
                    "bg-zinc-300/10 text-zinc-600 border-zinc-300/20"
                  } border`}
                >
                  <User className="w-3 h-3 mr-1" />
                  {contactInfo.erpEntity.type}: {contactInfo.erpEntity.id}
                </Badge>
              )}
            </div>
          </div>

          {/* Assigned Team Member */}
          {conversation.assignedTo && (
            <div className="space-y-3">
              <h4 className="text-sm font-medium text-zinc-900 flex items-center gap-2">
                <User className="w-4 h-4 text-green-700" />
                Assigned Team Member
              </h4>
              <div className="bg-zinc-100/30 rounded-lg p-3">
                <div className="flex items-center gap-3">
                  {conversation.assignedTo.avatar ? (
                    <img
                      src={conversation.assignedTo.avatar}
                      alt={conversation.assignedTo.name}
                      className="w-10 h-10 rounded-full object-cover"
                    />
                  ) : (
                    <div className="w-10 h-10 rounded-full bg-zinc-200 flex items-center justify-center text-zinc-900 text-sm font-medium">
                      {conversation.assignedTo.name[0]}
                    </div>
                  )}
                  <div>
                    <p className="text-sm font-medium text-zinc-900">
                      {conversation.assignedTo.name}
                    </p>
                    <p className="text-xs text-zinc-600">Account Manager</p>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Active Channels & Accounts */}
          <div className="space-y-3">
            <div>
              <h4 className="text-sm font-medium text-zinc-900 flex items-center gap-2">
                <MessageCircle className="w-4 h-4 text-blue-700" />
                Active Channels & Accounts
              </h4>
              <p className="text-[10px] text-zinc-600 mt-1 ml-6">
                Click on any account with access to switch conversation threads
              </p>
            </div>
            <div className="space-y-2">
              {Object.entries(accountsByChannel).map(([channel, accounts]) => (
                <div key={channel} className="bg-zinc-100/30 rounded-lg p-3">
                  <div className="flex items-center gap-2 mb-2">
                    {CHANNEL_ICONS[channel]}
                    <span className="text-sm font-medium text-zinc-900">
                      {CHANNEL_LABELS[channel] || channel}
                    </span>
                    <Badge className="ml-auto text-[9px] px-1.5 h-4 bg-blue-500/20 text-blue-700 border-0">
                      {accounts.length} account
                      {accounts.length > 1 ? "s" : ""}
                    </Badge>
                  </div>
                  <div className="space-y-1.5 pl-6">
                    {accounts.map((account) => {
                      const isActiveAccount =
                        account.id === conversation.activeAccount.id;
                      const canSwitch = account.hasAccess && !isActiveAccount;
                      return (
                        <button
                          key={account.id}
                          onClick={() =>
                            canSwitch && onAccountSwitch?.(account.id)
                          }
                          disabled={!canSwitch}
                          className={`group w-full flex items-center justify-between text-xs p-2 rounded transition-all ${
                            isActiveAccount
                              ? "bg-blue-500/10 border border-blue-500/30"
                              : canSwitch
                              ? "hover:bg-zinc-200/50 cursor-pointer"
                              : "opacity-50 cursor-not-allowed"
                          }`}
                        >
                          <div className="flex-1 min-w-0 text-left flex items-center gap-2">
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-1.5">
                                <p className="text-zinc-700 font-medium truncate">
                                  {account.name}
                                </p>
                                {isActiveAccount && (
                                  <Badge className="text-[8px] px-1 h-3.5 bg-blue-500 text-white border-0">
                                    ACTIVE
                                  </Badge>
                                )}
                              </div>
                              <p className="text-zinc-600 text-[10px] truncate">
                                {account.identifier}
                              </p>
                            </div>
                            {canSwitch && (
                              <ArrowRight className="w-3.5 h-3.5 text-blue-700 opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0" />
                            )}
                          </div>
                          <div className="flex items-center gap-1 ml-2">
                            {account.hasAccess ? (
                              <Badge className="text-[9px] px-1.5 h-4 bg-green-500/20 text-green-700 border-0">
                                <Shield className="w-2.5 h-2.5 mr-0.5" />
                                Access
                              </Badge>
                            ) : (
                              <Badge className="text-[9px] px-1.5 h-4 bg-orange-500/20 text-orange-700 border-0">
                                <ShieldAlert className="w-2.5 h-2.5 mr-0.5" />
                                No Access
                              </Badge>
                            )}
                          </div>
                        </button>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Contact Details */}
          <div className="space-y-3">
            <h4 className="text-sm font-medium text-zinc-900 flex items-center gap-2">
              <Mail className="w-4 h-4 text-blue-700" />
              Contact Information
            </h4>
            <div className="bg-zinc-100/30 rounded-lg p-3 space-y-3">
              <div className="flex items-start gap-3">
                <Mail className="w-4 h-4 text-zinc-600 mt-0.5 shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="text-xs text-zinc-600">Email</p>
                  <p className="text-sm text-zinc-700 truncate">
                    {contactInfo.email}
                  </p>
                </div>
              </div>
              <div className="flex items-center justify-between gap-3">
                <div className="flex items-start gap-3 flex-1 min-w-0">
                  <Phone className="w-4 h-4 text-zinc-600 mt-0.5 shrink-0" />
                  <div className="flex-1 min-w-0">
                    <p className="text-xs text-zinc-600">Phone</p>
                    <p className="text-sm text-zinc-700 truncate">{contactInfo.phone}</p>
                  </div>
                </div>
                {contactInfo.phone && (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => handleCall(contactInfo.phone)}
                    className="h-7 px-2.5 text-xs border-emerald-300 text-emerald-700 hover:bg-emerald-50 shrink-0"
                    title={"Call " + contactInfo.phone}
                  >
                    <Phone className="w-3 h-3 mr-1 text-emerald-600" />
                    Call
                  </Button>
                )}
              </div>
              {contactInfo.company && (
                <div className="flex items-start gap-3">
                  <Building2 className="w-4 h-4 text-zinc-600 mt-0.5 shrink-0" />
                  <div className="flex-1">
                    <p className="text-xs text-zinc-600">Company</p>
                    <p className="text-sm text-zinc-700">
                      {contactInfo.company}
                    </p>
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Linked ERP Entities */}
          <div className="space-y-3">
            <h4 className="text-sm font-medium text-zinc-900 flex items-center gap-2">
              <Link2 className="w-4 h-4 text-purple-700" />
              Linked ERP Entities
            </h4>
            {linkedLoading ? (
              <div className="bg-zinc-100/30 rounded-lg p-3 flex items-center justify-center gap-2">
                <Loader2 className="w-4 h-4 animate-spin text-zinc-600" />
                <span className="text-xs text-zinc-600">Loading links…</span>
              </div>
            ) : linkedEntities.length === 0 ? (
              <div className="bg-zinc-100/30 rounded-lg p-3 text-center">
                <p className="text-xs text-zinc-600">
                  No linked Lead, Customer, or Contact yet
                </p>
                <p className="text-[10px] text-zinc-500 mt-1">
                  Link from Omni Identity form
                </p>
              </div>
            ) : (
              <div className="space-y-2">
                {linkedEntities.map((entity) => (
                  <a
                    key={`${entity.linked_doctype}-${entity.linked_name}`}
                    href={getFormUrl(entity.linked_doctype, entity.linked_name)}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="group flex items-center gap-3 p-3 rounded-lg bg-zinc-100/30 border border-zinc-300 hover:border-blue-500/40 hover:bg-zinc-100/50 transition-all"
                  >
                    <div
                      className={`flex-1 min-w-0 ${
                        ENTITY_COLORS[entity.linked_doctype]
                          ? ""
                          : "bg-zinc-300/10 text-zinc-600 border-zinc-300/20"
                      }`}
                    >
                      <Badge
                        className={`text-[10px] px-2 h-5 border ${
                          ENTITY_COLORS[entity.linked_doctype] ||
                          "bg-zinc-300/10 text-zinc-600 border-zinc-300/20"
                        }`}
                      >
                        {entity.linked_doctype}
                      </Badge>
                      <p className="text-sm font-medium text-zinc-900 truncate mt-1.5">
                        {entity.title}
                      </p>
                      <p className="text-[10px] text-zinc-600 truncate">
                        {entity.linked_name}
                        {entity.role && entity.role !== "Unknown" && (
                          <span className="ml-1">• {entity.role}</span>
                        )}
                      </p>
                    </div>
                    <ExternalLink className="w-4 h-4 text-zinc-600 group-hover:text-blue-700 shrink-0 transition-colors" />
                  </a>
                ))}
              </div>
            )}
          </div>

          {/* Conversation Summary */}
          <div className="space-y-3">
            <h4 className="text-sm font-medium text-zinc-900 flex items-center gap-2">
              <Clock className="w-4 h-4 text-orange-700" />
              Conversation Summary
            </h4>
            <div className="bg-gradient-to-br from-zinc-100/50 to-zinc-50/50 rounded-lg p-3 border border-zinc-300">
              {statsLoading ? (
                <div className="flex items-center justify-center gap-2 py-2">
                  <Loader2 className="w-4 h-4 animate-spin text-zinc-600" />
                  <span className="text-xs text-zinc-600">Loading stats…</span>
                </div>
              ) : (
                <div className="space-y-3">
                  <div className="flex justify-between items-center">
                    <span className="text-xs text-zinc-600">
                      Total Messages
                    </span>
                    <span className="font-medium text-zinc-900">
                      {stats.total_messages}
                    </span>
                  </div>
                  <Separator className="bg-zinc-200" />
                  <div className="flex justify-between items-center">
                    <span className="text-xs text-zinc-600">
                      Inbound / Outbound
                    </span>
                    <span className="font-medium text-zinc-900">
                      {stats.inbound_count} / {stats.outbound_count}
                    </span>
                  </div>
                  <Separator className="bg-zinc-200" />
                  <div className="flex justify-between items-center">
                    <span className="text-xs text-zinc-600">
                      Team Replied
                    </span>
                    <Badge
                      className={`text-[10px] border ${
                        stats.erp_users_replied
                          ? "bg-green-500/10 text-green-700 border-green-500/20"
                          : "bg-red-500/10 text-red-700 border-red-500/20"
                      }`}
                    >
                      {stats.erp_users_replied ? "Yes" : "No"}
                    </Badge>
                  </div>
                  <Separator className="bg-zinc-200" />
                  <div className="flex justify-between items-center">
                    <span className="text-xs text-zinc-600">
                      Avg Response Time
                    </span>
                    <span className="font-medium text-zinc-900">
                      {formatResponseTime(stats.avg_response_time_seconds)}
                    </span>
                  </div>
                  <Separator className="bg-zinc-200" />
                  <div className="flex justify-between items-center">
                    <span className="text-xs text-zinc-600">Channels</span>
                    <div className="flex gap-1 flex-wrap justify-end">
                      {stats.channels.length > 0 ? (
                        stats.channels.map((ch) => (
                          <Badge
                            key={ch}
                            variant="outline"
                            className="border-zinc-300 text-zinc-700 text-[10px]"
                          >
                            {ch}
                          </Badge>
                        ))
                      ) : (
                        <Badge
                          variant="outline"
                          className="border-zinc-300 text-zinc-700"
                        >
                          {conversation.channel}
                        </Badge>
                      )}
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Quick Actions */}
          <div className="space-y-2">
            <h4 className="text-sm font-medium text-zinc-900 mb-3">
              Quick Actions
            </h4>
            <Button
              variant="outline"
              className="w-full justify-start border-zinc-300 hover:bg-zinc-100"
              onClick={() => {
                const entity = linkedEntities[0];
                if (entity) {
                  window.open(getFormUrl(entity.linked_doctype, entity.linked_name), "_blank");
                } else {
                  window.open(getFormUrl("Omni Identity", conversation.id), "_blank");
                }
              }}
            >
              <FileText className="w-4 h-4 mr-2" />
              View in ERPNext
            </Button>
            <Button
              variant="outline"
              className="w-full justify-start border-zinc-300 hover:bg-zinc-100"
              onClick={() => {
                const email = contactInfo.email;
                if (email) {
                  window.open(`mailto:${email}`, "_blank");
                }
              }}
              disabled={!contactInfo.email}
            >
              <Mail className="w-4 h-4 mr-2" />
              Send Email
            </Button>
            <Button
              variant="outline"
              className="w-full justify-start border-zinc-300 hover:bg-zinc-100"
              onClick={() => {
                const base = window.location.origin;
                const contact = linkedEntities.find((e) => e.linked_doctype === "Contact");
                const params = contact
                  ? `?party_type=Contact&party=${encodeURIComponent(contact.linked_name)}`
                  : "";
                window.open(`${base}/app/event/new${params}`, "_blank");
              }}
            >
              <Clock className="w-4 h-4 mr-2" />
              Schedule Meeting
            </Button>
          </div>
        </div>
        ) : (
        <div className="p-3 space-y-5">
          {docsLoading ? (
            <div className="flex items-center justify-center gap-2 py-12">
              <Loader2 className="w-5 h-5 animate-spin text-zinc-600" />
              <span className="text-sm text-zinc-600">Loading transactions...</span>
            </div>
          ) : !hasDocs ? (
            <div className="text-center py-12">
              <div className="w-12 h-12 rounded-full bg-zinc-100/50 flex items-center justify-center mx-auto mb-3">
                <Receipt className="w-6 h-6 text-zinc-500" />
              </div>
              <p className="text-sm text-zinc-600">No transactions found</p>
              <p className="text-xs text-zinc-500 mt-1">
                Link a Customer or Supplier to see documents
              </p>
            </div>
          ) : (
            docSections
              .filter((sec) => documents[sec.key].length > 0)
              .map((sec) => (
                <div key={sec.key} className="space-y-2">
                  <h4 className="text-xs font-medium text-zinc-900 flex items-center gap-2">
                    {sec.icon}
                    {sec.label}
                    <Badge className={`ml-auto text-[9px] px-1.5 h-4 border-0 ${badgeClasses[sec.color] || ""}`}>
                      {documents[sec.key].length}
                    </Badge>
                  </h4>
                  <div className="space-y-1.5">
                    {documents[sec.key].map((doc: ERPDocument) => (
                      <a
                        key={doc.name}
                        href={getFormUrl(sec.doctype, doc.name)}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="group block p-2.5 rounded-lg bg-zinc-100/30 border border-zinc-300 hover:border-blue-500/40 hover:bg-zinc-100/50 transition-all"
                      >
                        <div className="flex items-center justify-between mb-1">
                          <span className="text-[11px] font-medium text-zinc-900 truncate">{doc.name}</span>
                          <Badge className={`text-[9px] px-1.5 h-4 border ${
                            doc.status === "Completed" || doc.status === "Paid" || doc.status === "Delivered"
                              ? "bg-green-500/10 text-green-700 border-green-500/20"
                            : doc.status === "Overdue" || doc.status === "Cancelled"
                              ? "bg-red-500/10 text-red-700 border-red-500/20"
                            : doc.status === "Draft"
                              ? "bg-zinc-300/10 text-zinc-600 border-zinc-300/20"
                            : "bg-orange-500/10 text-orange-700 border-orange-500/20"
                          }`}>
                            {doc.status}
                          </Badge>
                        </div>
                        <div className="flex items-center justify-between text-[10px]">
                          <span className="text-zinc-600">{getDocDate(doc)}</span>
                          <span className="text-zinc-700 font-medium">
                            {doc.currency} {(doc.grand_total || 0).toLocaleString()}
                          </span>
                        </div>
                        {doc.outstanding_amount != null && doc.outstanding_amount > 0 && (
                          <div className="text-[10px] text-orange-700 text-right mt-0.5">
                            {doc.currency} {doc.outstanding_amount.toLocaleString()} due
                          </div>
                        )}
                        {getDocPartyName(doc) && (
                          <p className="text-[10px] text-zinc-600 mt-0.5 truncate">{getDocPartyName(doc)}</p>
                        )}
                      </a>
                    ))}
                  </div>
                </div>
              ))
          )}
        </div>
        )}
      </div>
    </div>
  );
}
