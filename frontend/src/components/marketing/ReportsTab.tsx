import { useState } from "react";
import { Download, ExternalLink, Eye, Loader2, Plus, Send } from "lucide-react";
import { useFrappePostCall } from "frappe-react-sdk";
import { toast } from "sonner";
import { useFrappeGetCall } from "@/lib/api";
import { deskUrl } from "../../hooks/useRecordLinks";
import { Badge, Button, EmptyState, Modal, type Accent } from "../primitives";
import { serverMessage } from "../admin/util";
import { ago } from "./format";

/** Shapes returned by excom.excom_ads.reports. */
interface ReportTemplate {
  name: string;
  template_name: string;
  enabled: number;
  company: string;
  workspace: string | null;
  brand: string | null;
  provider: string | null;
  account: string | null;
  date_preset: string;
  schedule_enabled: number;
  frequency: string | null;
  send_time: string | null;
  weekday: string | null;
  day_of_month: number | null;
  via_email: number;
  via_whatsapp: number;
  last_period_key: string | null;
  last_sent_at: string | null;
  next_run: string | null;
}

interface ReportList {
  templates: ReportTemplate[];
  can_send: boolean;
  can_create: boolean;
  pdf_available: boolean;
  whatsapp_configured: boolean;
}

interface Preview {
  html: string;
  subject: string;
  from_date: string;
  to_date: string;
  disclosure: { ok: boolean; headline: string; lines: { level: string; text: string }[] };
}

interface Delivery {
  name: string;
  period_key: string;
  channel: "Email" | "WhatsApp";
  status: "Sending" | "Sent" | "Failed" | "Skipped";
  recipient_user: string | null;
  recipient_email: string | null;
  sent_at: string | null;
  attempts: number;
  error: string | null;
  creation: string;
}

const STATUS_ACCENT: Record<Delivery["status"], Accent> = { Sent: "green", Skipped: "neutral", Failed: "rose", Sending: "blue" };

function schedule(t: ReportTemplate): string {
  if (!t.schedule_enabled) return "Not scheduled";
  const at = (t.send_time ?? "").slice(0, 5);
  if (t.frequency === "Weekly") return `Weekly, ${t.weekday} ${at}`;
  if (t.frequency === "Monthly") return `Monthly, day ${t.day_of_month} ${at}`;
  return `Daily ${at}`;
}

function Deliveries({ template }: { template: string }) {
  const { data, isLoading } = useFrappeGetCall<{ message: Delivery[] }>("excom.excom_ads.reports.deliveries", { template, page_length: 20 }, `ads-report-deliveries-${template}`, { refreshInterval: 60_000 });
  const rows = data?.message ?? [];
  if (isLoading && !rows.length) return <div className="py-2 flex justify-center"><Loader2 className="size-4 animate-spin text-ink-3" /></div>;
  if (!rows.length) return <p className="text-xs text-ink-3 py-1">Not sent yet.</p>;
  return (
    <ul className="text-xs divide-y divide-border">
      {rows.map((d) => (
        <li key={d.name} className="py-1 flex items-center gap-2 min-w-0">
          <Badge accent={STATUS_ACCENT[d.status]}>{d.status}</Badge>
          <span className="text-ink-2 truncate">{d.recipient_user ?? d.recipient_email} · {d.channel}</span>
          <span className="text-ink-3 shrink-0">{d.period_key}</span>
          <span className="text-ink-3 truncate flex-1" title={d.error ?? undefined}>{d.error ?? ""}</span>
          <span className="text-ink-3 shrink-0">{ago(d.sent_at ?? d.creation)}</span>
        </li>
      ))}
    </ul>
  );
}

export function ReportsTab() {
  const { data, isLoading, error } = useFrappeGetCall<{ message: ReportList }>("excom.excom_ads.reports.list_templates", undefined, "ads-report-templates", { refreshInterval: 5 * 60_000 });
  const send = useFrappePostCall("excom.excom_ads.reports.send_now");
  const [previewName, setPreviewName] = useState<string | null>(null);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const pv = useFrappeGetCall<{ message: Preview }>(previewName ? "excom.excom_ads.reports.preview" : null, previewName ? { template: previewName } : undefined, previewName ? `ads-report-preview-${previewName}` : null, { revalidateOnFocus: false });
  const list = data?.message;

  const sendNow = async (name: string) => {
    try {
      await send.call({ template: name });
      toast.success("Queued: each recipient gets the report in their own scope");
    } catch (e) {
      toast.error(serverMessage(e) || "Could not queue the report");
    }
  };

  if (isLoading && !list) return <div className="flex justify-center py-16"><Loader2 className="size-5 animate-spin text-ink-3" /></div>;
  if (error || !list) return <EmptyState title="Could not load reports" hint={String((error as { message?: string })?.message ?? "")} />;
  const dl = (name: string, fmt: string) => `/api/method/excom.excom_ads.reports.download?template=${encodeURIComponent(name)}&fmt=${fmt}`;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-xs text-ink-3">
        <span>Reports render the same figures as these screens, inside each recipient's own scope, and always state data freshness.</span>
        <span>{list.pdf_available ? "PDF attached." : "Excel + CSV attached (no PDF engine on this server)."}</span>
        {!list.whatsapp_configured && <span>WhatsApp delivery needs a report template in Ads Settings.</span>}
        {list.can_create && (
          <a className="ml-auto" href={`${window.location.origin}/app/ads-report-template/new`} target="_blank" rel="noreferrer">
            <Button size="sm" variant="ghost"><Plus />New report</Button>
          </a>
        )}
      </div>
      {!list.templates.length ? (
        <EmptyState title="No reports yet" hint="Create an Ads Report Template: scope, period, sections and a schedule. Recipients each receive it inside their own scope." />
      ) : (
        <ul className="rounded-lg border border-border divide-y divide-border">
          {list.templates.map((t) => (
            <li key={t.name} className="p-3 space-y-1.5 min-w-0">
              <div className="flex flex-wrap items-center gap-2 min-w-0">
                <button className="text-sm text-ink-1 truncate hover:underline text-left" onClick={() => setOpen((o) => ({ ...o, [t.name]: !o[t.name] }))} aria-expanded={!!open[t.name]}>{t.template_name}</button>
                {!t.enabled && <Badge accent="neutral">Disabled</Badge>}
                <span className="text-xs text-ink-3 truncate">{[t.company, t.workspace, t.brand, t.provider].filter(Boolean).join(" · ")} · {t.date_preset}</span>
                <div className="ml-auto flex items-center gap-1 shrink-0">
                  <Button size="sm" variant="ghost" onClick={() => setPreviewName(t.name)}><Eye />Preview</Button>
                  {list.can_send && <a href={dl(t.name, "xlsx")}><Button size="sm" variant="ghost"><Download />Excel</Button></a>}
                  {list.can_send && <a href={dl(t.name, "csv")}><Button size="sm" variant="ghost"><Download />CSV</Button></a>}
                  {list.can_send && <Button size="sm" variant="ghost" onClick={() => sendNow(t.name)}><Send />Send now</Button>}
                  <a href={deskUrl("Ads Report Template", t.name)} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs text-ink-3 hover:text-ink-1 px-1.5"><ExternalLink className="size-3.5" />Edit</a>
                </div>
              </div>
              <p className="text-xs text-ink-3">
                {schedule(t)}{t.schedule_enabled ? ` · ${[t.via_email ? "email" : "", t.via_whatsapp ? "WhatsApp" : ""].filter(Boolean).join(" + ")}` : ""}
                {t.next_run ? ` · next ${t.next_run}` : ""}{t.last_sent_at ? ` · last sent ${ago(t.last_sent_at)}` : ""}
              </p>
              {open[t.name] && <Deliveries template={t.name} />}
            </li>
          ))}
        </ul>
      )}
      <Modal open={!!previewName} onOpenChange={(o) => !o && setPreviewName(null)} title={pv.data?.message.subject ?? "Preview"} description={pv.data ? `${pv.data.message.disclosure.headline} Rendered for you, inside your scope.` : undefined} width="max-w-5xl">
        {pv.error ? (
          <EmptyState compact title="Could not render the report" hint={serverMessage(pv.error)} />
        ) : !pv.data ? (
          <div className="flex justify-center py-12"><Loader2 className="size-5 animate-spin text-ink-3" /></div>
        ) : (
          // The server escapes every value; the frame is sandboxed anyway (no scripts, no same-origin).
          <iframe title="Report preview" sandbox="" srcDoc={pv.data.message.html} className="w-full h-[70vh] rounded border border-border bg-surface" />
        )}
      </Modal>
    </div>
  );
}
