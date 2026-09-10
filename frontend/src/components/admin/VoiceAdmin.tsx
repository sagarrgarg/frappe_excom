import { useState } from "react";
import { useFrappePostCall } from "frappe-react-sdk";
import { useFrappeGetCall } from "@/lib/api";
import { Copy, Check, RefreshCw, Phone, AlertTriangle, UserPlus } from "lucide-react";
import { toast } from "sonner";
import { Button, Chip, EmptyState, Select } from "../primitives";
import { serverMessage } from "./util";

/**
 * Voice line setup.
 *
 * Two jobs an administrator cannot do anywhere else: copy the four webhook URLs into the provider
 * console, and give the desk's agents a softphone each. Everything else about the line — number,
 * credentials, ring strategy, recording policy — is edited on the Channel Account itself, because
 * a second form over the same fields is a second place for them to disagree.
 */

interface EndpointRow {
  name: string;
  user: string;
  status: string;
  sip_uri: string;
  last_registered_at: string | null;
  registered_now: boolean;
  available: boolean;
}

interface LineStatus {
  account: string;
  provider: string;
  capabilities: string[];
  credentials_present: boolean;
  agents_on_line: number;
  endpoints: EndpointRow[];
  webhooks: Record<string, string>;
}

const WEBHOOK_LABELS: Record<string, string> = {
  route: "Answer URL",
  fallback: "Fallback URL",
  hangup: "Hangup URL",
  ringing: "Ring URL",
};

export function VoiceAdmin() {
  const [account, setAccount] = useState<string>("");

  const { data: linesData } = useFrappeGetCall<{ message: { name: string; account_name: string }[] }>(
    "frappe.client.get_list",
    {
      doctype: "Excom Channel Account",
      filters: JSON.stringify([["channel", "=", "voice"]]),
      fields: JSON.stringify(["name", "account_name"]),
      limit_page_length: 50,
    },
    "voice-lines",
    { revalidateOnFocus: false },
  );
  const lines = linesData?.message ?? [];
  const active = account || lines[0]?.name || "";

  const { data, isLoading, mutate } = useFrappeGetCall<{ message: LineStatus }>(
    active ? "excom.excom.api.voice.line_status" : null,
    active ? { account: active } : undefined,
    active ? `voice-status:${active}` : null,
    { revalidateOnFocus: false },
  );
  const status = data?.message;

  const { call: provision, loading: provisioning } = useFrappePostCall(
    "excom.excom.api.voice.provision_line",
  );
  const { call: testCreds, loading: testing } = useFrappePostCall(
    "excom.excom.api.voice.test_credentials",
  );

  if (!lines.length) {
    return (
      <div className="p-3">
        <EmptyState
          icon={<Phone />}
          title="No voice line yet"
          hint="Create an Excom Channel Account with channel 'voice' under Channel accounts, fill in the provider credentials, then come back here to wire up the webhooks."
        />
      </div>
    );
  }

  const runProvision = async () => {
    try {
      const r = await provision({ account: active });
      const m = r.message;
      const bits = [];
      if (m.created?.length) bits.push(`${m.created.length} softphone(s) created`);
      if (m.removed?.length) bits.push(`${m.removed.length} removed`);
      if (m.failed?.length) bits.push(`${m.failed.length} failed`);
      toast[m.failed?.length ? "warning" : "success"](bits.join(" · ") || "Nothing to change");
      if (m.failed?.length) {
        toast.error(m.failed.map((f: any) => `${f.user}: ${f.error}`).join(" · "), { duration: 12000 });
      }
      void mutate();
    } catch (e) {
      toast.error(serverMessage(e));
    }
  };

  const runTest = async () => {
    try {
      await testCreds({ account: active });
      toast.success("Credentials work — the provider answered.");
    } catch (e) {
      toast.error(serverMessage(e));
    }
  };

  return (
    <div className="p-3 space-y-4 max-w-3xl">
      {lines.length > 1 && (
        <Select value={active} onChange={(e) => setAccount(e.target.value)} className="max-w-xs">
          {lines.map((l) => (
            <option key={l.name} value={l.name}>
              {l.account_name || l.name}
            </option>
          ))}
        </Select>
      )}

      {isLoading && <p className="text-sm text-ink-3">Loading…</p>}

      {status && (
        <>
          <section className="rounded-lg border border-border">
            <header className="flex items-center gap-2 px-3 h-9 border-b border-border bg-surface-sunken">
              <span className="text-sm text-ink-1 flex-1">Line</span>
              <Chip
                size="sm"
                accent={status.credentials_present ? "green" : "rose"}
                label={status.credentials_present ? "Credentials set" : "No credentials"}
              />
              <Button size="sm" variant="ghost" disabled={testing} onClick={runTest}>
                <RefreshCw className={testing ? "animate-spin" : ""} />
                Test
              </Button>
            </header>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1 p-3 text-sm">
              <dt className="text-ink-3">Provider</dt>
              <dd className="text-ink-1">{status.provider || "—"}</dd>
              <dt className="text-ink-3">Agents on this line</dt>
              <dd className="text-ink-1 tabular-nums">{status.agents_on_line}</dd>
              <dt className="text-ink-3">Softphones provisioned</dt>
              <dd className="text-ink-1 tabular-nums">
                {status.endpoints.filter((e) => e.status === "Active").length}
              </dd>
              <dt className="text-ink-3">Browser calling</dt>
              <dd className="text-ink-1">
                {status.capabilities.includes("webrtc") ? "Available" : "Off for this line"}
              </dd>
            </dl>
          </section>

          <section className="rounded-lg border border-border">
            <header className="flex items-center px-3 h-9 border-b border-border bg-surface-sunken">
              <span className="text-sm text-ink-1">Webhook URLs</span>
            </header>
            <p className="px-3 pt-2 text-xs text-ink-3">
              Paste these into the provider's Application. Requests are rejected unless they carry a
              valid signature, so these URLs are safe to copy around.
            </p>
            <ul className="divide-y divide-border">
              {Object.entries(status.webhooks).map(([kind, url]) => (
                <WebhookRow key={kind} label={WEBHOOK_LABELS[kind] || kind} url={url} />
              ))}
            </ul>
          </section>

          <section className="rounded-lg border border-border">
            <header className="flex items-center gap-2 px-3 h-9 border-b border-border bg-surface-sunken">
              <span className="text-sm text-ink-1 flex-1">Softphones</span>
              <Button size="sm" variant="primary" disabled={provisioning} onClick={runProvision}>
                <UserPlus className={provisioning ? "animate-spin" : ""} />
                Sync with teams
              </Button>
            </header>

            {status.agents_on_line > status.endpoints.filter((e) => e.status === "Active").length && (
              <p className="flex items-start gap-1.5 px-3 py-2 text-xs text-crayon-amber-text border-b border-border bg-crayon-amber-tint">
                <AlertTriangle className="size-3.5 mt-0.5 shrink-0" />
                Some agents on this line have no softphone yet. Until they do, their calls ring their
                mobile instead of the browser. Press "Sync with teams".
              </p>
            )}

            {status.endpoints.length === 0 ? (
              <p className="p-3 text-sm text-ink-3">
                Nobody has a softphone on this line yet.
              </p>
            ) : (
              <ul className="divide-y divide-border">
                {status.endpoints.map((e) => (
                  <li key={e.name} className="flex items-center gap-2 px-3 py-2 min-w-0">
                    <span className="text-sm text-ink-1 truncate flex-1">{e.user}</span>
                    <Chip
                      size="sm"
                      accent={e.registered_now ? "green" : "sand"}
                      label={e.registered_now ? "Connected" : "Offline"}
                    />
                    <Chip
                      size="sm"
                      accent={e.available ? "blue" : "sand"}
                      label={e.available ? "Taking calls" : "Not taking calls"}
                    />
                    {e.status !== "Active" && <Chip size="sm" accent="rose" label={e.status} />}
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </div>
  );
}

function WebhookRow({ label, url }: { label: string; url: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error("Could not copy. Select the text and copy it by hand.");
    }
  };
  return (
    <li className="flex items-center gap-2 px-3 py-2 min-w-0">
      <span className="text-sm text-ink-2 w-28 shrink-0">{label}</span>
      <code className="text-xs text-ink-1 truncate flex-1 min-w-0">{url}</code>
      <Button size="icon-sm" variant="ghost" onClick={copy} aria-label={`Copy ${label}`}>
        {copied ? <Check className="text-crayon-green-text" /> : <Copy />}
      </Button>
    </li>
  );
}
