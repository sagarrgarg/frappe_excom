import { useEffect, useRef, useState } from "react";
import { Mic, MicOff, PhoneOff, Grid3x3, Loader2, SignalLow, Phone } from "lucide-react";
import { useFrappePostCall } from "frappe-react-sdk";
import { toast } from "sonner";
import { toastError } from "../ErrorDialog";
import { Button, Textarea } from "../primitives";
import { cn } from "../ui/utils";
import { useSoftphoneContext } from "./SoftphoneProvider";

const DIGITS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "*", "0", "#"];

/**
 * The live call, inside the conversation it belongs to.
 *
 * It sits between the feed and the composer rather than floating over the screen, because the
 * bottom of the screen is where the composer is: a fixed bar covered the one thing an agent needs
 * while talking. Here the timer, the controls and a note field are all visible, and the composer
 * stays usable — so notes and messages can be written mid-call.
 *
 * Renders only on the thread the call belongs to. Everywhere else the floating pill takes over.
 */
export function ActiveCallCard({ threadIds }: { threadIds?: string[] }) {
  const api = useSoftphoneContext();
  const [seconds, setSeconds] = useState(0);
  const [keypad, setKeypad] = useState(false);
  const [notes, setNotes] = useState("");
  const savedFor = useRef<string>("");
  const { call: saveNotes } = useFrappePostCall("excom.excom.api.voice.save_notes");

  const call = api?.state.call;
  const live = call && call.phase !== "none" && call.phase !== "ringing";

  useEffect(() => {
    if (!live || !call?.startedAt) {
      setSeconds(0);
      return;
    }
    const tick = () => setSeconds(Math.floor((Date.now() - (call.startedAt as number)) / 1000));
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, [live, call?.startedAt]);

  // Flush whatever was typed when the call ends, then reset for the next one.
  useEffect(() => {
    if (live) {
      if (call?.callName) savedFor.current = call.callName;
      return;
    }
    const name = savedFor.current;
    const text = notes.trim();
    if (name && text) {
      // This used to swallow the failure. The card unmounts the moment the call ends, so a note
      // that did not save vanished with it and the agent had no idea — they typed it while
      // talking and would only find out it was gone much later, if ever.
      saveNotes({ call: name, notes: text })
        .then(() => toast.success("Note saved to the call."))
        .catch((err: unknown) => {
          toast.error("Your note was not saved", {
            description: text,
            duration: 30_000,
            action: {
              label: "Retry",
              onClick: () => {
                saveNotes({ call: name, notes: text })
                  .then(() => toast.success("Note saved to the call."))
                  .catch((again: unknown) => toastError(again, "Your note was still not saved"));
              },
            },
          });
          if (import.meta.env?.DEV) console.error("[excom] save_notes failed", err);
        });
    }
    savedFor.current = "";
    setNotes("");
    setKeypad(false);
  }, [live, call?.callName]);

  if (!api || !live || !call) return null;

  // A call belongs to one conversation. Showing its controls on somebody else's thread would let
  // an agent hang up a call while reading an unrelated chat.
  if (threadIds?.length && call.threadId && !threadIds.includes(call.threadId)) return null;

  const connecting = call.phase === "outgoing";
  const ending = call.phase === "ending";
  const poor = isPoor(api.state.quality);

  return (
    <div className="mx-3 mb-2 rounded-lg border border-crayon-green-base/40 bg-crayon-green-tint/40">
      <div className="flex flex-wrap items-center gap-2 px-3 py-2">
        <span
          className={cn(
            "size-2 shrink-0 rounded-full",
            connecting ? "bg-crayon-amber-base animate-pulse" : "bg-crayon-green-base",
          )}
          aria-hidden
        />
        <Phone className="size-4 shrink-0 text-crayon-green-text" />

        <div className="min-w-0">
          <span className="text-sm text-ink-1">{call.displayName || call.peerNumber}</span>
          <span className="ml-2 text-xs text-ink-3">
            {connecting ? (
              <span className="inline-flex items-center gap-1">
                <Loader2 className="size-3 animate-spin" />
                Connecting
              </span>
            ) : (
              <span className="tabular-nums">{mmss(seconds)}</span>
            )}
          </span>
          {poor && (
            <span className="ml-2 inline-flex items-center gap-1 text-xs text-crayon-amber-text">
              <SignalLow className="size-3" />
              Weak line
            </span>
          )}
          {call.transport === "Phone" && (
            <span className="ml-2 text-2xs uppercase tracking-wide text-ink-3">on phone</span>
          )}
        </div>

        <div className="ml-auto flex items-center gap-1.5">
          <Button
            variant={call.muted ? "primary" : "default"}
            size="sm"
            onClick={api.toggleMute}
            aria-pressed={call.muted}
          >
            {call.muted ? <MicOff className="size-3.5" /> : <Mic className="size-3.5" />}
            {call.muted ? "Muted" : "Mute"}
          </Button>
          <Button
            variant={keypad ? "primary" : "default"}
            size="sm"
            onClick={() => setKeypad((v) => !v)}
            aria-expanded={keypad}
            title="Keypad"
          >
            <Grid3x3 className="size-3.5" />
          </Button>
          <Button variant="danger" size="sm" onClick={api.hangup} disabled={ending}>
            <PhoneOff className="size-3.5" />
            {ending ? "Ending" : "End"}
          </Button>
        </div>
      </div>

      {keypad && (
        <div className="border-t border-crayon-green-base/30 px-3 py-2">
          <div className="mx-auto grid max-w-[13rem] grid-cols-3 gap-1">
            {DIGITS.map((d) => (
              <Button key={d} variant="subtle" size="sm" onClick={() => api.sendDigit(d)}>
                {d}
              </Button>
            ))}
          </div>
        </div>
      )}

      {call.callName && (
        <div className="border-t border-crayon-green-base/30 px-3 py-2">
          <Textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={2}
            placeholder="Notes — saved onto this call when it ends"
            className="resize-none text-sm"
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

/** The SDK reports MOS on a 1-5 scale; below about 3 is where people start saying "you're breaking up". */
function isPoor(quality: Record<string, unknown> | null | undefined): boolean {
  if (!quality) return false;
  const mos = Number((quality as any).mos ?? (quality as any).score);
  return Number.isFinite(mos) && mos > 0 && mos < 3;
}
