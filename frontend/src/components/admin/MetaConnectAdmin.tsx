import { useState } from "react";
import { useFrappePostCall } from "frappe-react-sdk";
import { useFrappeGetCall } from "@/lib/api";
import { Loader2, RefreshCw, Plus, ShieldCheck, ShieldAlert, ExternalLink, Copy, Facebook, Instagram, FileText, MessageCircle } from "lucide-react";
import { toast } from "sonner";
import { Button, Chip, EmptyState, Sheet, Input, Field } from "../primitives";
import { DocForm, useSchema } from "./DocAdmin";
import { deskUrl } from "../../hooks/useRecordLinks";
import { serverMessage } from "./util";
import { cn } from "../ui/utils";

interface Asset { asset_type: "Page" | "Instagram" | "Lead Form" | "WhatsApp Number"; asset_id: string; asset_name?: string; page_id?: string; enabled: number; linked_doctype?: string; linked_name?: string; extra: Record<string, unknown> }
interface Conn { name: string; business_id?: string; company?: string; status: string; app_id?: string; api_version?: string; last_synced_at?: string; token_valid?: number; token_scopes?: string; webhook_verify_token?: string; has_token: boolean; has_secret: boolean; assets: Asset[] }

const ICON: Record<Asset["asset_type"], React.ReactNode> = { Page: <Facebook />, Instagram: <Instagram />, "Lead Form": <FileText />, "WhatsApp Number": <MessageCircle /> };
const WHAT: Record<Asset["asset_type"], string> = { Page: "Messenger inbox for this page", Instagram: "Instagram DMs", "Lead Form": "Lead-ad form → Intake source", "WhatsApp Number": "WhatsApp channel account" };

/** One Meta Business connection → discover pages / Instagram / lead forms / WhatsApp numbers → enable each. */
export function MetaConnectAdmin() {
  const { data, isLoading, mutate } = useFrappeGetCall<{ message: Conn[] }>("excom.excom.api.meta.get_connections", undefined, "admin-meta", { revalidateOnFocus: false });
  const { data: hook } = useFrappeGetCall<{ message: { url: string } }>("excom.excom.api.meta.webhook_url", undefined, "admin-meta-hook", { revalidateOnFocus: false });
  const { data: urls } = useFrappeGetCall<{ message: Record<string, string> }>("excom.excom.api.meta.app_urls", undefined, "admin-meta-urls", { revalidateOnFocus: false });
  const { data: sch } = useSchema("Excom Meta Connection");
  const { call: discover, loading: discovering } = useFrappePostCall("excom.excom.api.meta.discover");
  const { call: enable } = useFrappePostCall("excom.excom.api.meta.enable_asset");
  const { call: debug } = useFrappePostCall("excom.excom.api.meta.debug_token");
  const { call: exchange, loading: exchanging } = useFrappePostCall("excom.excom.api.meta.exchange_token");
  const [edit, setEdit] = useState<string | null>(null);
  const [short, setShort] = useState("");
  const conns = data?.message ?? [];
  const wrap = async (fn: () => Promise<unknown>, ok: string) => { try { await fn(); toast.success(ok); mutate(); } catch (e) { toast.error(serverMessage(e)); } };
  if (isLoading && !data) return <div className="flex justify-center py-8 text-ink-3"><Loader2 className="size-5 animate-spin" /></div>;
  return (
    <div className="flex flex-col min-h-0 h-full">
      <div className="shrink-0 flex items-center gap-2 px-3 py-2 border-b border-border flex-wrap">
        <span className="text-xs text-ink-3 flex-1 min-w-0 truncate">Webhook URL for the Meta app: <code className="text-ink-1">{hook?.message?.url || "…"}</code></span>
        {hook?.message?.url && <Button size="sm" variant="ghost" onClick={() => navigator.clipboard?.writeText(hook.message.url).then(() => toast.success("Copied"))}><Copy />Copy</Button>}
        <Button size="sm" variant="primary" onClick={() => setEdit("")}><Plus />New connection</Button>
      </div>
      <p className="px-3 py-1.5 text-xs text-ink-3 border-b border-border bg-surface-sunken">Create a Business Manager <b>system user</b>, assign it the pages, Instagram accounts and WhatsApp accounts, generate a token with the scopes listed on the form, paste it here, then <b>Discover</b>. Enabling an asset creates or updates the matching channel account / intake source and subscribes the page to the webhook (polling works even if that fails).</p>
      <div className="flex-1 min-h-0 overflow-y-auto p-3 space-y-4">
        {urls?.message && (
          <section className="rounded-lg border border-border p-3 min-w-0">
            <h4 className="text-xs font-medium text-ink-3 uppercase tracking-wide mb-2">Values for developers.facebook.com → your app</h4>
            <dl className="grid gap-x-4 gap-y-1 text-xs [grid-template-columns:max-content_1fr]">
              {([["App domain (Settings → Basic)", urls.message.app_domain], ["Privacy Policy URL", urls.message.privacy_policy_url], ["Terms of Service URL", urls.message.terms_url], ["User data deletion → Data deletion instructions URL", urls.message.data_deletion_url], ["User data deletion → Data deletion callback URL", urls.message.data_deletion_callback_url], ["Webhooks → Callback URL (WhatsApp, Messenger, Instagram, Page leadgen)", urls.message.webhook_callback_url], ["Webhooks → Verify token", "the Webhook Verify Token on the connection below"]] as [string, string][]).map(([k, v]) => (
                <><dt key={k + "k"} className="text-ink-3">{k}</dt><dd key={k + "v"} className="min-w-0 flex items-center gap-1"><code className="text-ink-1 truncate">{v}</code>{v.startsWith("http") && <button type="button" className="text-ink-3 hover:text-ink-1 shrink-0" onClick={() => navigator.clipboard?.writeText(v).then(() => toast.success("Copied"))}><Copy className="size-3.5" /></button>}</dd></>
              ))}
            </dl>
          </section>
        )}
        {conns.length === 0 && <EmptyState icon={<Facebook />} title="No Meta connection yet" hint="One connection per Business Manager." action={<Button size="sm" variant="primary" onClick={() => setEdit("")}>New connection</Button>} />}
        {conns.map((c) => (
          <section key={c.name} className="rounded-lg border border-border min-w-0">
            <div className="flex items-center gap-2 px-3 h-11 border-b border-border bg-surface-sunken min-w-0 flex-wrap">
              <span className="text-sm font-medium text-ink-1 truncate">{c.name}</span>
              {c.business_id && <span className="text-xs text-ink-3">BM {c.business_id}</span>}
              <Chip size="sm" accent={c.status === "Active" ? "green" : "neutral"} label={c.status} />
              {c.has_token ? (c.token_valid ? <Chip size="sm" accent="green" icon={<ShieldCheck />} label="Token valid" /> : <Chip size="sm" accent="amber" icon={<ShieldAlert />} label="Token unchecked" title={c.token_scopes || ""} />) : <Chip size="sm" accent="rose" label="No token" />}
              {!c.has_secret && <Chip size="sm" accent="amber" label="No app secret — webhooks unsigned" />}
              <span className="flex-1" />
              {c.last_synced_at && <span className="text-xs text-ink-3 tabular-nums">discovered {c.last_synced_at.slice(0, 16)}</span>}
              <Button size="sm" variant="ghost" onClick={() => wrap(() => debug({ name: c.name }), "Token checked")}><ShieldCheck />Check token</Button>
              <Button size="sm" variant="default" disabled={discovering || !c.has_token} onClick={() => wrap(async () => { const r = await discover({ name: c.name }); toast.message(`Found ${r.message.total}: ${Object.entries(r.message.found).map(([k, v]) => `${v} ${k}`).join(", ")}`); }, "Discovery done")}><RefreshCw className={discovering ? "animate-spin" : ""} />Discover</Button>
              <Button size="sm" variant="ghost" onClick={() => setEdit(c.name)}>Edit</Button>
            </div>
            {c.assets.length === 0 ? <p className="px-3 py-3 text-xs text-ink-3">Nothing discovered yet.</p> : (
              <ul className="divide-y divide-border">
                {(["Page", "Instagram", "Lead Form", "WhatsApp Number"] as Asset["asset_type"][]).map((t) => c.assets.filter((a) => a.asset_type === t).map((a) => (
                  <li key={t + a.asset_id} className={cn("flex items-center gap-2 px-3 h-11 min-w-0", a.enabled && "bg-crayon-green-tint/40")}>
                    <span className="text-ink-3 [&>svg]:size-4 shrink-0">{ICON[t]}</span>
                    <div className="flex-1 min-w-0"><div className="text-sm text-ink-1 truncate">{a.asset_name || a.asset_id}</div><div className="text-xs text-ink-3 truncate">{t} · {a.asset_id}{a.page_id && t !== "Page" ? ` · page ${a.page_id}` : ""}{a.extra?.status ? ` · ${String(a.extra.status)}` : ""}{a.extra?.leads_count != null ? ` · ${String(a.extra.leads_count)} leads` : ""}</div></div>
                    {a.enabled && a.linked_doctype && a.linked_name && <a href={deskUrl(a.linked_doctype, a.linked_name)} target="_blank" rel="noreferrer" className="text-xs text-ink-2 hover:underline inline-flex items-center gap-1 truncate max-w-[40%]">{a.linked_name}<ExternalLink className="size-3.5" /></a>}
                    <span className="text-xs text-ink-3 hidden tablet:inline">{WHAT[t]}</span>
                    {!a.asset_id.startsWith("-") && <label className="inline-flex items-center gap-1.5 text-xs text-ink-1"><input type="checkbox" className="size-4" checked={Boolean(a.enabled)} onChange={(e) => wrap(() => enable({ name: c.name, asset_type: t, asset_id: a.asset_id, enable: e.target.checked ? 1 : 0 }), e.target.checked ? `${a.asset_name || t} enabled` : `${a.asset_name || t} disabled`)} />Enabled</label>}
                  </li>
                )))}
              </ul>
            )}
            {c.app_id && c.has_secret && (
              <div className="flex items-center gap-2 px-3 py-2 border-t border-border flex-wrap">
                <Field label="Have only a short-lived user token? Exchange it" className="flex-1 min-w-[240px]"><Input value={short} onChange={(e) => setShort(e.target.value)} placeholder="EAAB…" /></Field>
                <Button size="sm" variant="default" disabled={!short || exchanging} onClick={() => wrap(async () => { await exchange({ name: c.name, short_lived_token: short }); setShort(""); }, "Long-lived token stored")}>Exchange</Button>
              </div>
            )}
          </section>
        ))}
      </div>
      <Sheet open={edit !== null} onOpenChange={(o) => !o && setEdit(null)} title={edit ? "Meta connection" : "New Meta connection"} width="w-[560px]">
        {edit !== null && sch?.message && <DocForm doctype="Excom Meta Connection" name={edit} schema={sch.message} onSaved={() => { mutate(); }} onDeleted={() => { mutate(); setEdit(null); }} />}
      </Sheet>
    </div>
  );
}
