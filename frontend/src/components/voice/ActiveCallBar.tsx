import { useEffect, useRef, useState } from "react";
import { Mic, MicOff, PhoneOff, Grid3x3, Loader2, SignalLow } from "lucide-react";
import { useFrappePostCall } from "frappe-react-sdk";
import { Button, Textarea } from "../primitives";
import { cn } from "../ui/utils";
import type { SoftphoneApi } from "@/hooks/useSoftphone";

const DIGITS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "*", "0", "#"];

/**
 * The in-call bar. Persistent, above the router, so navigating away does not drop the call.
 *
 * Notes are the reason this is a bar and not a floating pill: agents type while they talk, and a
 * note written during the call is the one that is actually accurate.
 */
export function ActiveCallBar({ api }: { api: SoftphoneApi }) {
  const { state, hangup, toggleMute, sendDigit } = api;
  const call = state.call;
  const [seconds, setSeconds] = useState(0);
  const [keypad, setKeypad] = useState(false);
  const [notes, setNotes] = useState("");
  const savedFor = useRef<string>("");

  const { call: saveNotes } = useFrappePostCall("excom.excom.api.voice.save_notes");

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

  // Reset per call, and flush whatever was typed when the call ends.
  useEffect(() => {
    if (call.phase === "none") {
      const name = savedFor.current;
      const text = notes.trim();
      if (name && text) {
        saveNotes({ call: name, notes: text }).catch(() => {
          /* the call is over; a failed note should not raise a dialog over the next screen */
        });
      }
      savedFor.current = "";
      setNotes("");
      setKeypad(false);
      return;
    }
    if (call.callName) savedFor.current = call.callName;
  }, [call.phase, call.callName]);

  if (call.phase === "none" || call.phase === "ringing") return null;

  const connecting = call.phase === "outgoing";
  const ending = call.phase === "ending";
  const poor = isPoor(state.quality);

  return (
    <div className="fixed inset-x-0 bottom-0 z-40 border-t border-border bg-surface shadow-ex">
      <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-2 px-3 py-2">
        <span
          className={cn(
            "size-2 shrink-0 rounded-full",
            connecting ? "bg-crayon-amber-base animate-pulse" : "bg-crayon-green-base",
          )}
          aria-hidden
        />

        <div className="min-w-0">
          <div className="truncate text-sm text-ink-1">{call.displayName || call.peerNumber}</div>
          <div className="flex items-center gap-1.5 text-xs text-ink-3">
            {connecting ? (
              <>
                <Loader2 className="size-3 animate-spin" />
                Connecting
              </>
            ) : (
              <span className="tabular-nums">{mmss(seconds)}</span>
            )}
            {poor && (
              <span className="inline-flex items-center gap-1 text-crayon-amber-text" title="Weak connection">
                <SignalLow className="size-3" />
                Weak line
              </span>
            )}
          </div>
        </div>

        <div className="ml-auto flex items-center gap-2">
          <Button
            variant={call.muted ? "primary" : "default"}
            size="touch"
            onClick={toggleMute}
            aria-pressed={call.muted}
            title={call.muted ? "Unmute" : "Mute"}
          >
            {call.muted ? <MicOff className="size-4" /> : <Mic className="size-4" />}
            <span className="hidden sm:inline">{call.muted ? "Muted" : "Mute"}</span>
          </Button>

          <Button
            variant={keypad ? "primary" : "default"}
            size="touch"
            onClick={() => setKeypad((v) => !v)}
            aria-expanded={keypad}
            title="Keypad"
          >
            <Grid3x3 className="size-4" />
          </Button>

          <Button variant="danger" size="touch" onClick={hangup} disabled={ending}>
            <PhoneOff className="size-4" />
            <span className="hidden sm:inline">{ending ? "Ending" : "End"}</span>
          </Button>
        </div>
      </div>

      {keypad && (
        <div className="border-t border-border px-3 py-2">
          <div className="mx-auto grid max-w-[15rem] grid-cols-3 gap-1">
            {DIGITS.map((d) => (
              <Button key={d} variant="subtle" size="touch" onClick={() => sendDigit(d)}>
                {d}
              </Button>
            ))}
          </div>
        </div>
      )}

      {call.callName && (
        <div className="border-t border-border px-3 py-2">
          <Textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={2}
            placeholder="Notes — saved onto the call when it ends"
            className="mx-auto block max-w-5xl resize-none text-sm"
          />
        </div>
      )}
    </div>
  );
}

function mmss(total: number): string {
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

/** The SDK reports MOS on a 1-5 scale; below ~3 is where people start saying "you're breaking up". */
function isPoor(quality: Record<string, unknown> | null): boolean {
  if (!quality) return false;
  const mos = Number((quality as any).mos ?? (quality as any).score);
  return Number.isFinite(mos) && mos > 0 && mos < 3;
}
