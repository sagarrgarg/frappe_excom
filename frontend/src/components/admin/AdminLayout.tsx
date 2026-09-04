import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useFrappePostCall } from "frappe-react-sdk";
import { useFrappeGetCall } from "@/lib/api";
import { Shield, Users, Radio, Tag, MessageSquare, Smile, FileText, Bell, Inbox, Settings, History, GitMerge, ListChecks, Cog, RefreshCw, LayoutGrid, AlertTriangle, UserCheck, Facebook, Stethoscope } from "lucide-react";
import { toast } from "sonner";
import { AdminPage } from "../shell/AdminPage";
import { Button, Select, Sheet, Chip } from "../primitives";
import { cn } from "../ui/utils";
import { hasRole } from "../../lib/ui-flag";
import { TeamsAdmin } from "./TeamsAdmin";
import { UsersAdmin } from "./UsersAdmin";
import { DocAdmin } from "./DocAdmin";
import { AuditAdmin } from "./AuditAdmin";
import { MetaConnectAdmin } from "./MetaConnectAdmin";
import { serverMessage } from "./util";

interface Section { id: string; label: string; icon: React.ReactNode; group: "People" | "Channels" | "Content" | "Automation" | "System" | "Lists"; render?: () => React.ReactNode; to?: string; hint?: string }

interface Diag { account: string; phone_id: string; waba_id: string; version: string; ok: boolean; templates_visible?: number; id_type?: string; suggested_wabas?: { id: string; name: string }[]; checks: { label: string; ok: boolean; detail: string }[] }

/** One click that says why a WhatsApp account cannot sync: token, WABA id, and what Meta answers. */
function DiagnoseWhatsApp() {
  const { call, loading } = useFrappePostCall("excom.excom.api.admin.diagnose_whatsapp");
  const { call: setWaba } = useFrappePostCall("excom.excom.api.admin.set_whatsapp_business_id");
  const [rows, setRows] = useState<Diag[] | null>(null);
  const rerun = async () => { try { const r = await call({}); setRows(r.message); } catch (e) { toast.error(serverMessage(e)); } };
  return (
    <>
      <Button size="sm" variant="ghost" disabled={loading} onClick={rerun}><Stethoscope className={loading ? "animate-spin" : ""} />Diagnose accounts</Button>
      <Sheet open={rows !== null} onOpenChange={(o) => !o && setRows(null)} title="WhatsApp account check" width="w-[560px]">
        <div className="p-3 space-y-3">
          {(rows ?? []).length === 0 && <p className="text-sm text-ink-3">No active WhatsApp accounts.</p>}
          {(rows ?? []).map((r) => (
            <section key={r.account} className="rounded-md border border-border min-w-0">
              <div className="flex items-center gap-2 px-2 h-9 border-b border-border bg-surface-sunken min-w-0">
                <span className="text-sm text-ink-1 truncate flex-1">{r.account}</span>
                <Chip size="sm" accent={r.ok ? "green" : "rose"} label={r.ok ? "Ready" : "Blocked"} />
              </div>
              <ul className="divide-y divide-border">
                {r.checks.map((c, i) => (
                  <li key={i} className="px-2 py-1.5 min-w-0">
                    <div className="flex items-center gap-2 min-w-0"><Chip size="sm" accent={c.ok ? "green" : "rose"} label={c.ok ? "OK" : "FAIL"} /><span className="text-sm text-ink-1 truncate">{c.label}</span></div>
                    {c.detail && <p className="text-xs text-ink-2 mt-0.5 break-words">{c.detail}</p>}
                  </li>
                ))}
              </ul>
              {(r.suggested_wabas ?? []).length > 0 && (
                <div className="px-2 py-1.5 border-t border-border flex items-center gap-2 flex-wrap">
                  <span className="text-xs text-ink-2">Use the real WhatsApp Business Account:</span>
                  {r.suggested_wabas!.map((w) => (
                    <Button key={w.id} size="sm" variant="primary" onClick={async () => { try { await setWaba({ account: r.account, waba_id: w.id }); toast.success(`${r.account} → WABA ${w.id}`); rerun(); } catch (e) { toast.error(serverMessage(e)); } }}>{w.name || w.id}</Button>
                  ))}
                </div>
              )}
              <p className="px-2 py-1.5 text-xs text-ink-3">phone id {r.phone_id || "—"} · WABA {r.waba_id || "—"}{r.id_type ? ` (${r.id_type.replace(/_/g, " ")})` : ""} · {r.version}{typeof r.templates_visible === "number" ? ` · ${r.templates_visible > 0 ? "templates visible" : "no templates on this WABA"}` : ""}</p>
            </section>
          ))}
        </div>
      </Sheet>
    </>
  );
}

function SyncTemplates() {
  const { call, loading } = useFrappePostCall("excom.excom.api.admin.sync_whatsapp_templates");
  return <Button size="sm" variant="default" disabled={loading} onClick={async () => { try { const r = await call({}); const m = r.message; if (m.errors?.length) { toast.error(`Sync problem: ${m.errors.join(" · ")}`, { duration: 12000 }); } else { toast.success(`Synced — ${m.count} templates (${m.new} new)`); } window.dispatchEvent(new Event("excom:templates-synced")); } catch (e) { toast.error(serverMessage(e)); } }}><RefreshCw className={loading ? "animate-spin" : ""} />Sync from Meta</Button>;
}

const SECTIONS: Section[] = [
  { id: "overview", label: "Overview", icon: <LayoutGrid />, group: "System" },
  { id: "teams", label: "Teams", icon: <Shield />, group: "People", render: () => <TeamsAdmin /> },
  { id: "users", label: "Users & roles", icon: <Users />, group: "People", render: () => <UsersAdmin /> },
  { id: "meta", label: "Meta Business", icon: <Facebook />, group: "Channels", render: () => <MetaConnectAdmin /> },
  { id: "accounts", label: "Channel accounts", icon: <Radio />, group: "Channels", render: () => <DocAdmin doctype="Excom Channel Account" hint="WhatsApp Cloud API, Gmail and web-chat accounts. Tokens are write-only here; leave a password field blank to keep it." /> },
  { id: "templates", label: "WhatsApp templates", icon: <FileText />, group: "Channels", render: () => <DocAdmin doctype="WhatsApp Templates" headerAction={<><DiagnoseWhatsApp /><SyncTemplates /></>} hint="Approved templates are pulled from Meta. Create or edit here to submit a new one." /> },
  { id: "intake", label: "Sources", icon: <Inbox />, group: "Channels", render: () => <DocAdmin doctype="Excom Source" hint="The one list of where leads come from. Integrations (Website, IndiaMART, TradeIndia, Meta) poll or receive; Exhibition / Manual are typed in; Channel rows are organic conversations. Each row mirrors itself into ERPNext's Lead Source so attribution never needs a second list." /> },
  { id: "canned", label: "Canned responses", icon: <MessageSquare />, group: "Content", render: () => <DocAdmin doctype="Excom Canned Response" hint="Type / in the composer to use them. Global ones are visible to every team." /> },
  { id: "tags", label: "Tags", icon: <Tag />, group: "Content", render: () => <DocAdmin doctype="Excom Tag" /> },
  { id: "stickers", label: "Stickers", icon: <Smile />, group: "Content", render: () => <DocAdmin doctype="Excom Sticker" hint="WebP stickers uploaded to the WhatsApp account's media library." /> },
  { id: "signatures", label: "Email signatures", icon: <FileText />, group: "Content", render: () => <DocAdmin doctype="Excom Email Signature" /> },
  { id: "notifications", label: "Notification rules", icon: <Bell />, group: "Automation", render: () => <DocAdmin doctype="Excom Notification" hint="Send a WhatsApp/email when a document event happens (e.g. Sales Invoice submitted)." /> },
  { id: "assignment", label: "Auto-assignment rules", icon: <UserCheck />, group: "Automation", render: () => <DocAdmin doctype="Assignment Rule" hint="ERPNext's own Assignment Rules, limited to Excom doctypes (threads, leads, opportunities, contacts…). Round-robin, load-balancing or by field, with conditions and working days." /> },
  { id: "subscribers", label: "Subscriber lists", icon: <ListChecks />, group: "Lists", to: "/subscribers" },
  { id: "rules", label: "Subscriber rules", icon: <Cog />, group: "Lists", to: "/rules" },
  { id: "merge", label: "Merge suggestions", icon: <GitMerge />, group: "Lists", to: "/merge" },
  { id: "settings", label: "Excom settings", icon: <Settings />, group: "System", render: () => <DocAdmin doctype="Excom Settings" /> },
  { id: "audit", label: "Admin audit", icon: <History />, group: "System", render: () => <AuditAdmin /> },
  { id: "transfers", label: "Transfer log", icon: <History />, group: "System", render: () => <DocAdmin doctype="Excom Thread Transfer Log" /> },
  { id: "stages", label: "Stage change log", icon: <History />, group: "System", render: () => <DocAdmin doctype="Excom Stage Change Log" /> },
  { id: "notif-log", label: "Notification log", icon: <History />, group: "System", render: () => <DocAdmin doctype="Excom Notification Log" /> },
];
const GROUPS: Section["group"][] = ["People", "Channels", "Content", "Automation", "Lists", "System"];

function Overview({ go }: { go: (id: string) => void }) {
  const { data } = useFrappeGetCall<{ message: Record<string, number> }>("excom.excom.api.admin.get_admin_overview", undefined, "admin-overview", { revalidateOnFocus: false });
  const m = data?.message ?? {};
  const cards: { id: string; label: string; value: number | undefined; warn?: boolean }[] = [
    { id: "teams", label: "Teams", value: m.teams }, { id: "users", label: "Excom users", value: m.users }, { id: "accounts", label: "Active accounts", value: m.accounts },
    { id: "templates", label: "WhatsApp templates", value: m.templates }, { id: "canned", label: "Canned responses", value: m.canned }, { id: "tags", label: "Tags", value: m.tags },
    { id: "stickers", label: "Stickers", value: m.stickers }, { id: "notifications", label: "Notification rules", value: m.notifications }, { id: "assignment", label: "Auto-assignment rules", value: m.assignment_rules }, { id: "intake", label: "Sources", value: m.intake_sources },
    { id: "users", label: "Chats owned by disabled users", value: m.disabled_owner_threads, warn: (m.disabled_owner_threads || 0) > 0 },
  ];
  return (
    <div className="p-3 grid grid-cols-2 tablet:grid-cols-3 laptop:grid-cols-5 gap-2">
      {cards.map((c, i) => (
        <button key={i} type="button" onClick={() => go(c.id)} className={cn("rounded-lg border border-border p-3 text-left hover:bg-surface-hover min-w-0", c.warn && "border-crayon-amber-text/40 bg-crayon-amber-tint")}>
          <div className="text-xs text-ink-3 truncate flex items-center gap-1">{c.warn && <AlertTriangle className="size-3.5 text-crayon-amber-text" />}{c.label}</div>
          <div className="text-lg text-ink-1 tabular-nums mt-1">{c.value ?? "…"}</div>
        </button>
      ))}
      <a href="/excom/inbox/unassigned" className="rounded-lg border border-border p-3 hover:bg-surface-hover min-w-0"><div className="text-xs text-ink-3">Unassigned chats</div><div className="text-lg text-ink-1 tabular-nums mt-1">{m.unassigned ?? "…"}</div></a>
    </div>
  );
}

/** /admin/:section — everything a manager used to do in Desk, in one place. Manager-only. */
export function AdminLayout() {
  const { section = "overview" } = useParams();
  const navigate = useNavigate();
  const go = (id: string) => { const s = SECTIONS.find((x) => x.id === id); if (s?.to) navigate(s.to); else navigate(`/admin/${id}`); };
  if (!hasRole("Excom Manager") && !hasRole("System Manager")) {
    return <AdminPage title="Admin" icon={<Shield />} embedded onBack={() => navigate("/inbox")}><p className="text-sm text-ink-2 p-3">You need the Excom Manager role to open this area.</p></AdminPage>;
  }
  const cur = SECTIONS.find((s) => s.id === section) || SECTIONS[0];
  return (
    <AdminPage title={`Admin · ${cur.label}`} icon={<Shield />} embedded bleed onBack={() => navigate("/inbox")} className="!bg-surface"
      toolbar={<div className="laptop:hidden"><Select value={cur.id} onChange={(e) => go(e.target.value)}>{GROUPS.map((g) => <optgroup key={g} label={g}>{SECTIONS.filter((s) => s.group === g).map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}</optgroup>)}</Select></div>}>
      <div className="flex h-full min-h-0">
        <nav className="hidden laptop:flex flex-col w-56 shrink-0 border-r border-border overflow-y-auto py-2" aria-label="Admin sections">
          {GROUPS.map((g) => (
            <div key={g} className="mb-2">
              <div className="px-3 py-1 text-2xs font-medium uppercase tracking-wide text-ink-3">{g}</div>
              {SECTIONS.filter((s) => s.group === g).map((s) => (
                <button key={s.id} type="button" onClick={() => go(s.id)} className={cn("w-full flex items-center gap-2 px-3 h-8 text-sm text-left hover:bg-surface-hover [&>svg]:size-4 [&>svg]:text-ink-3", cur.id === s.id ? "bg-surface-active text-ink-1 font-medium" : "text-ink-2")}>{s.icon}<span className="truncate">{s.label}</span>{s.to && <span className="ml-auto text-2xs text-ink-muted">↗</span>}</button>
              ))}
            </div>
          ))}
        </nav>
        <div className="flex-1 min-w-0 min-h-0 flex flex-col">
          {cur.id === "overview" ? <Overview go={go} /> : cur.render?.()}
        </div>
      </div>
    </AdminPage>
  );
}
