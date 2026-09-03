import { useState, useEffect, useCallback } from "react";
import { useFrappePostCall } from "frappe-react-sdk";
import { GitMerge, X, Phone, Mail, MessageCircle, Check, ArrowRight } from "lucide-react";
import { toast } from "sonner";
import { Button, Chip, EmptyState, Badge } from "./primitives";
import { AdminPage } from "./shell/AdminPage";

interface Suggestion {
  source_name: string; source_display_name: string; source_phone: string; source_email: string; source_whatsapp: string;
  target_name: string; target_display_name: string; target_phone: string; target_email: string; target_whatsapp: string;
  shared_fields: string[];
}

const FIELD_ICON: Record<string, React.ReactNode> = { Phone: <Phone />, Email: <Mail />, WhatsApp: <MessageCircle /> };

/** Merge suggestions — avatar menu page (T3). Side-by-side on tablet+, stacked on phone. */
export function MergeSuggestionsPage({ onNavigateBack, embedded }: { onNavigateBack: () => void; embedded?: boolean }) {
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [processing, setProcessing] = useState<Set<string>>(new Set());
  const { call: fetchSuggestions } = useFrappePostCall("excom.excom.api.merge_suggestions.get_merge_suggestions");
  const { call: approveMerge } = useFrappePostCall("excom.excom.api.merge_suggestions.approve_merge");
  const { call: dismissSuggestion } = useFrappePostCall("excom.excom.api.merge_suggestions.dismiss_suggestion");

  const load = useCallback(async () => {
    try { const res = await fetchSuggestions({ limit: 100 }); setSuggestions((res as any)?.message || []); } catch { toast.error("Failed to load suggestions"); }
  }, [fetchSuggestions]);
  useEffect(() => { load(); }, [load]);

  const act = async (source: string, fn: () => Promise<unknown>, ok: string, fail: string) => {
    setProcessing((p) => new Set(p).add(source));
    try { await fn(); toast.success(ok); setSuggestions((prev) => prev.filter((s) => s.source_name !== source)); }
    catch { toast.error(fail); }
    finally { setProcessing((p) => { const n = new Set(p); n.delete(source); return n; }); }
  };

  return (
    <AdminPage title="Merge suggestions" icon={<GitMerge />} onBack={onNavigateBack} embedded={embedded} actions={<Badge accent="green" count={suggestions.length} />}>
      {suggestions.length === 0 ? (
        <EmptyState icon={<GitMerge />} title="No merge suggestions" hint="The system scans daily for identities that share a phone, email or WhatsApp number." />
      ) : (
        <div className="space-y-3 max-w-[900px] mx-auto">
          {suggestions.map((s) => (
            <div key={s.source_name} className="rounded-lg border border-border p-3 min-w-0">
              <div className="flex items-center gap-2 min-w-0 mb-3">
                <div className="chip-row flex-1 h-6">
                  {s.shared_fields.map((f) => <Chip key={f} size="sm" accent="amber" icon={FIELD_ICON[f]} label={`Shared ${f}`} />)}
                </div>
                <div className="flex items-center gap-1.5 shrink-0">
                  <Button size="sm" variant="ghost" disabled={processing.has(s.source_name)} onClick={() => act(s.source_name, () => dismissSuggestion({ identity: s.source_name }), "Suggestion dismissed", "Dismiss failed")}><X />Dismiss</Button>
                  <Button size="sm" variant="primary" disabled={processing.has(s.source_name)} onClick={() => act(s.source_name, () => approveMerge({ source: s.source_name, target: s.target_name }), "Identities merged", "Merge failed")}><Check />Merge</Button>
                </div>
              </div>
              <div className="grid gap-2 items-center [grid-template-columns:minmax(0,1fr)] tablet:[grid-template-columns:minmax(0,1fr)_auto_minmax(0,1fr)]">
                <IdentityCard label="Keep" name={s.target_name} displayName={s.target_display_name} phone={s.target_phone} email={s.target_email} whatsapp={s.target_whatsapp} />
                <ArrowRight className="size-4 text-ink-muted mx-auto rotate-90 tablet:rotate-180" />
                <IdentityCard label="Merge into it" name={s.source_name} displayName={s.source_display_name} phone={s.source_phone} email={s.source_email} whatsapp={s.source_whatsapp} />
              </div>
            </div>
          ))}
        </div>
      )}
    </AdminPage>
  );
}

function IdentityCard({ label, name, displayName, phone, email, whatsapp }: { label: string; name: string; displayName: string; phone: string; email: string; whatsapp: string }) {
  return (
    <div className="rounded-md bg-surface-sunken p-2.5 min-w-0">
      <span className="text-xs text-ink-3">{label}</span>
      <p className="text-sm font-medium text-ink-1 truncate mt-0.5" title={name}>{displayName || name}</p>
      <div className="mt-1.5 space-y-0.5 text-xs text-ink-2 [&_svg]:size-3 [&_svg]:text-ink-muted [&_svg]:shrink-0">
        {phone && <p className="flex items-center gap-1.5 min-w-0"><Phone /><span className="truncate">{phone}</span></p>}
        {email && <p className="flex items-center gap-1.5 min-w-0"><Mail /><span className="truncate">{email}</span></p>}
        {whatsapp && <p className="flex items-center gap-1.5 min-w-0"><MessageCircle /><span className="truncate">{whatsapp}</span></p>}
      </div>
    </div>
  );
}
