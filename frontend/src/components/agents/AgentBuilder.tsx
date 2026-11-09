import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useFrappePostCall } from "frappe-react-sdk";
import { ArrowLeft, ArrowRight, Bot, Check, Loader2, Plus, Undo2 } from "lucide-react";
import { toast } from "sonner";
import { useFrappeGetCall } from "@/lib/api";
import { PageFrame } from "../shell/PageFrame";
import { Badge, Button, EmptyState, Field, Input, SegmentedControl, Textarea } from "../primitives";
import { LinkField } from "../crm/LinkField";
import { cn } from "../ui/utils";
import { toastError } from "../ErrorDialog";
import { parseError } from "../../lib/errors";
import { currentUserFullName } from "../../lib/ui-flag";
import { ABILITIES, DEFAULT_ABILITIES, abilitiesText, type Ability } from "../../lib/agents-logic";
import type { BuilderPreview, Excluded, KeywordRow } from "../../types/agents";
import { M, SectionPanel, TypeChip } from "./bits";
import { useAgentTypes } from "./AgentsHome";

type Step = "type" | "keywords" | "erp" | "people" | "create";
const STEPS: { value: Step; label: string }[] = [
  { value: "type", label: "1. Type" },
  { value: "keywords", label: "2. Keywords" },
  { value: "erp", label: "3. What the ERP shows" },
  { value: "people", label: "4. People" },
  { value: "create", label: "5. Create" },
];

/**
 * Countries a region keeps out because they do not speak the chosen language, and the keyword
 * that would bring each back. Mirrors builder/geo.py LANGUAGES for the countries a region can
 * leave out; anything else gets the general advice.
 */
const LANGUAGE_OF: Record<string, string> = {
  Brazil: "Portuguese", Portugal: "Portuguese", Angola: "Portuguese", Mozambique: "Portuguese",
  Belize: "English", Guyana: "English", Jamaica: "English", "Trinidad and Tobago": "English", "United States": "English",
  "United Kingdom": "English", Canada: "English", Australia: "English", "New Zealand": "English", Ireland: "English",
  Haiti: "French", France: "French", Belgium: "French", Suriname: "Dutch", Netherlands: "Dutch",
  Germany: "German", Austria: "German", Italy: "Italian", Israel: "Hebrew", Iran: "Persian", "Türkiye": "Turkish",
};

/** "Understood as: …" in words a sales manager reads at a glance. */
function understood(r: KeywordRow): string {
  const detail = (r.detail || "").replace(/^glossary: /, "confirmed earlier, ").replace(/^name contains: /, "name contains the word, ");
  switch (r.resolved_as) {
    case "Vocabulary": return [`${r.link_doctype} ${r.link_name}`, detail].filter(Boolean).join(" · ");
    case "Concept": return `Business idea · ${r.label}`;
    case "Region": return `Region · ${r.label} · ${r.countries.length} countries`;
    case "Language": return `Language · ${r.label} · spoken in ${r.countries.length} countries`;
    case "AI proposal": return `Maybe ${r.link_doctype} ${r.link_name}`;
    default: return "Not found in your ERP";
  }
}

/** Switch a keyword to the record with the same name, client-side, before the preview. */
function withAlternative(r: KeywordRow): KeywordRow {
  const a = r.alternative!;
  return {
    ...r, resolved_as: "Vocabulary", link_doctype: a.link_doctype, link_name: a.link_name, label: `${a.link_doctype}: ${a.link_name}`,
    detail: "your choice", proposal: null, concept: null, language: null, matches: 0,
    countries: a.link_doctype === "Country" ? [a.link_name] : [],
  };
}

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t); }, [value, ms]);
  return v;
}

/** /agents/new — the builder (HLD-007 §6.2), one step at a time so it works on a phone. */
export function AgentBuilder() {
  const navigate = useNavigate();
  const { types, canBuild, isLoading: typesLoading } = useAgentTypes();
  const [step, setStep] = useState<Step>("type");
  const [reached, setReached] = useState(0);
  const [agentType, setAgentType] = useState("");
  const [text, setText] = useState("");
  const [overrides, setOverrides] = useState<Record<string, { off?: boolean; alt?: boolean }>>({});
  const [excluded, setExcluded] = useState<Excluded>({});
  const [team, setTeam] = useState("");
  const [backup, setBackup] = useState("");
  const [persona, setPersona] = useState("");
  /* Custom agents only: what it should do (its playbook's process rules) and its abilities */
  const [description, setDescription] = useState("");
  const [abilities, setAbilities] = useState<Ability[]>(DEFAULT_ABILITIES);
  const isCustom = types.find((t) => t.name === agentType)?.pack === "custom";
  const toggleAbility = (a: Ability, on: boolean) => setAbilities((l) => (on ? [...l, a] : l.filter((x) => x !== a)));

  const go = (s: Step) => { const i = STEPS.findIndex((x) => x.value === s); setReached((r) => Math.max(r, i)); setStep(s); };
  const idx = STEPS.findIndex((x) => x.value === step);

  /* step 2: keywords → what they mean here, as the person types */
  const debounced = useDebounced(text.trim(), 500);
  const resolveKey = agentType && debounced ? `agent-resolve|${agentType}|${debounced}` : null;
  const { data: resolved, error: resolveError, isValidating: resolving, mutate: reResolve } = useFrappeGetCall<{ message: KeywordRow[] }>(
    resolveKey ? M.resolve : null, { agent_type: agentType, keywords: debounced }, resolveKey, { revalidateOnFocus: false, keepPreviousData: true, shouldRetryOnError: false });
  const rows: KeywordRow[] = useMemo(() => (resolveKey ? resolved?.message ?? [] : []).map((r) => {
    const o = overrides[r.keyword] || {};
    const base = o.alt && r.alternative ? withAlternative(r) : r;
    return { ...base, included: o.off ? 0 : 1 };
  }), [resolved, overrides, resolveKey]);
  const setOverride = (k: string, patch: { off?: boolean; alt?: boolean }) => setOverrides((o) => ({ ...o, [k]: { ...o[k], ...patch } }));

  /* step 3: what the ERP shows for those keywords */
  const { call: previewCall } = useFrappePostCall<{ message: BuilderPreview }>(M.preview);
  const [preview, setPreview] = useState<BuilderPreview | null>(null);
  const [previewError, setPreviewError] = useState("");
  const [previewing, setPreviewing] = useState(false);
  const rowsJson = JSON.stringify(rows);
  const excludedJson = JSON.stringify(excluded);
  const seq = useRef(0);
  useEffect(() => {
    if (step !== "erp" || !rows.length) return;
    const n = ++seq.current;
    const t = setTimeout(async () => {
      setPreviewing(true); setPreviewError("");
      try {
        const r = await previewCall({ agent_type: agentType, keywords: debounced, rows: rowsJson, excluded: excludedJson, team: team || undefined });
        if (n === seq.current) setPreview(r.message);
      } catch (e) {
        if (n === seq.current) setPreviewError(parseError(e).message);
      } finally {
        if (n === seq.current) setPreviewing(false);
      }
    }, 300);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, rowsJson, excludedJson, team, agentType]);

  const exclude = (doctype: string, name: string) => setExcluded((x) => ({ ...x, [doctype]: [...new Set([...(x[doctype] || []), name])] }));
  const putBack = (doctype: string, name: string) => setExcluded((x) => {
    const left = (x[doctype] || []).filter((n) => n !== name);
    const next = { ...x };
    if (left.length) next[doctype] = left; else delete next[doctype];
    return next;
  });
  const addKeyword = (k: string) => setText((t) => (t.trim() ? `${t.trim().replace(/,\s*$/, "")}, ${k}` : k));

  /* step 5: save the Draft agent */
  const { call: createCall, loading: creating } = useFrappePostCall<{ message: { name: string } }>(M.create);
  const create = async () => {
    try {
      const custom = isCustom ? { description: description.trim(), abilities: abilitiesText(abilities) } : {};
      const r = await createCall({ agent_type: agentType, keywords: debounced, rows: rowsJson, excluded: excludedJson, persona: persona.trim(), backup: backup || undefined, team: team || undefined, ...custom });
      toast.success("Draft agent created");
      navigate(`/agents/${encodeURIComponent(r.message.name)}`);
    } catch (e) { toastError(e, "Could not create the agent"); }
  };

  const usable = rows.filter((r) => r.included && r.resolved_as !== "Unmatched" && r.resolved_as !== "AI proposal");
  const canNext: Record<Step, boolean> = {
    type: Boolean(agentType) && (!isCustom || (Boolean(description.trim()) && abilities.length > 0)),
    keywords: usable.length > 0 && !resolving && debounced === text.trim(),
    erp: true,
    people: true,
    create: false,
  };
  const excludedCount = Object.values(excluded).reduce((n, l) => n + l.length, 0);

  if (!typesLoading && !canBuild) {
    return (
      <PageFrame title="New agent" icon={<Bot />}>
        <EmptyState icon={<Bot />} title="You cannot build agents" hint="An Excom Admin or a team's manager builds agents. Ask one of them." action={<Button onClick={() => navigate("/agents")}>Back to agents</Button>} />
      </PageFrame>
    );
  }

  return (
    <PageFrame title="New agent" icon={<Bot />}>
      <div className="max-w-3xl mx-auto min-w-0">
        <div className="min-w-0 max-w-full overflow-x-auto -mx-3 px-3 border-b border-border mb-3">
          <SegmentedControl<Step> value={step} onChange={go} ariaLabel="Builder steps"
            segments={STEPS.map((s, i) => ({ value: s.value, label: s.label, disabled: i > reached }))} />
        </div>

        {step === "type" && (
          <section aria-labelledby="step-type">
            <h2 id="step-type" className="text-md text-ink-1">What should the agent do?</h2>
            <p className="text-sm text-ink-3 mb-3">Pick the kind of work. You can name it and narrow it down next.</p>
            {typesLoading ? <p className="text-sm text-ink-3">Loading…</p> : (
              <div className="grid grid-cols-1 tablet:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-2">
                {types.map((t) => (
                  <button key={t.name} type="button" aria-pressed={agentType === t.name}
                    onClick={() => { if (t.name !== agentType) { setOverrides({}); setExcluded({}); setPreview(null); } setAgentType(t.name); if (t.pack !== "custom") go("keywords"); }}
                    className={cn("text-left rounded-lg border p-3 min-w-0 hover:bg-surface-hover outline-none focus-visible:ring-2 focus-visible:ring-crayon-blue-base",
                      agentType === t.name ? "border-crayon-blue-base bg-surface-active" : "border-border")}>
                    <TypeChip type={t.name} colour={t.colour} size="md" />
                    <p className="text-sm text-ink-2 mt-1.5 break-words">{t.description}</p>
                  </button>
                ))}
              </div>
            )}
            {isCustom && (
              <div className="mt-4 space-y-3">
                <Field label="What should it do?" htmlFor="custom-description" required
                  hint="A few plain sentences. They become the rules in its playbook, which you review before it starts.">
                  <Textarea id="custom-description" rows={3} value={description} onChange={(e) => setDescription(e.target.value)}
                    placeholder="For example: watch new purchase orders and draft a short note to the supplier when one is late." autoFocus />
                </Field>
                <fieldset className="min-w-0">
                  <legend className="text-xs text-ink-3 mb-1">What it may do. It always asks you first.</legend>
                  <ul className="rounded-lg border border-border divide-y divide-border">
                    {ABILITIES.map((ab) => (
                      <li key={ab.value} className="flex items-start gap-2 px-3 py-2 min-w-0">
                        <input id={`ability-${ab.value}`} type="checkbox" className="size-4 shrink-0 mt-0.5" checked={abilities.includes(ab.value)}
                          onChange={(e) => toggleAbility(ab.value, e.target.checked)} aria-describedby={`ability-${ab.value}-hint`} />
                        <label htmlFor={`ability-${ab.value}`} className="min-w-0 flex-1">
                          <span className="block text-sm font-medium text-ink-1">{ab.label}</span>
                          <span id={`ability-${ab.value}-hint`} className="block text-xs text-ink-2 break-words">{ab.hint}</span>
                        </label>
                      </li>
                    ))}
                  </ul>
                  {abilities.length === 0 && <p className="text-xs text-crayon-rose-text mt-1">Choose at least one.</p>}
                </fieldset>
              </div>
            )}
          </section>
        )}

        {step === "keywords" && (
          <section aria-labelledby="step-keywords" className="space-y-3">
            <div>
              <h2 id="step-keywords" className="text-md text-ink-1">Describe the job in a few words</h2>
              <p className="text-sm text-ink-3">Separate them with commas: products, markets, languages, kinds of buyer. For example: export, water guns, latin america, spanish.</p>
            </div>
            <Field label="Keywords" htmlFor="kw">
              <Textarea id="kw" rows={2} value={text} onChange={(e) => setText(e.target.value)} placeholder="export, water guns, latin america, spanish" autoFocus />
            </Field>
            <div className="flex items-center gap-2 text-xs text-ink-3 min-h-4" aria-live="polite">
              {resolving && <><Loader2 className="size-3.5 animate-spin" />Looking these up in your ERP…</>}
              {resolveError && <span className="text-crayon-rose-text">{parseError(resolveError).message}</span>}
            </div>
            {rows.length > 0 && (
              <ul className="flex flex-wrap gap-2" aria-label="What each keyword was understood as">
                {rows.map((r) => <KeywordChip key={r.keyword} row={r} raw={resolved?.message.find((x) => x.keyword === r.keyword) ?? r}
                  usingAlt={Boolean(overrides[r.keyword]?.alt)}
                  onToggle={(on) => setOverride(r.keyword, { off: !on })}
                  onAlt={(alt) => setOverride(r.keyword, { alt })}
                  onConfirmed={() => reResolve()} />)}
              </ul>
            )}
          </section>
        )}

        {step === "erp" && (
          <section aria-labelledby="step-erp" className="space-y-3">
            <div>
              <h2 id="step-erp" className="text-md text-ink-1">What your ERP shows for these keywords</h2>
              <p className="text-sm text-ink-3">This is what the agent will know. Open a list to untick a customer or an item it should leave out.</p>
            </div>
            <div className="flex items-center gap-2 text-xs text-ink-3 min-h-4" aria-live="polite">
              {previewing && <><Loader2 className="size-3.5 animate-spin" />Asking the ERP…</>}
              {!previewing && preview && <span>{preview.company} · amounts in {preview.currency}</span>}
            </div>
            {previewError && <p role="alert" className="text-sm text-crayon-rose-text">{previewError}</p>}
            {preview && preview.left_out.length > 0 && (
              <div className="rounded-md border border-crayon-amber-base/50 bg-crayon-amber-tint px-3 py-2 text-sm text-crayon-amber-text space-y-1.5" role="status">
                {preview.left_out.map((c) => {
                  const lang = LANGUAGE_OF[c];
                  return (
                    <div key={c} className="flex items-center gap-2 flex-wrap min-w-0">
                      <span className="min-w-0 break-words">{lang ? `${c} speaks ${lang}: left out.` : `${c} does not speak the language you chose: left out.`}</span>
                      {lang ? <Button size="sm" variant="default" onClick={() => addKeyword(lang.toLowerCase())}><Plus />Add {lang}</Button> : <span className="text-xs">Add its language as a keyword to include it.</span>}
                    </div>
                  );
                })}
              </div>
            )}
            {excludedCount > 0 && (
              <div className="rounded-md border border-border px-3 py-2 min-w-0">
                <p className="text-xs text-ink-3 mb-1">You left out {excludedCount}:</p>
                <ul className="flex flex-wrap gap-1">
                  {Object.entries(excluded).flatMap(([dt, names]) => names.map((n) => (
                    <li key={`${dt}:${n}`} className="inline-flex items-center gap-1 h-6 pl-2 pr-1 rounded-full bg-surface-sunken text-xs text-ink-2 max-w-full min-w-0">
                      <span className="truncate">{dt} {n}</span>
                      <button type="button" onClick={() => putBack(dt, n)} className="rounded-full px-1 hover:bg-surface-hover text-crayon-blue-text shrink-0" aria-label={`Put ${dt} ${n} back`}>Put back</button>
                    </li>
                  )))}
                </ul>
              </div>
            )}
            {preview ? (
              <div className="grid grid-cols-1 laptop:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-2 items-start">
                {preview.sections.map((s) => <SectionPanel key={s.section} section={s} onExclude={exclude} />)}
              </div>
            ) : !previewError && <p className="text-sm text-ink-3">Loading…</p>}
          </section>
        )}

        {step === "people" && (
          <section aria-labelledby="step-people" className="space-y-3">
            <div>
              <h2 id="step-people" className="text-md text-ink-1">Who looks after it</h2>
              <p className="text-sm text-ink-3">You ({currentUserFullName()}) will own it. The backup takes over when you are away.</p>
            </div>
            <Field label="Team" hint="The agent may use the numbers and mailboxes this team works on.">
              <LinkField doctype="Excom Team" value={team} onChange={setTeam} placeholder="Choose a team" />
            </Field>
            <Field label="Backup" hint="Someone who can approve and stop it when you cannot.">
              <LinkField doctype="User" value={backup} onChange={setBackup} placeholder="Choose a person" filters={{ enabled: 1, user_type: "System User" }} />
            </Field>
            <Field label="Persona name (optional)" htmlFor="persona" hint="The name it signs with. Leave empty and one is suggested for the market's language.">
              <Input id="persona" value={persona} onChange={(e) => setPersona(e.target.value)} placeholder="For example Lucía" />
            </Field>
          </section>
        )}

        {step === "create" && (
          <section aria-labelledby="step-create" className="space-y-3">
            <div>
              <h2 id="step-create" className="text-md text-ink-1">Create the draft</h2>
              <p className="text-sm text-ink-3">It is saved as a Draft: it does nothing until you approve its brief and start it in Shadow, where nothing is sent.</p>
            </div>
            <dl className="rounded-lg border border-border divide-y divide-border text-sm">
              {[
                ["Type", agentType],
                ...(isCustom ? [
                  ["What it does", description.trim() || "—"],
                  ["Abilities", ABILITIES.filter((ab) => abilities.includes(ab.value)).map((ab) => ab.label).join(", ") || "—"],
                ] : []),
                ["Keywords", usable.map((r) => r.keyword).join(", ") || "—"],
                ["Left out", excludedCount ? `${excludedCount} records` : "Nothing"],
                ["Team", team || "None"],
                ["Backup", backup || "None"],
                ["Persona", persona.trim() || "Suggested for you"],
              ].map(([k, v]) => (
                <div key={k} className="flex gap-3 px-3 py-2 min-w-0"><dt className="w-24 shrink-0 text-ink-3">{k}</dt><dd className="min-w-0 break-words text-ink-1">{v}</dd></div>
              ))}
            </dl>
            <Button variant="primary" size="lg" disabled={creating} onClick={create} className="w-full tablet:w-auto">
              {creating ? <><Loader2 className="animate-spin" />Writing the brief and checking readiness…</> : <><Check />Create the draft agent</>}
            </Button>
            {creating && <p className="text-xs text-ink-3">This can take up to a minute.</p>}
          </section>
        )}

        {(step !== "type" || isCustom) && (
          <div className="sticky bottom-0 -mx-3 px-3 py-2 mt-4 bg-surface border-t border-border flex items-center gap-2">
            {idx > 0 && <Button variant="ghost" onClick={() => go(STEPS[idx - 1].value)} disabled={creating}><ArrowLeft />Back</Button>}
            <span className="flex-1" />
            {step !== "create" && <Button variant="primary" disabled={!canNext[step]} onClick={() => go(STEPS[idx + 1].value)}>Next<ArrowRight /></Button>}
          </div>
        )}
      </div>
    </PageFrame>
  );
}

/** One keyword: what it was understood as, how much it covers, and the choices it offers. */
function KeywordChip({ row: r, raw, usingAlt, onToggle, onAlt, onConfirmed }: {
  row: KeywordRow; raw: KeywordRow; usingAlt: boolean;
  onToggle: (on: boolean) => void; onAlt: (alt: boolean) => void; onConfirmed: () => void;
}) {
  const { call: confirm, loading } = useFrappePostCall(M.confirmGlossary);
  const on = Boolean(r.included);
  const proposal = r.resolved_as === "AI proposal";
  const unmatched = r.resolved_as === "Unmatched";
  const id = `kw-${r.keyword.replace(/\W+/g, "-")}`;
  return (
    <li className={cn("rounded-lg border px-2.5 py-2 min-w-0 max-w-full w-full tablet:w-auto tablet:max-w-[360px]",
      unmatched ? "border-border bg-surface-sunken" : proposal ? "border-crayon-amber-base/60 bg-crayon-amber-tint" : "border-border bg-surface",
      !on && "opacity-60")}>
      <div className="flex items-center gap-2 min-w-0">
        <input id={id} type="checkbox" checked={on} onChange={(e) => onToggle(e.target.checked)} className="size-4 shrink-0" aria-label={`Use "${r.keyword}"`} />
        <label htmlFor={id} className={cn("text-sm font-medium truncate flex-1 min-w-0", unmatched ? "text-ink-3" : "text-ink-1")}>{r.keyword}</label>
        {r.matches > 0 && <Badge accent={unmatched ? "neutral" : "blue"} count={r.matches} max={9999} title={`${r.matches} found`} />}
      </div>
      <p className={cn("text-xs mt-0.5 break-words", unmatched ? "text-ink-3" : "text-ink-2")}>Understood as: {understood(r)}</p>
      {proposal && raw.proposal && (
        <div className="mt-1.5 space-y-1.5">
          <p className="text-xs text-crayon-amber-text break-words">
            {raw.proposal.in_group} of the {raw.proposal.items} items named like “{r.keyword}” are in {raw.proposal.item_group} ({raw.proposal.group_items} items). Not used until you confirm it.
          </p>
          <Button size="sm" variant="primary" disabled={loading} onClick={async () => {
            try {
              await confirm({ term: raw.proposal!.term, link_doctype: raw.link_doctype || "Item Group", link_name: raw.link_name || raw.proposal!.item_group });
              toast.success(`“${r.keyword}” now means ${raw.proposal!.item_group}, for every agent`);
              onConfirmed();
            } catch (e) { toastError(e, "Could not confirm"); }
          }}><Check />Confirm: it means {raw.proposal.item_group}</Button>
        </div>
      )}
      {unmatched && <p className="text-xs text-ink-3 mt-0.5">It will be left out.</p>}
      {raw.alternative && (usingAlt ? (
        <button type="button" onClick={() => onAlt(false)} className="mt-1 inline-flex items-center gap-1 text-xs text-crayon-blue-text hover:underline"><Undo2 className="size-3.5" />Go back to {raw.resolved_as === "AI proposal" ? "the suggestion" : understood(raw)}</button>
      ) : (
        <button type="button" onClick={() => onAlt(true)} className="mt-1 text-xs text-crayon-blue-text hover:underline text-left break-words">Use {raw.alternative.link_doctype} {raw.alternative.link_name} instead</button>
      ))}
    </li>
  );
}
