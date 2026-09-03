import { X, Sparkles, ArrowRight, CheckCircle2, Clock, AlertCircle, Loader2, ExternalLink, RefreshCw } from "lucide-react";
import { Button, Chip, EmptyState } from "./primitives";
import type { Accent } from "./primitives/Chip";
import { useAISuggestions } from "../hooks/useAISuggestions";
import { cn } from "./ui/utils";

interface AIAssistantDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  contactName: string;
  threadId?: string;
  onUseSuggestion?: (text: string) => void;
  /** Rendered as a tab in the P1 details pane (no fixed overlay). */
  embedded?: boolean;
}

const PRIORITY: Record<string, { accent: Accent; icon: React.FC<{ className?: string }> }> = {
  high: { accent: "rose", icon: AlertCircle }, medium: { accent: "amber", icon: Clock }, low: { accent: "blue", icon: CheckCircle2 },
};

/** AI assist — T3. Suggested replies, summary, next actions, insights. Violet = AI/automated (UX-001 §2.2). */
export function AIAssistantDrawer({ isOpen, onClose, contactName, threadId, onUseSuggestion, embedded }: AIAssistantDrawerProps) {
  const { suggestions, isLoading, refresh } = useAISuggestions(isOpen ? threadId || null : null);
  if (!isOpen) return null;
  const { suggested_replies, summary, next_actions, insights } = suggestions;

  const body = (
    <div className="flex flex-col h-full min-h-0">
      {!embedded && (
        <div className="shrink-0 h-header-h px-3 flex items-center gap-2 border-b border-border">
          <Sparkles className="size-4 text-crayon-violet-base" /><h2 className="text-md text-ink-1 truncate flex-1">AI assistant</h2>
          <Button variant="ghost" size="icon" aria-label="Close" onClick={onClose}><X /></Button>
        </div>
      )}
      <div className="flex-1 min-h-0 overflow-y-auto p-3 space-y-4">
        {isLoading ? <div className="flex justify-center py-10 text-ink-3"><Loader2 className="size-5 animate-spin" /></div> : (
          <>
            <Block title="Suggested replies">
              {suggested_replies.length === 0 ? <p className="text-xs text-ink-3">No suggestions yet for {contactName}.</p> : (
                <div className="space-y-1.5">
                  {suggested_replies.map((r, i) => (
                    <button key={i} type="button" onClick={() => onUseSuggestion?.(r.text)} className="w-full text-left rounded-md border border-border bg-crayon-violet-tint/60 px-2.5 py-2 hover:border-crayon-violet-base/60 group min-w-0">
                      <p className="text-sm text-ink-1 break-words">{r.text}</p>
                      {onUseSuggestion && <span className="text-xs text-ink-3 inline-flex items-center gap-1 mt-1">Use <ArrowRight className="size-3" /></span>}
                    </button>
                  ))}
                </div>
              )}
            </Block>
            <Block title="Summary">
              <p className="text-sm text-ink-1 break-words">{summary.text || "No summary available"}</p>
              <div className="flex items-center gap-2 mt-1.5"><Chip size="sm" accent="violet" label={summary.sentiment} />{summary.updated_at && <span className="text-xs text-ink-3 tabular-nums">{new Date(summary.updated_at).toLocaleTimeString()}</span>}</div>
            </Block>
            {next_actions.length > 0 && (
              <Block title="Recommended actions">
                <div className="space-y-1.5">
                  {next_actions.map((a, i) => {
                    const p = PRIORITY[a.priority] || PRIORITY.low;
                    return (
                      <div key={i} className="flex items-start gap-2 rounded-md border border-border px-2.5 py-2 min-w-0">
                        <p.icon className={cn("size-4 mt-0.5 shrink-0", `text-crayon-${p.accent}-base`)} />
                        <div className="flex-1 min-w-0"><p className="text-sm text-ink-1 break-words">{a.action}</p><div className="flex items-center gap-2 mt-0.5"><Chip size="sm" accent={p.accent} label={a.priority} />{a.due && <span className="text-xs text-ink-3">Due {a.due}</span>}</div></div>
                        <Button size="icon-sm" variant="ghost" aria-label="Open" onClick={() => { const m = a.action.match(/Lead\s+(.+)/); if (m) window.open(`/app/lead/${encodeURIComponent(m[1])}`, "_blank"); }}><ExternalLink /></Button>
                      </div>
                    );
                  })}
                </div>
              </Block>
            )}
            <Block title="Insights">
              <dl className="grid grid-cols-1 gap-1.5 text-sm">
                <div><dt className="text-xs text-ink-3">Response pattern</dt><dd className="text-ink-1">{insights.response_pattern}</dd></div>
                <div><dt className="text-xs text-ink-3">Engagement</dt><dd className="text-ink-1 tabular-nums">{insights.engagement_rate > 0 ? `${Math.round(insights.engagement_rate * 100)}% reply ratio` : "No engagement data"}</dd></div>
                <div><dt className="text-xs text-ink-3">Best contact time</dt><dd className="text-ink-1">{insights.best_contact_time}</dd></div>
              </dl>
            </Block>
            {!threadId && <EmptyState icon={<Sparkles />} title="No thread selected" compact />}
          </>
        )}
      </div>
      <div className="shrink-0 border-t border-border p-2"><Button className="w-full" onClick={() => refresh()}><RefreshCw />Regenerate</Button></div>
    </div>
  );

  if (embedded) return body;
  return <div className="fixed inset-y-0 right-0 w-96 max-w-[100vw] bg-surface border-l border-border shadow-ex z-50 flex flex-col overflow-hidden">{body}</div>;
}

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className="min-w-0"><h3 className="text-xs text-ink-3 mb-1.5 flex items-center gap-1.5"><Sparkles className="size-3.5 text-crayon-violet-base" />{title}</h3>{children}</section>;
}
