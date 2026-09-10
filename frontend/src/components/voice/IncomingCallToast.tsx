import { useEffect, useState } from "react";
import { Phone, PhoneOff, ExternalLink } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { useFrappeGetCall } from "@/lib/api";
import { Avatar, Button } from "../primitives";
import type { SoftphoneApi } from "@/hooks/useSoftphone";

/**
 * The screen pop.
 *
 * It appears the instant the server publishes `excom:call_ringing`, which happens before any
 * database write — so it is up while the provider is still dialling. Everything beyond the number
 * (name, company, the record they belong to) is fetched afterwards and fills in, rather than the
 * pop waiting on a lookup.
 */
export function IncomingCallToast({ api }: { api: SoftphoneApi }) {
  const { incoming, answeredElsewhere, answer, reject, dismissIncoming } = api;
  const navigate = useNavigate();
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!incoming) {
      setElapsed(0);
      return;
    }
    const id = window.setInterval(() => setElapsed((n) => n + 1), 1000);
    return () => window.clearInterval(id);
  }, [incoming?.provider_call_id, Boolean(incoming)]);

  // Identity context, fetched once the pop is already visible.
  const { data } = useFrappeGetCall<{ message: any }>(
    incoming?.omni_identity ? "excom.excom.api.record.get_identity" : null,
    incoming?.omni_identity ? { omni_identity: incoming.omni_identity } : undefined,
  );
  const identity = data?.message;

  if (answeredElsewhere) {
    return (
      <div className="fixed bottom-4 right-4 z-50 max-w-xs rounded-lg border border-border bg-surface px-3 py-2 text-sm text-ink-2 shadow-ex">
        Picked up by <span className="text-ink-1 font-medium">{answeredElsewhere}</span>
      </div>
    );
  }

  if (!incoming) return null;

  const name = identity?.display_name || incoming.display_name || incoming.from_number;
  const company = identity?.company_name || identity?.linked_entity_label || "";
  const remaining = Math.max(0, (incoming.ring_seconds || 30) - elapsed);

  return (
    <div
      role="alertdialog"
      aria-label={`Incoming call from ${name}`}
      className="fixed bottom-4 right-4 z-50 w-[min(22rem,calc(100vw-2rem))] overflow-hidden rounded-xl border border-crayon-green-base/50 bg-surface shadow-ex"
    >
      <div className="h-0.5 w-full bg-crayon-green-base" />

      <div className="flex items-start gap-3 p-3">
        <Avatar name={name} size={40} />

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 text-2xs uppercase tracking-wide text-crayon-green-text">
            <Phone className="size-3" />
            Incoming call
            <span className="ml-auto tabular-nums text-ink-3">{remaining}s</span>
          </div>

          <div className="truncate text-md text-ink-1">{name}</div>
          <div className="truncate text-xs text-ink-3">
            {incoming.from_number}
            {company ? ` · ${company}` : ""}
          </div>

          {incoming.omni_identity && (
            <button
              type="button"
              onClick={() => navigate(`/t/${incoming.omni_identity}`)}
              className="mt-1 inline-flex items-center gap-1 text-xs text-crayon-blue-text hover:underline"
            >
              Open conversation <ExternalLink className="size-3" />
            </button>
          )}
        </div>
      </div>

      <div className="flex gap-2 border-t border-border p-2">
        <Button variant="danger" size="touch" className="flex-1" onClick={reject}>
          <PhoneOff className="size-4" />
          Decline
        </Button>
        <Button
          size="touch"
          className="flex-1 bg-crayon-green-base text-white hover:bg-crayon-green-text"
          onClick={answer}
        >
          <Phone className="size-4" />
          Answer
        </Button>
      </div>

      <button
        type="button"
        onClick={dismissIncoming}
        className="absolute right-2 top-2 text-ink-3 hover:text-ink-1"
        aria-label="Dismiss"
      >
        ×
      </button>
    </div>
  );
}
