import { AlertTriangle, Check, Globe, Loader2 } from "lucide-react";
import { Menu, menuItemClass } from "../primitives";
import { cn } from "../ui/utils";
import { useSoftphoneContext } from "./SoftphoneProvider";

/**
 * Which desks the agent is reachable on.
 *
 * An agent who works two countries has two lines, on two providers, and both register at once —
 * so this reports rather than chooses. The one case where it still acts is a line that did not
 * come up: selecting it tries again, which is also what is needed when two lines share a vendor
 * that allows only one live client and reaching one gives up the other.
 *
 * Renders nothing when there is only one line, which is every site until a second country is added.
 */
export function LinePicker({ compact = false }: { compact?: boolean }) {
  const api = useSoftphoneContext();
  if (!api?.config?.enabled) return null;

  const lines = api.lines.filter((l) => l.browser_calls && l.has_endpoint);
  if (lines.length < 2) return null;

  const stateOf = (account: string) => api.state.lines[account]?.registration ?? "idle";
  const live = lines.filter((l) => stateOf(l.account) === "registered");
  const busy = lines.some((l) => ["loading", "registering"].includes(stateOf(l.account)));
  const allUp = live.length === lines.length;

  const label = allUp
    ? `${lines.length} lines ready`
    : live.length
      ? `${live.length} of ${lines.length} lines`
      : "Lines not connected";

  return (
    <Menu.Root modal={false}>
      <Menu.Trigger asChild>
        <button
          type="button"
          title={`${label}. Click to see each one.`}
          className="flex h-10 w-full min-w-0 items-center gap-3 rounded-md px-2.5 text-sm text-ink-2 hover:bg-surface-hover hover:text-ink-1"
        >
          <span className="relative inline-flex shrink-0">
            {busy ? <Loader2 className="size-5 animate-spin" /> : <Globe className="size-5" />}
            {!busy && !allUp && (
              <span
                className={cn(
                  "absolute -right-0.5 -top-0.5 size-1.5 rounded-full ring-2 ring-surface",
                  live.length ? "bg-crayon-amber-base" : "bg-crayon-rose-base",
                )}
                aria-hidden
              />
            )}
          </span>
          <span className={cn("flex-1 truncate text-left", compact ? "w-0 opacity-0" : "opacity-100")}>
            {label}
          </span>
        </button>
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Content
          side="right"
          align="start"
          sideOffset={6}
          className="z-50 min-w-[252px] rounded-lg border border-border bg-surface p-1 shadow-ex"
        >
          <Menu.Label className="px-2 py-1.5 text-xs text-ink-3">
            Calls reach you on every line that is ready
          </Menu.Label>
          {lines.map((line) => {
            const status = stateOf(line.account);
            const ready = status === "registered";
            const trying = status === "loading" || status === "registering";
            return (
              <Menu.Item
                key={line.account}
                className={menuItemClass}
                onSelect={() => {
                  if (!ready) void api.chooseLine(line.account);
                }}
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate">{line.account_name}</span>
                  <span className="block truncate text-xs text-ink-3">
                    {line.business_number}
                    {line.allows_international ? " · international" : ""}
                  </span>
                </span>
                {ready ? (
                  <Check className="size-4 shrink-0 text-crayon-green-text" />
                ) : trying ? (
                  <Loader2 className="size-4 shrink-0 animate-spin text-ink-3" />
                ) : (
                  <AlertTriangle className="size-4 shrink-0 text-crayon-amber-text" />
                )}
              </Menu.Item>
            );
          })}
        </Menu.Content>
      </Menu.Portal>
    </Menu.Root>
  );
}
