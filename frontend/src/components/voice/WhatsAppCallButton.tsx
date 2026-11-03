import { useFrappePostCall } from "frappe-react-sdk";
import { PhoneCall, PhoneForwarded, RefreshCw, ShieldCheck, ShieldQuestion } from "lucide-react";
import { toast } from "sonner";
import { useFrappeGetCall } from "@/lib/api";
import { toastError } from "../ErrorDialog";
import { Button, OverflowMenu } from "../primitives";
import type { ButtonProps } from "../primitives/Button";
import type { ExcomThread } from "../../types";
import { useSoftphoneContext } from "./SoftphoneProvider";

/** What api.whatsapp_calling.permission returns for one WhatsApp conversation. */
interface CallPermission {
  available: boolean;
  window_open?: boolean;
  sip?: boolean;
  status?: "unknown" | "no_permission" | "temporary" | "permanent";
  expires_on?: string;
  can_call?: boolean;
  reason?: string;
  can_request?: boolean;
  request_reason?: string;
  requests_today?: number;
  requests_this_week?: number;
  call_limits?: { time_period: string; max_allowed: number; current_usage: number; limit_expiration_time?: number }[];
  call_limit_resets_on?: string;
  unanswered_warning?: boolean;
  consecutive_unanswered?: number;
  unanswered_limit?: number;
}

const DEFAULT_BUTTON_BODY = "Tap below to call us on WhatsApp. Calls to us are free.";

/**
 * "WhatsApp call" on a conversation.
 *
 * Calling the person needs their permission (asked with a call permission request, at most once a
 * day and twice a week), so the menu says where things stand: allowed until when, today's calls
 * against WhatsApp's daily cap, and whether a request can go now. The free direction — the person
 * calling us — is one tap away too: "Send a call button".
 *
 * Renders nothing unless the conversation is on a WhatsApp number this agent takes calls on.
 */
export function WhatsAppCallButton({
  threads,
  displayName,
  size = "md",
  label,
}: {
  threads?: ExcomThread[];
  displayName: string;
  size?: ButtonProps["size"];
  label?: string;
}) {
  const api = useSoftphoneContext();
  const lines = api?.whatsappLines ?? [];
  const thread = (threads ?? []).find((t) => t.channel === "whatsapp" && lines.some((l) => l.account === t.account));

  const { data, mutate } = useFrappeGetCall<{ message: CallPermission }>(
    thread ? "excom.excom.api.whatsapp_calling.permission" : null,
    thread ? { thread: thread.name } : undefined,
    thread ? `wa-call-permission:${thread.name}` : null,
    // Not on every focus: each read may ask Meta. "Check permission again" is the explicit refresh.
    { revalidateOnFocus: false, dedupingInterval: 60_000 },
  );
  const { call: askPermission, loading: asking } = useFrappePostCall("excom.excom.api.whatsapp_calling.request_permission");
  const { call: sendButton } = useFrappePostCall("excom.excom.api.whatsapp_calling.send_call_button");
  const { call: recheck } = useFrappePostCall("excom.excom.api.whatsapp_calling.permission");

  if (!api || !thread) return null;
  const perm = data?.message;
  const busy = api.state.call.phase !== "none";
  const ready = api.state.lines[thread.account]?.registration === "registered";
  const daily = perm?.call_limits?.find((l) => l.time_period === "PT24H");

  const call = () => {
    if (!perm?.can_call) {
      toast.message(perm?.reason || "They have not allowed calls yet.", {
        description: perm?.can_request ? "Ask for permission from the menu beside the button." : perm?.request_reason,
      });
      return;
    }
    api.callWhatsApp(thread.name, thread.account, displayName);
  };

  const request = async () => {
    try {
      await askPermission({ thread: thread.name });
      toast.success("Permission request sent", { description: "You can call once they tap Allow." });
      void mutate();
    } catch (e) {
      toastError(e, "The permission request was not sent");
    }
  };

  const offerButton = async () => {
    const body = window.prompt("Message above the call button", DEFAULT_BUTTON_BODY);
    if (!body) return;
    try {
      await sendButton({ thread: thread.name, body, display_text: "Call us" });
      toast.success("Call button sent");
    } catch (e) {
      toastError(e, "The call button was not sent");
    }
  };

  const status =
    perm?.status === "permanent"
      ? "Allowed calls permanently"
      : perm?.status === "temporary"
        ? `Allowed calls until ${(perm.expires_on || "").slice(0, 16)}`
        : perm?.status === "no_permission"
          ? "Has not allowed calls"
          : "Permission not checked yet";

  const groups = [
    [
      {
        id: "wa-call",
        label: "Call on WhatsApp",
        icon: <PhoneCall />,
        onSelect: call,
        disabled: busy || !ready || !perm?.can_call || Boolean(perm?.sip),
        hint: perm?.sip ? "This number uses SIP" : !ready ? "Not ready in this browser" : perm?.can_call ? undefined : perm?.reason,
      },
      {
        id: "wa-ask",
        label: "Ask permission to call",
        icon: <ShieldQuestion />,
        onSelect: () => void request(),
        disabled: asking || !perm?.can_request,
        hint: perm?.can_request ? `${perm?.requests_this_week ?? 0} of 2 sent this week` : perm?.request_reason,
      },
      {
        id: "wa-button",
        label: "Send a call button",
        icon: <PhoneForwarded />,
        onSelect: () => void offerButton(),
        disabled: !perm?.window_open,
        hint: perm?.window_open ? "They call you; free for both" : "Only within 24 h of their last message",
      },
    ],
    [
      {
        id: "wa-status",
        label: status,
        icon: <ShieldCheck />,
        disabled: true,
        hint: [
          daily ? `${daily.current_usage}/${daily.max_allowed} calls today` : "",
          perm?.unanswered_warning
            ? `${perm.consecutive_unanswered} unanswered in a row (${perm.unanswered_limit ?? 4} revoke it)`
            : "",
        ].filter(Boolean).join(" · ") || undefined,
      },
      {
        id: "wa-refresh",
        label: "Check permission again",
        icon: <RefreshCw />,
        onSelect: () =>
          void recheck({ thread: thread.name, refresh: 1 })
            .then(() => mutate())
            .catch((e: unknown) => toastError(e, "WhatsApp could not be asked")),
      },
    ],
  ];

  return (
    <div className="inline-flex">
      <Button
        variant="ghost"
        size={size}
        disabled={busy}
        onClick={call}
        className="rounded-r-none"
        title={perm?.can_call ? "WhatsApp call" : perm?.reason || "WhatsApp call"}
      >
        <PhoneCall className="size-4 text-crayon-green-text" />
        {label ?? "WhatsApp call"}
      </Button>
      <OverflowMenu label="WhatsApp call options" className="rounded-l-none border-l-0" groups={groups} />
    </div>
  );
}
