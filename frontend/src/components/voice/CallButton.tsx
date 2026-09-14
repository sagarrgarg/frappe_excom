import { Globe, Phone, Smartphone } from "lucide-react";
import { Button, OverflowMenu } from "../primitives";
import type { ButtonProps } from "../primitives/Button";
import { useSoftphoneContext } from "./SoftphoneProvider";

/**
 * The one way to start a call from anywhere in Excom.
 *
 * It renders nothing when the site has no voice line, so it can be dropped into a header or a
 * contact panel without a guard at every call site.
 *
 * The transport is normally chosen by the server — browser when the agent's softphone is connected,
 * their handset otherwise — and the menu is there for the case where an agent knows better than the
 * default, which is usually "I am about to walk out, ring my mobile".
 *
 * The line is chosen by the server too, from the destination: an Indian number goes out on the
 * Indian line at a local rate with a caller id the customer recognises. That is the right answer
 * almost every time, so it stays the default and the menu carries the exception — ringing an Indian
 * number from the American line, say, which routing would never pick on its own.
 */
export function CallButton({
  number,
  thread,
  displayName,
  size = "md",
  variant = "default",
  label,
}: {
  number: string | null | undefined;
  thread?: string;
  displayName?: string;
  size?: ButtonProps["size"];
  variant?: ButtonProps["variant"];
  label?: string;
}) {
  const api = useSoftphoneContext();
  if (!api || !api.config?.enabled || !number) return null;

  const busy = api.state.call.phase !== "none";
  const both = api.canCallInBrowser && api.canCallOnPhone;
  // One line is not a choice. With two, which one carries the call is worth offering.
  const lines = api.lines.length > 1 ? api.lines : [];

  const start = (transport?: string, account?: string) =>
    void api.dial(number, { thread, displayName, transport, account });

  // How the call is carried: this browser, or the agent's handset.
  const transportChoices = both
    ? [
        {
          id: "browser",
          label: "Call in browser",
          icon: <Phone className="size-4" />,
          onSelect: () => start("Browser"),
          disabled: busy || api.state.registration !== "registered",
          // Say why, not just that it is off. "Softphone not connected" leaves an agent with
          // nothing to act on and nothing useful to report.
          hint:
            api.state.registration === "registered"
              ? undefined
              : api.state.error ||
                {
                  idle: "Turn on 'Taking calls' in the sidebar first",
                  loading: "Connecting…",
                  registering: "Connecting…",
                  unsupported: "This browser or network cannot make calls",
                  failed: "Could not connect",
                }[api.state.registration],
        },
        {
          id: "phone",
          label: "Ring my phone instead",
          icon: <Smartphone className="size-4" />,
          onSelect: () => start("Phone"),
          disabled: busy,
        },
      ]
    : [];

  // Which line carries it, and therefore which number the customer sees. Naming one overrides the
  // destination routing entirely; a line that cannot place the call refuses with a reason, which is
  // better than this menu quietly hiding the option.
  const lineChoices = lines.map((l) => ({
    id: `line:${l.account}`,
    label: `Call from ${l.account_name}`,
    icon: <Globe className="size-4" />,
    hint: l.business_number,
    onSelect: () => start(undefined, l.account),
    disabled: busy,
  }));

  if (!both && !lines.length) {
    return (
      <Button variant={variant} size={size} disabled={busy} onClick={() => start()}>
        <Phone className="size-4" />
        {label ?? "Call"}
      </Button>
    );
  }

  return (
    <div className="inline-flex">
      <Button
        variant={variant}
        size={size}
        disabled={busy}
        onClick={() => start()}
        className="rounded-r-none"
      >
        <Phone className="size-4" />
        {label ?? "Call"}
      </Button>
      <OverflowMenu
        label="Call options"
        className="rounded-l-none border-l-0"
        groups={[transportChoices, lineChoices]}
      />
    </div>
  );
}
