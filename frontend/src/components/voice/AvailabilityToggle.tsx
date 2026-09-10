import { Headphones, HeadphoneOff, Loader2, AlertTriangle } from "lucide-react";
import { Button } from "../primitives";
import { cn } from "../ui/utils";
import { useSoftphoneContext } from "./SoftphoneProvider";

/**
 * "Am I taking calls?" — the one control an agent touches every day.
 *
 * It shows the true state, not the wish: availability is what the agent chose, but the dot only
 * goes green when the softphone is actually registered. An agent who thinks they are on the queue
 * while their browser is disconnected is the failure this control exists to prevent.
 *
 * Renders nothing when the site has no voice line.
 */
export function AvailabilityToggle({ compact = false }: { compact?: boolean }) {
  const api = useSoftphoneContext();
  if (!api?.config?.enabled) return null;

  const { state, available, toggleAvailability, config } = api;
  const reg = state.registration;

  const busy = reg === "loading" || reg === "registering";
  const broken = reg === "failed" || reg === "unsupported";
  // Phone transport still works without a registration, so a line with the fallback on is never
  // truly unreachable — say "on your phone" rather than "unavailable".
  const phoneOnly = !config.browser_calls || !config.has_endpoint || broken;

  const label = !available
    ? "Not taking calls"
    : busy
      ? "Connecting…"
      : phoneOnly
        ? config.phone_calls
          ? "Taking calls on your phone"
          : "Cannot take calls"
        : "Taking calls";

  const dot = !available
    ? "bg-ink-3"
    : busy
      ? "bg-crayon-amber-base animate-pulse"
      : phoneOnly
        ? config.phone_calls
          ? "bg-crayon-amber-base"
          : "bg-crayon-rose-base"
        : "bg-crayon-green-base";

  const detail = broken
    ? state.error || "The softphone could not connect on this network."
    : state.micDenied
      ? "Microphone access is blocked. Allow it in the browser address bar."
      : "";

  return (
    <Button
      variant="ghost"
      size={compact ? "icon" : "md"}
      onClick={() => void toggleAvailability()}
      aria-pressed={available}
      title={detail ? `${label} — ${detail}` : label}
      className={cn("shrink-0", !compact && "gap-2")}
    >
      {busy ? (
        <Loader2 className="animate-spin" />
      ) : available ? (
        <Headphones />
      ) : (
        <HeadphoneOff />
      )}
      {!compact && (
        <span className="flex items-center gap-1.5 min-w-0">
          <span className={cn("size-1.5 rounded-full shrink-0", dot)} aria-hidden />
          <span className="truncate text-sm">{label}</span>
          {(broken || state.micDenied) && available && (
            <AlertTriangle className="size-3.5 text-crayon-amber-text shrink-0" />
          )}
        </span>
      )}
    </Button>
  );
}
