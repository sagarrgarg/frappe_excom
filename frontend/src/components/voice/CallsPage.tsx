import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useFrappeEventListener } from "frappe-react-sdk";
import { PhoneMissed, PhoneIncoming, PhoneOutgoing, Phone, RefreshCw } from "lucide-react";
import { useFrappeGetCall } from "@/lib/api";
import { PageFrame } from "../shell/PageFrame";
import { Avatar, Button, Chip, EmptyState, SegmentedControl } from "../primitives";
import { formatServerShortDateTime, parseFrappeDateTime } from "../../utils/datetime";
import { useSoftphoneContext } from "./SoftphoneProvider";
import type { CallSummary } from "./CallCard";

/**
 * Calls — the missed-call queue first, everything else behind a filter.
 *
 * A missed call is a customer who tried to reach you and could not. That is a worklist, not a log
 * entry, so it is what the page opens on. The list is filtered server-side by the same rule as the
 * inbox: an agent sees the calls belonging to conversations they can open.
 */

type View = "missed" | "mine" | "recorded" | "all";

const UNANSWERED = new Set(["Missed", "No Answer", "Busy", "Failed", "Canceled"]);

interface CallRow extends CallSummary {
  omni_identity?: string;
  thread?: string;
}

export function CallsPage() {
  const [view, setView] = useState<View>("missed");
  const navigate = useNavigate();
  const softphone = useSoftphoneContext();

  const { data, isLoading, mutate } = useFrappeGetCall<{ message: CallRow[] }>(
    "excom.excom.api.voice.list_calls",
    { view, limit: 200 },
    "calls-page:" + view,
    { revalidateOnFocus: false },
  );
  const rows = data?.message ?? [];

  // The badge counts the queue, not the current view, so switching to All does not hide it.
  const { data: missedData, mutate: mutateMissed } = useFrappeGetCall<{ message: CallRow[] }>(
    "excom.excom.api.voice.list_calls",
    { view: "missed", limit: 200 },
    "calls-page:missed-count",
    { revalidateOnFocus: false },
  );
  const missedCount = missedData?.message?.length ?? 0;

  const refresh = () => {
    void mutate();
    void mutateMissed();
  };

  // A call that ends while this page is open should appear without a reload.
  useFrappeEventListener("excom:call_ended", refresh);

  return (
    <PageFrame
      title="Calls"
      icon={<Phone />}
      actions={
        <>
          <SegmentedControl
            value={view}
            onChange={setView}
            ariaLabel="Which calls to show"
            segments={[
              { value: "missed", label: "Missed", count: missedCount || undefined },
              { value: "mine", label: "Mine" },
              { value: "recorded", label: "Recorded" },
              { value: "all", label: "All" },
            ]}
          />
          <Button variant="ghost" size="icon" aria-label="Refresh" onClick={refresh}>
            <RefreshCw className={isLoading ? "animate-spin" : ""} />
          </Button>
        </>
      }
    >
      {isLoading && rows.length === 0 ? (
        <p className="p-3 text-sm text-ink-3">Loading…</p>
      ) : rows.length === 0 ? (
        <EmptyState
          icon={<PhoneMissed />}
          title={view === "missed" ? "No missed calls" : "No calls yet"}
          hint={
            view === "missed"
              ? "Every call that came in has been answered or called back."
              : "Calls appear here once the voice line is live."
          }
        />
      ) : (
        <ul className="divide-y divide-border">
          {rows.map((c) => (
            <Row
              key={c.name}
              call={c}
              onOpen={() => c.omni_identity && navigate("/t/" + c.omni_identity)}
              onCallBack={
                softphone && c.customer_number
                  ? () =>
                      void softphone.dial(c.customer_number as string, {
                        thread: c.thread || undefined,
                        displayName: c.display_name,
                      })
                  : undefined
              }
            />
          ))}
        </ul>
      )}
    </PageFrame>
  );
}

function Row({
  call,
  onOpen,
  onCallBack,
}: {
  call: CallRow;
  onOpen: () => void;
  onCallBack?: () => void;
}) {
  const missed = UNANSWERED.has(call.status);
  const Icon = missed ? PhoneMissed : call.direction === "Inbound" ? PhoneIncoming : PhoneOutgoing;
  const name = call.display_name || call.customer_number || "Unknown";
  const when = call.creation ? parseFrappeDateTime(call.creation) : null;

  return (
    <li className="flex items-center gap-3 px-3 py-2 hover:bg-surface-hover min-w-0">
      <Icon
        className={"size-4 shrink-0 " + (missed ? "text-crayon-rose-text" : "text-ink-3")}
        aria-hidden
      />
      <Avatar name={name} size={28} />

      <button
        type="button"
        onClick={onOpen}
        className="flex-1 min-w-0 text-left"
        title="Open the conversation"
      >
        <div className="text-sm text-ink-1 truncate">{name}</div>
        <div className="text-xs text-ink-3 truncate">
          {call.customer_number}
          {when ? " · " + formatServerShortDateTime(when) : ""}
          {call.duration ? " · " + mmss(call.duration) : ""}
        </div>
      </button>

      {(call.answered_by || call.agent) && (
        <span className="hidden laptop:inline text-xs text-ink-3 truncate max-w-[10rem]">
          {call.answered_by || call.agent}
        </span>
      )}
      {missed && <Chip size="sm" accent="rose" label={call.status} />}
      {call.recording_status === "Ready" && !missed && (
        <Chip size="sm" accent="sand" label="Recorded" />
      )}

      {onCallBack && (
        <Button size="sm" variant={missed ? "primary" : "subtle"} onClick={onCallBack}>
          <Phone className="size-3.5" />
          Call back
        </Button>
      )}
    </li>
  );
}

function mmss(total: number): string {
  const m = Math.floor(total / 60);
  const s = total % 60;
  return m ? m + "m " + String(s).padStart(2, "0") + "s" : s + "s";
}
