import { Phone, Smartphone } from "lucide-react";
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

  const start = (transport?: string) =>
    void api.dial(number, { thread, displayName, transport });

  if (!both) {
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
        groups={[
          [
            {
              id: "browser",
              label: "Call in browser",
              icon: <Phone className="size-4" />,
              onSelect: () => start("Browser"),
              disabled: busy || api.state.registration !== "registered",
              hint: api.state.registration === "registered" ? undefined : "Softphone not connected",
            },
            {
              id: "phone",
              label: "Ring my phone instead",
              icon: <Smartphone className="size-4" />,
              onSelect: () => start("Phone"),
              disabled: busy,
            },
          ],
        ]}
      />
    </div>
  );
}
