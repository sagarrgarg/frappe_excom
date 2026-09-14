import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Mic, MicOff, PhoneOff, Phone, Loader2 } from "lucide-react";
import { Button } from "../primitives";
import { cn } from "../ui/utils";
import type { SoftphoneApi } from "@/hooks/useSoftphone";

/**
 * The call, when the agent has walked away from it.
 *
 * The full controls live in the conversation itself (`ActiveCallCard`) — a fixed bar across the
 * bottom sat on top of the composer, which is the one thing an agent needs while talking. This is
 * only the reminder: who is on the line, how long, and a way back. It hides itself on the thread
 * the call belongs to, so the two never stack.
 */
export function ActiveCallBar({ api }: { api: SoftphoneApi }) {
  const { state, hangup, toggleMute } = api;
  const call = state.call;
  const [seconds, setSeconds] = useState(0);
  const navigate = useNavigate();
  const location = useLocation();

  useEffect(() => {
    if (call.phase !== "connected" || !call.startedAt) {
      setSeconds(0);
      return;
    }
    const tick = () => setSeconds(Math.floor((Date.now() - (call.startedAt as number)) / 1000));
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, [call.phase, call.startedAt]);

  if (call.phase === "none" || call.phase === "ringing") return null;

  // On the conversation itself, the in-thread card is showing and this would be a duplicate.
  const onItsThread = location.pathname.startsWith("/t/");
  if (onItsThread) return null;

  const connecting = call.phase === "outgoing";
  const ending = call.phase === "ending";

  return (
    <div className="fixed bottom-3 left-1/2 z-40 -translate-x-1/2">
      <div className="flex items-center gap-2 rounded-full border border-border bg-surface px-3 py-1.5 shadow-ex">
        <span
          className={cn(
            "size-2 shrink-0 rounded-full",
            connecting ? "bg-crayon-amber-base animate-pulse" : "bg-crayon-green-base",
          )}
          aria-hidden
        />
        <Phone className="size-3.5 shrink-0 text-ink-2" />
        <span className="max-w-[12rem] truncate text-sm text-ink-1">
          {call.displayName || call.peerNumber}
        </span>
        <span className="text-xs text-ink-3">
          {connecting ? <Loader2 className="size-3 animate-spin" /> : <span className="tabular-nums">{mmss(seconds)}</span>}
        </span>

        <Button
          variant="ghost"
          size="icon-sm"
          onClick={toggleMute}
          aria-pressed={call.muted}
          title={call.muted ? "Unmute" : "Mute"}
        >
          {call.muted ? <MicOff /> : <Mic />}
        </Button>

        {call.threadId && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => navigate(`/t/${call.threadId}`)}
            title="Back to the conversation"
          >
            Open
          </Button>
        )}

        <Button variant="danger" size="icon-sm" onClick={hangup} disabled={ending} title="End call">
          <PhoneOff />
        </Button>
      </div>
    </div>
  );
}

function mmss(total: number): string {
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}
