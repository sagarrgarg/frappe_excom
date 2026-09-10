import { useState } from "react";
import { PhoneIncoming, PhoneOutgoing, PhoneMissed, Download, Loader2 } from "lucide-react";
import { Button } from "../primitives";
import { cn } from "../ui/utils";

/**
 * A call, rendered in the thread timeline beside WhatsApp and email.
 *
 * It fills in progressively: duration lands when the call ends, the player when the recording is
 * ready, the summary after transcription. Each stage is optional, so a call whose transcript failed
 * still shows as a call rather than as a hole in the conversation.
 */

export interface CallSummary {
  name: string;
  direction: "Inbound" | "Outbound";
  transport?: "Browser" | "Phone";
  status: string;
  duration?: number;
  display_name?: string;
  customer_number?: string;
  agent?: string;
  answered_by?: string;
  recording_status?: "None" | "Pending" | "Ready" | "Failed" | "Purged";
  summary?: string;
  next_action?: string;
  transcript_status?: string;
  creation?: string;
}

const MISSED = new Set(["Missed", "No Answer", "Busy", "Failed", "Canceled"]);

export function CallCard({ call, onCallBack }: { call: CallSummary; onCallBack?: (n: string) => void }) {
  const [playing, setPlaying] = useState(false);
  const missed = MISSED.has(call.status);
  const inbound = call.direction === "Inbound";

  const Icon = missed ? PhoneMissed : inbound ? PhoneIncoming : PhoneOutgoing;
  const tone = missed
    ? "text-crayon-rose-text border-crayon-rose-base/40"
    : "text-crayon-green-text border-border";

  return (
    <div className={cn("rounded-lg border bg-surface p-3", tone)}>
      <div className="flex items-center gap-2">
        <Icon className="size-4 shrink-0" />
        <span className="text-sm text-ink-1">{label(call)}</span>
        {call.duration ? (
          <span className="text-xs tabular-nums text-ink-3">{mmss(call.duration)}</span>
        ) : null}
        {call.transport === "Phone" && (
          <span className="text-2xs uppercase tracking-wide text-ink-3">on phone</span>
        )}
        {missed && onCallBack && call.customer_number && (
          <Button
            variant="subtle"
            size="sm"
            className="ml-auto"
            onClick={() => onCallBack(call.customer_number as string)}
          >
            Call back
          </Button>
        )}
      </div>

      {call.recording_status === "Ready" && (
        <div className="mt-2 flex items-center gap-2">
          {playing ? (
            <audio
              controls
              autoPlay
              className="h-8 w-full max-w-md"
              src={recordingUrl(call.name, false)}
            />
          ) : (
            <Button variant="subtle" size="sm" onClick={() => setPlaying(true)}>
              Play recording
            </Button>
          )}
          <a
            href={recordingUrl(call.name, true)}
            className="text-ink-3 hover:text-ink-1"
            title="Download"
            aria-label="Download recording"
          >
            <Download className="size-4" />
          </a>
        </div>
      )}
      {/* Only while a call that actually connected is still waiting for its audio. A missed call
          has nothing to record, and the spinner sat there for ever promising otherwise. */}
      {call.recording_status === "Pending" && !missed && Boolean(call.duration) && (
        <div className="mt-2 inline-flex items-center gap-1.5 text-xs text-ink-3">
          <Loader2 className="size-3 animate-spin" />
          Recording is being prepared
        </div>
      )}
      {call.recording_status === "Purged" && (
        <div className="mt-2 text-xs text-ink-3">Recording removed under the retention policy</div>
      )}

      {call.summary && (
        <div className="mt-2 border-t border-border pt-2">
          <div className="text-2xs uppercase tracking-wide text-ink-3">Summary</div>
          <p className="mt-0.5 whitespace-pre-line text-sm text-ink-2">{call.summary}</p>
        </div>
      )}

      {call.next_action && (
        <div className="mt-2 text-sm text-ink-2">
          <span className="text-2xs uppercase tracking-wide text-ink-3">Next</span>{" "}
          {call.next_action}
        </div>
      )}
    </div>
  );
}

/** Streamed through Excom, never the provider's own URL — that one is public until auth is on. */
function recordingUrl(call: string, download: boolean): string {
  const params = new URLSearchParams({ call, download: download ? "1" : "0" });
  return `/api/method/excom.excom.api.voice.get_recording?${params}`;
}

function label(call: CallSummary): string {
  const who = call.display_name || call.customer_number || "Unknown";
  if (MISSED.has(call.status)) {
    return call.direction === "Inbound" ? `Missed call from ${who}` : `No answer from ${who}`;
  }
  return call.direction === "Inbound" ? `Call from ${who}` : `Call to ${who}`;
}

function mmss(total: number): string {
  const m = Math.floor(total / 60);
  const s = total % 60;
  return m ? `${m}m ${String(s).padStart(2, "0")}s` : `${s}s`;
}
