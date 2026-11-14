import { useCallback, useEffect, useState } from "react";
import { useFrappePostCall } from "frappe-react-sdk";
import { Loader2, RefreshCw, UserPlus } from "lucide-react";
import { Button, Chip, Modal } from "../primitives";
import { parseError } from "../../lib/errors";
import { MAX_PEOPLE_PICKS, creditsFor, togglePick } from "../../lib/agents-logic";
import type { AddPeopleResult, FindPeopleResult, PeopleCandidate } from "../../types/agents";
import type { CrmRef } from "../../hooks/useCrm";
import { LinkedInLink, M } from "../agents/bits";

/**
 * Find people at this company (HLD-009 §3.5): search, tick up to five, add them as contacts.
 *
 * Searching is free (Apollo) or about one credit (Prospeo); adding is where credits go — one per
 * person whose address research has not already found. The dialog says so before the click, not after.
 */
export function FindPeopleDialog({
  refr,
  open,
  onOpenChange,
  onAdded,
}: {
  refr: CrmRef;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onAdded: () => void;
}) {
  const { call: findCall } = useFrappePostCall<{ message: FindPeopleResult }>(M.findPeople);
  const { call: addCall } = useFrappePostCall<{ message: AddPeopleResult }>(M.addPeople);
  const [searching, setSearching] = useState(false);
  const [adding, setAdding] = useState(false);
  const [found, setFound] = useState<FindPeopleResult | null>(null);
  const [picks, setPicks] = useState<string[]>([]);
  const [result, setResult] = useState<AddPeopleResult | null>(null);
  const [err, setErr] = useState("");

  const search = useCallback(async () => {
    setSearching(true);
    setErr("");
    setFound(null);
    setPicks([]);
    setResult(null);
    try {
      const r = await findCall({ doctype: refr.doctype, name: refr.name });
      setFound(r?.message ?? null);
    } catch (e) {
      setErr(parseError(e).message || "Could not search for people");
    } finally {
      setSearching(false);
    }
  }, [findCall, refr.doctype, refr.name]);

  useEffect(() => {
    if (open) search();
  }, [open, search]);

  const add = async () => {
    if (!picks.length || adding) return;
    setAdding(true);
    setErr("");
    try {
      const r = await addCall({ doctype: refr.doctype, name: refr.name, picks: JSON.stringify(picks) });
      setResult(r?.message ?? { added: [], no_email: [] });
      onAdded();
    } catch (e) {
      setErr(parseError(e).message || "Could not add the people");
    } finally {
      setAdding(false);
    }
  };

  const candidates = found?.candidates ?? [];
  const credits = creditsFor(candidates, picks);
  const full = picks.length >= MAX_PEOPLE_PICKS;

  const footer = result ? (
    <Button variant="primary" onClick={() => onOpenChange(false)}>Done</Button>
  ) : (
    <>
      <span className="text-xs text-ink-3 flex-1 min-w-0">
        {picks.length === 0
          ? `Tick up to ${MAX_PEOPLE_PICKS}.`
          : credits === 0
            ? "Their emails are already known: no credits used."
            : `Uses about ${credits} credit${credits === 1 ? "" : "s"} (~$${(credits * 0.04).toFixed(2)}), one per person whose email isn't known yet.`}
      </span>
      <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
      <Button variant="primary" disabled={!picks.length || adding || searching} onClick={add}>
        {adding ? <Loader2 className="animate-spin" /> : <UserPlus />}
        {adding ? "Adding…" : picks.length ? `Add ${picks.length} ${picks.length === 1 ? "person" : "people"}` : "Add people"}
      </Button>
    </>
  );

  return (
    <Modal open={open} onOpenChange={onOpenChange} title="Find people" width="max-w-lg" footer={footer}>
      <div className="space-y-3">
        {err && (
          <div role="alert" className="text-sm text-crayon-rose-text flex items-start gap-2">
            <p className="flex-1 min-w-0 whitespace-pre-line">{err}</p>
            {!searching && !result && (
              <Button size="sm" variant="ghost" onClick={search}><RefreshCw />Search again</Button>
            )}
          </div>
        )}

        {result ? (
          <AddedSummary result={result} />
        ) : searching ? (
          <p className="flex items-center gap-2 text-sm text-ink-3"><Loader2 className="size-4 animate-spin" />Searching…</p>
        ) : found ? (
          <>
            <p className="text-xs text-ink-3">
              {found.searched
                ? `${candidates.length} found at ${found.domain}${found.provider ? ` (${found.provider})` : ""}. Searching is free or about one credit; adding uses credits.`
                : "No people search is available: Apollo and Prospeo are switched off or out of budget. Ask a manager."}
            </p>
            {found.searched && candidates.length === 0 && (
              <p className="text-sm text-ink-2">Nobody found with the job titles we look for.</p>
            )}
            {candidates.length > 0 && (
              <ul className="divide-y divide-border rounded-md border border-border">
                {candidates.map((c) => (
                  <CandidateRow
                    key={c.pick}
                    c={c}
                    checked={picks.includes(c.pick)}
                    disabled={!picks.includes(c.pick) && full}
                    onToggle={() => setPicks((p) => togglePick(p, c.pick))}
                  />
                ))}
              </ul>
            )}
            {full && <p className="text-xs text-ink-3">{MAX_PEOPLE_PICKS} at a time. Add these, then search again for more.</p>}
          </>
        ) : null}
      </div>
    </Modal>
  );
}

function CandidateRow({ c, checked, disabled, onToggle }: { c: PeopleCandidate; checked: boolean; disabled: boolean; onToggle: () => void }) {
  const id = `find-people-${c.pick}`;
  return (
    <li className="flex items-center gap-2 px-2 py-1.5 min-w-0">
      <input id={id} type="checkbox" className="size-4 shrink-0" checked={checked} disabled={disabled} onChange={onToggle} />
      <label htmlFor={id} className="min-w-0 flex-1 cursor-pointer">
        <span className="block text-sm text-ink-1 truncate">{c.full_name || "Name hidden"}</span>
        <span className="block text-xs text-ink-3 truncate">{[c.title, c.seniority].filter(Boolean).join(" · ") || "No title"}</span>
      </label>
      {c.known_email ? (
        <Chip size="sm" accent="green" label="Email known" title={`Already found: ${c.known_email}. No credit used.`} />
      ) : c.has_email ? (
        <Chip size="sm" accent="neutral" label="Has email" title="The provider holds an address. Fetching it uses about one credit." />
      ) : c.has_email === false ? (
        <Chip size="sm" accent="amber" label="No email" title="The provider has no address. A lookup may still find none." />
      ) : null}
      <LinkedInLink url={c.linkedin_url} label="" className="p-1" />
    </li>
  );
}

function AddedSummary({ result }: { result: AddPeopleResult }) {
  return (
    <div className="space-y-3">
      {result.added.length > 0 ? (
        <div>
          <p className="text-sm text-ink-1 mb-1">Added to this record: {result.added.length}</p>
          <ul className="divide-y divide-border rounded-md border border-border">
            {result.added.map((a) => (
              <li key={a.contact} className="flex items-center gap-2 px-2 py-1.5 min-w-0">
                <div className="min-w-0 flex-1">
                  <p className="text-sm text-ink-1 truncate">{a.full_name || a.email}</p>
                  <p className="text-xs text-ink-3 truncate">{a.email}{a.reused ? " · already in your contacts, linked" : ""}</p>
                </div>
                <LinkedInLink url={a.linkedin_url} label="" className="p-1" />
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="text-sm text-ink-2">Nobody was added.</p>
      )}
      {result.no_email.length > 0 && (
        <div>
          <p className="text-sm text-ink-1 mb-1">No email found: {result.no_email.length}</p>
          <p className="text-xs text-ink-3 mb-1">You can write to them on LinkedIn instead.</p>
          <ul className="divide-y divide-border rounded-md border border-border">
            {result.no_email.map((p, i) => (
              <li key={`${p.linkedin_url || p.full_name}-${i}`} className="flex items-center gap-2 px-2 py-1.5 min-w-0">
                <span className="text-sm text-ink-1 truncate flex-1 min-w-0">{p.full_name || "Name hidden"}</span>
                <LinkedInLink url={p.linkedin_url} />
              </li>
            ))}
          </ul>
        </div>
      )}
      {(result.failed?.length ?? 0) > 0 && (
        <p className="text-sm text-crayon-rose-text">
          Could not add {result.failed!.map((p) => p.full_name || p.email).join(", ")}. Try adding them again: it uses no
          more credits.
        </p>
      )}
    </div>
  );
}
