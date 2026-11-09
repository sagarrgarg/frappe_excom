import { useCallback, useEffect, useRef, useState } from "react";
import { useFrappeFileUpload, useFrappePostCall } from "frappe-react-sdk";
import { FileSpreadsheet, UserPlus } from "lucide-react";
import { toast } from "sonner";
import { useFrappeGetCall } from "@/lib/api";
import { Button, Chip, EmptyState, type Accent } from "../primitives";
import { toastError } from "../ErrorDialog";
import { parseError } from "../../lib/errors";
import type { AgentDetail, AIPerson, ResearchSummary } from "../../types/agents";
import { M, Panel, Stat, useAgentAction } from "./bits";

/** Excom AI Company.status, in the order research moves through. */
const COMPANY_ORDER = ["New", "Profiled", "Contacts found", "No contacts", "Qualified", "Promoted", "Excluded"];
/** Excom AI Person.email_status. */
const EMAIL_ORDER = ["Verified", "Risky", "Guess", "Unknown", "Invalid"];
const EMAIL_ACCENT: Record<string, Accent> = { Verified: "green", Risky: "amber", Guess: "neutral", Unknown: "neutral", Invalid: "rose" };
const EMAIL_HINT: Record<string, string> = {
  Verified: "The mailbox exists",
  Risky: "The domain accepts any address, so it cannot be confirmed",
  Guess: "Built from the company's usual pattern, not checked",
  Unknown: "Not checked yet",
  Invalid: "Mail would bounce",
};

function ordered(counts: Record<string, number>, order: string[]): [string, number][] {
  const known = order.filter((k) => counts[k]).map((k) => [k, counts[k]] as [string, number]);
  const other = Object.entries(counts).filter(([k, n]) => n && !order.includes(k)).map(([k, n]) => [k || "Not set", n] as [string, number]);
  return [...known, ...other];
}

/** What research has found for this agent: companies, people, a Volza import, and people ready to become leads. */
export function ResearchTab({ d }: { d: AgentDetail }) {
  const name = d.agent.name;
  const { data, error, mutate } = useFrappeGetCall<{ message: ResearchSummary }>(M.researchSummary, { name }, `research-${name}`, { revalidateOnFocus: false });
  const s = data?.message;
  const companies = s ? ordered(s.companies || {}, COMPANY_ORDER) : [];
  const people = s ? ordered(s.people || {}, EMAIL_ORDER) : [];
  const peopleTotal = people.reduce((n, [, c]) => n + c, 0);
  return (
    <div className="space-y-2">
      {error && <p role="alert" className="text-sm text-crayon-rose-text">{parseError(error).message}</p>}
      <div className="grid grid-cols-1 laptop:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-2 items-start">
        <Panel title={s ? `Companies found: ${s.total}` : "Companies found"}>
          {!s ? <p className="text-sm text-ink-3">Loading…</p> : companies.length === 0 ? <p className="text-sm text-ink-3">None yet.</p> : (
            <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)] gap-2">
              {companies.map(([k, n]) => <Stat key={k} label={k} value={n} tone={k === "Excluded" ? "rose" : undefined} />)}
            </div>
          )}
          {s && companies.some(([k]) => k === "Excluded") && <p className="text-xs text-ink-3 mt-2">Excluded: existing customers, open deals, do-not-contact and the like. They are never written to.</p>}
        </Panel>
        <Panel title={s ? `People found: ${peopleTotal}` : "People found"}>
          {!s ? <p className="text-sm text-ink-3">Loading…</p> : people.length === 0 ? <p className="text-sm text-ink-3">None yet.</p> : (
            <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)] gap-2">
              {people.map(([k, n]) => <Stat key={k} label={k} value={n} hint={EMAIL_HINT[k]} tone={k === "Invalid" ? "rose" : k === "Risky" ? "amber" : undefined} />)}
            </div>
          )}
        </Panel>
      </div>
      {d.can_change && <VolzaImport agent={name} onQueued={() => mutate()} />}
      <ReadyPeople agent={name} canPromote={d.can_change} onPromoted={() => mutate()} />
    </div>
  );
}

function VolzaImport({ agent, onQueued }: { agent: string; onQueued: () => void }) {
  const input = useRef<HTMLInputElement>(null);
  const { upload, loading: uploading, progress } = useFrappeFileUpload();
  const importVolza = useAgentAction(M.importVolza);
  const [file, setFile] = useState<File | null>(null);
  const go = async () => {
    if (!file) return;
    let url = "";
    try { url = (await upload(file, { isPrivate: true })).file_url; } catch (e) { toastError(e, "Could not upload the file"); return; }
    const r = await importVolza.run({ name: agent, file_url: url }, "Import queued: the companies appear here as they are read. See Activity.", "Could not start the import");
    if (r) { setFile(null); if (input.current) input.current.value = ""; onQueued(); }
  };
  const busy = uploading || importVolza.loading;
  return (
    <Panel title="Import from Volza">
      <p className="text-sm text-ink-2">Export importers from Volza as CSV or Excel and upload the file. The agent reads it, leaves out existing customers and do-not-contact companies, and researches the rest.</p>
      <div className="flex items-center gap-2 flex-wrap mt-3 min-w-0">
        <input ref={input} id={`volza-${agent}`} type="file" accept=".csv,.xlsx,.xls,text/csv,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" className="sr-only"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        <Button size="sm" onClick={() => input.current?.click()} disabled={busy}><FileSpreadsheet />{file ? "Choose another file" : "Choose a file"}</Button>
        {file && <span className="text-sm text-ink-1 truncate min-w-0 max-w-full">{file.name}</span>}
        <Button size="sm" variant="primary" disabled={!file || busy} onClick={go}>
          {uploading ? `Uploading… ${Math.round(progress || 0)}%` : importVolza.loading ? "Starting…" : "Import"}
        </Button>
      </div>
    </Panel>
  );
}

/**
 * People research marked Ready, for this agent (excom_ai.api.research_people: owners cannot read
 * research records directly).
 */
function ReadyPeople({ agent, canPromote, onPromoted }: { agent: string; canPromote: boolean; onPromoted: () => void }) {
  const { call } = useFrappePostCall("excom.excom_ai.api.research_people");
  const promote = useAgentAction(M.promotePerson);
  const [rows, setRows] = useState<AIPerson[] | null>(null);
  const [companyNames, setCompanyNames] = useState<Record<string, string>>({});
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    setErr("");
    try {
      const p = await call({ name: agent, status: "Ready", limit: 200 });
      const people = (p?.message ?? []) as (AIPerson & { company_name?: string })[];
      setCompanyNames(Object.fromEntries(people.map((x) => [x.company, x.company_name || x.company])));
      setRows(people);
    } catch (e) {
      setErr(parseError(e).message || "Could not load the people");
      setRows([]);
    }
  }, [call, agent]);
  useEffect(() => { load(); }, [load]);

  const doPromote = async (p: AIPerson) => {
    setBusy(p.name);
    const r = await promote.run({ name: agent, person: p.name }, "", "Could not make a lead");
    setBusy(null);
    if (!r) return;
    const out = r as { lead?: string; created?: boolean };
    toast.success(out.created === false ? `Already a lead${out.lead ? `: ${out.lead}` : ""}` : `Lead created${out.lead ? `: ${out.lead}` : ""}`);
    setRows((rs) => (rs ?? []).filter((x) => x.name !== p.name));
    onPromoted();
  };

  return (
    <Panel title={rows ? `Ready to become leads: ${rows.length}${rows.length === 200 ? "+" : ""}` : "Ready to become leads"}>
      {err ? (
        <p role="alert" className="text-sm text-crayon-rose-text">{err}</p>
      ) : !rows ? (
        <p className="text-sm text-ink-3">Loading…</p>
      ) : rows.length === 0 ? (
        <EmptyState compact icon={<UserPlus />} title="Nobody is ready yet" hint="People whose address has been found and checked appear here." />
      ) : (
        <ul className="-mx-3 -mb-3 divide-y divide-border">
          {rows.map((p) => (
            <li key={p.name} className="flex items-center gap-2 px-3 py-2 min-w-0 flex-wrap">
              <div className="min-w-0 flex-1 basis-48">
                <p className="text-sm text-ink-1 truncate">{p.full_name || p.email}</p>
                <p className="text-xs text-ink-3 truncate">{p.email}{p.company ? ` · ${companyNames[p.company] || p.company}` : ""}</p>
              </div>
              {p.email_status && <Chip size="sm" accent={EMAIL_ACCENT[p.email_status] ?? "neutral"} label={p.email_status} title={EMAIL_HINT[p.email_status]} />}
              {canPromote && <Button size="sm" disabled={busy === p.name} onClick={() => doPromote(p)}><UserPlus />{busy === p.name ? "Working…" : "Promote to lead"}</Button>}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
