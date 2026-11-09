import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, BadgeCheck, Check, CornerDownLeft, Loader2, Save, Sparkles, X } from "lucide-react";
import { Button, Chip, Input, Textarea } from "../primitives";
import { cn } from "../ui/utils";
import { BRIEF_FIELDS, type AgentDetail, type BriefDraft, type BriefField } from "../../types/agents";
import { M, Panel, useAgentAction, when } from "./bits";

const LABEL: Record<BriefField, { label: string; rows?: number; hint?: string }> = {
  agent_name: { label: "Persona name", hint: "The name it signs with" },
  title: { label: "Title" },
  what_it_is: { label: "What this agent is", rows: 4 },
  mission: { label: "Mission", rows: 2 },
  audience: { label: "Who it writes to", rows: 3 },
  tone: { label: "Tone", rows: 2 },
  tone_examples: { label: "Sample opening lines", rows: 3 },
  rules_do: { label: "It always", rows: 4 },
  rules_dont: { label: "It never", rows: 4 },
};
const NOTE_LABEL: Record<string, string> = { price: "Price basis" };

const needsCheck = (src?: string) => Boolean(src && /AI suggestion/i.test(src));

/** Where a line of the brief comes from; an AI guess is flagged for a person to check. */
function SourceChip({ source }: { source?: string }) {
  if (!source) return null;
  if (needsCheck(source)) {
    const rest = source.replace(/;?\s*AI suggestion, please check/i, "").trim();
    return <Chip size="sm" accent="amber" icon={<AlertTriangle />} label={rest ? `Please check · ${rest}` : "AI suggestion: please check"} title={source} />;
  }
  return <Chip size="sm" accent="neutral" label={/^edited by /i.test(source) ? source.replace(/^edited/i, "Edited") : `From ${source}`} title={source} />;
}

/** Brief tab: the words the agent works from, each with its source; save, approve, redraft. */
export function BriefTab({ d, reload }: { d: AgentDetail; reload: () => void }) {
  const a = d.agent;
  const server = useMemo(() => Object.fromEntries(BRIEF_FIELDS.map((k) => [k, a[k] ?? ""])) as Record<BriefField, string>, [a]);
  const [values, setValues] = useState(server);
  const serverKey = JSON.stringify(server);
  useEffect(() => { setValues(server); /* a reload replaces what is shown */ }, [serverKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const changed = BRIEF_FIELDS.filter((k) => (values[k] ?? "") !== (server[k] ?? ""));
  const dirty = changed.length > 0;
  const editable = d.can_change && a.status !== "Retired";

  const save = useAgentAction(M.saveBrief);
  const approve = useAgentAction(M.approveBrief);
  const redraftCall = useAgentAction(M.redraft);
  const [draft, setDraft] = useState<BriefDraft | null>(null);

  const doSave = async () => {
    const warn = a.brief_approved_on
      ? `Saving clears the brief's approval${a.status === "Live" ? ", and the agent goes back to Shadow until you approve it again" : ""}. Save?`
      : "";
    if (warn && !window.confirm(warn)) return;
    const fields = Object.fromEntries(changed.map((k) => [k, values[k]]));
    if (await save.run({ name: a.name, fields: JSON.stringify(fields) }, "Brief saved", "Could not save the brief")) reload();
  };
  const notes = Object.entries(d.brief_sources).filter(([k]) => !(BRIEF_FIELDS as readonly string[]).includes(k));
  const toCheck = BRIEF_FIELDS.filter((k) => needsCheck(d.brief_sources[k])).length;

  return (
    <div className="space-y-2 min-w-0">
      <Panel>
        <div className="flex items-start gap-3 flex-wrap min-w-0">
          <div className="min-w-0 flex-1">
            {a.brief_approved_on ? (
              <p className="text-sm text-ink-1 flex items-center gap-1.5"><BadgeCheck className="size-4 text-crayon-green-text shrink-0" />Version {a.brief_version} approved by {a.brief_approved_by}, {when(a.brief_approved_on)}</p>
            ) : (
              <p className="text-sm text-crayon-amber-text flex items-center gap-1.5"><AlertTriangle className="size-4 shrink-0" />Not approved. The agent cannot go live until you approve its brief.</p>
            )}
            {toCheck > 0 && <p className="text-xs text-ink-3 mt-1">{toCheck} {toCheck === 1 ? "line was" : "lines were"} suggested by the AI with no record behind {toCheck === 1 ? "it" : "them"}: please check {toCheck === 1 ? "it" : "them"}.</p>}
          </div>
          {editable && (
            <div className="flex flex-wrap gap-1.5">
              <Button size="sm" variant="ghost" disabled={redraftCall.loading} onClick={async () => { const r = await redraftCall.run({ name: a.name }, "", "Could not redraft"); if (r) setDraft(r as BriefDraft); }}>
                {redraftCall.loading ? <Loader2 className="animate-spin" /> : <Sparkles />}{redraftCall.loading ? "Redrafting…" : "Redraft"}
              </Button>
              {dirty && <Button size="sm" variant="ghost" onClick={() => setValues(server)}><X />Discard changes</Button>}
              <Button size="sm" variant={dirty ? "primary" : "default"} disabled={!dirty || save.loading} onClick={doSave}><Save />{save.loading ? "Saving…" : "Save"}</Button>
              <Button size="sm" variant={!dirty && !a.brief_approved_on ? "primary" : "default"} disabled={dirty || approve.loading} title={dirty ? "Save your changes first" : undefined}
                onClick={async () => { if (await approve.run({ name: a.name }, "Brief approved", "Could not approve the brief")) reload(); }}><Check />Approve brief</Button>
            </div>
          )}
        </div>
        {editable && dirty && a.brief_approved_on && (
          <p className="text-xs text-crayon-amber-text mt-2" role="status">Saving clears the approval{a.status === "Live" ? " and puts the agent back in Shadow" : ""}; approve it again after.</p>
        )}
        {notes.length > 0 && (
          <ul className="mt-2 space-y-0.5">
            {notes.map(([k, v]) => <li key={k} className="text-xs text-ink-2 break-words"><span className="text-ink-3">{NOTE_LABEL[k] || k}:</span> {v}</li>)}
          </ul>
        )}
      </Panel>

      {draft && (
        <div className="flex items-center gap-2 rounded-md border border-crayon-violet-base/40 bg-crayon-violet-tint px-3 py-2 text-sm text-crayon-violet-text min-w-0 flex-wrap" role="status">
          <Sparkles className="size-4 shrink-0" />
          <span className="flex-1 min-w-0">A fresh draft from what the ERP shows today is beside each field. Nothing changes until you use a line and save.</span>
          <Button size="sm" variant="ghost" onClick={() => setDraft(null)}><X />Close the draft</Button>
        </div>
      )}

      <div className="space-y-2">
        {BRIEF_FIELDS.map((k) => {
          const meta = LABEL[k];
          const src = d.brief_sources[k];
          const suggestion = draft?.fields?.[k];
          const id = `brief-${k}`;
          const control = !editable
            ? <p id={id} className="text-sm text-ink-1 whitespace-pre-wrap break-words">{values[k] || <span className="text-ink-3">—</span>}</p>
            : meta.rows
              ? <Textarea id={id} rows={meta.rows} value={values[k]} onChange={(e) => setValues((v) => ({ ...v, [k]: e.target.value }))} />
              : <Input id={id} value={values[k]} onChange={(e) => setValues((v) => ({ ...v, [k]: e.target.value }))} />;
          return (
            <section key={k} className={cn("rounded-lg border bg-surface p-3 min-w-0 border-l-[3px]", needsCheck(src) ? "border-crayon-amber-base border-l-crayon-amber-base" : "border-border border-l-border")}>
              <div className="flex items-center gap-2 flex-wrap mb-1.5 min-w-0">
                <label htmlFor={id} className="text-sm font-medium text-ink-1">{meta.label}</label>
                {changed.includes(k) && <Chip size="sm" accent="blue" label="Changed" />}
                <span className="ml-auto min-w-0 max-w-full"><SourceChip source={src} /></span>
              </div>
              {draft ? (
                <div className="grid grid-cols-1 laptop:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-2">
                  <div className="min-w-0">{control}</div>
                  <div className="min-w-0 rounded-md bg-surface-sunken p-2">
                    <div className="flex items-center gap-2 mb-1 flex-wrap">
                      <span className="text-xs text-ink-3">Fresh draft</span>
                      {draft.sources?.[k] && <SourceChip source={draft.sources[k]} />}
                      {editable && suggestion !== undefined && suggestion !== values[k] && (
                        <Button size="sm" variant="ghost" className="ml-auto" onClick={() => setValues((v) => ({ ...v, [k]: suggestion }))}><CornerDownLeft />Use this</Button>
                      )}
                    </div>
                    <p className="text-sm text-ink-1 whitespace-pre-wrap break-words select-text">{suggestion || <span className="text-ink-3">No suggestion.</span>}</p>
                  </div>
                </div>
              ) : control}
              {meta.hint && <p className="text-xs text-ink-3 mt-1">{meta.hint}</p>}
            </section>
          );
        })}
      </div>
    </div>
  );
}
