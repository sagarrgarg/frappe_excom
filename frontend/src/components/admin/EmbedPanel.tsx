import { useState } from "react";
import { useFrappeGetCall, useFrappePostCall } from "frappe-react-sdk";
import { Copy, RefreshCw, Code2, Globe } from "lucide-react";
import { toast } from "sonner";
import { Button, Chip } from "../primitives";
import { serverMessage } from "./util";
import { cn } from "../ui/utils";

interface Embed { kind: "webchat" | "website" | "none"; site?: string; script?: string; notes?: string; token?: string; has_token?: boolean; form_endpoint?: string; webhook_endpoint?: string; html?: string; js?: string; curl?: string; origins?: string; ips?: string }

function Code({ text, label }: { text: string; label: string }) {
  return (
    <div className="relative min-w-0">
      <pre className="text-xs font-mono bg-surface-sunken border border-border rounded-md p-2 overflow-x-auto whitespace-pre max-h-64">{text}</pre>
      <Button size="sm" variant="ghost" className="absolute top-1 right-1" onClick={() => navigator.clipboard?.writeText(text).then(() => toast.success(`${label} copied`))}><Copy />Copy</Button>
    </div>
  );
}

/** Copy-paste code for a saved web chat account or Website intake source. Rendered under the form in the admin drawer. */
export function EmbedPanel({ doctype, name, channel, sourceType }: { doctype: string; name: string; channel?: string; sourceType?: string }) {
  const relevant = (doctype === "Excom Channel Account" && channel === "webchat") || (doctype === "Excom Source" && sourceType === "Website");
  const { data, mutate } = useFrappeGetCall<{ message: Embed }>(relevant && name ? "excom.excom.api.admin.get_embed" : (null as unknown as string), { doctype, name }, `embed-${doctype}-${name}`, { revalidateOnFocus: false });
  const { call: regen, loading } = useFrappePostCall("excom.excom.api.admin.regenerate_source_token");
  const [tab, setTab] = useState<"html" | "js" | "curl">("html");
  if (!relevant) return null;
  if (!name) return <p className="text-xs text-ink-3 rounded-md border border-dashed border-border p-2">Save first — the embed code needs the record name.</p>;
  const e = data?.message;
  if (!e || e.kind === "none") return null;
  if (e.kind === "webchat") {
    return (
      <section className="min-w-0 space-y-2">
        <h4 className="text-xs font-medium text-ink-3 uppercase tracking-wide flex items-center gap-1"><Globe className="size-3.5" />Install on your website</h4>
        <Code text={e.script || ""} label="Script tag" />
        <p className="text-xs text-ink-3">{e.notes}</p>
      </section>
    );
  }
  return (
    <section className="min-w-0 space-y-2">
      <h4 className="text-xs font-medium text-ink-3 uppercase tracking-wide flex items-center gap-1"><Code2 className="size-3.5" />Connect your website form</h4>
      <div className="flex items-center gap-2 flex-wrap text-xs">
        {e.has_token ? <Chip size="sm" accent="green" label="Token set" /> : <Chip size="sm" accent="amber" label="No token yet — generate one" />}
        <Button size="sm" variant="default" disabled={loading} onClick={async () => { if (e.has_token && !window.confirm("Generate a new token? The current one stops working immediately and every form using it must be updated.")) return; try { await regen({ name }); toast.success("Token generated"); mutate(); } catch (x) { toast.error(serverMessage(x)); } }}><RefreshCw />{e.has_token ? "Regenerate token" : "Generate token"}</Button>
        {e.has_token && <Button size="sm" variant="ghost" onClick={() => navigator.clipboard?.writeText(e.token || "").then(() => toast.success("Token copied"))}><Copy />Copy token</Button>}
      </div>
      <div className="text-xs text-ink-2 space-y-0.5">
        <div><span className="text-ink-3">Form endpoint (browser): </span><code className="text-ink-1">{e.form_endpoint}</code></div>
        <div><span className="text-ink-3">Webhook (servers / form builders): </span><code className="text-ink-1 break-all">{e.webhook_endpoint}</code></div>
        <div><span className="text-ink-3">Allowed origins: </span>{e.origins ? e.origins.split("\n").join(", ") : <span className="text-crayon-amber-text">none — add your site origin above for browser forms</span>}</div>
      </div>
      <div className="flex gap-1">{(["html", "js", "curl"] as const).map((t) => <button key={t} type="button" onClick={() => setTab(t)} className={cn("rounded-full border px-2 h-6 text-xs", tab === t ? "bg-surface-active border-border text-ink-1" : "border-border text-ink-2")}>{t === "html" ? "HTML form" : t === "js" ? "JavaScript" : "Webhook / curl"}</button>)}</div>
      <Code text={(tab === "html" ? e.html : tab === "js" ? e.js : e.curl) || ""} label={tab === "html" ? "HTML" : tab === "js" ? "JavaScript" : "curl"} />
      <p className="text-xs text-ink-3">{e.notes}</p>
    </section>
  );
}
