import { Check, Globe, Loader2 } from "lucide-react";
import { Menu, menuItemClass } from "../primitives";
import { cn } from "../ui/utils";
import { useSoftphoneContext } from "./SoftphoneProvider";

/**
 * Which line the browser is signed in to.
 *
 * This exists because the Plivo SDK keeps one client per page: `new Plivo()` hands back the
 * existing `window._PlivoInstance` and throws the new options away, so two lines cannot be
 * registered at once — the second replaces the first. An agent who works both the Indian and the
 * American desk is therefore reachable on one of them at a time, and has to be able to say which.
 *
 * Outbound does not need this — dialling switches line on its own, because the server already
 * knows which line carries the destination and switches back when the call ends. It is *incoming*
 * calls that need a choice, and an agent who cannot see which desk they are sitting at will not
 * understand why the other one never rings.
 *
 * Renders nothing when there is only one line, which is every site until a second country is added.
 */
export function LinePicker({ compact = false }: { compact?: boolean }) {
  const api = useSoftphoneContext();
  if (!api?.config?.enabled) return null;

  const lines = api.lines.filter((l) => l.browser_calls && l.has_endpoint);
  if (lines.length < 2) return null;

  const active = lines.find((l) => l.account === api.activeLine);
  const switching = api.state.registration === "loading" || api.state.registration === "registering";
  const label = active?.account_name || "Choose a line";

  return (
    <Menu.Root modal={false}>
      <Menu.Trigger asChild>
        <button
          type="button"
          title={`Receiving calls on ${label}. Click to change.`}
          className={cn(
            "flex h-10 items-center gap-3 rounded-md px-2.5 text-sm text-ink-2 hover:bg-surface-hover hover:text-ink-1 min-w-0 w-full",
          )}
        >
          {switching ? (
            <Loader2 className="size-5 shrink-0 animate-spin" />
          ) : (
            <Globe className="size-5 shrink-0" />
          )}
          <span className={cn("truncate text-left flex-1", compact ? "opacity-0 w-0" : "opacity-100")}>
            {label}
          </span>
        </button>
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Content
          side="right"
          align="start"
          sideOffset={6}
          className="z-50 min-w-[240px] rounded-lg border border-border bg-surface p-1 shadow-ex"
        >
          <Menu.Label className="px-2 py-1.5 text-xs text-ink-3">
            Receiving calls on — one line at a time
          </Menu.Label>
          {lines.map((line) => (
            <Menu.Item
              key={line.account}
              className={menuItemClass}
              onSelect={() => void api.chooseLine(line.account)}
            >
              <span className="min-w-0 flex-1">
                <span className="block truncate">{line.account_name}</span>
                <span className="block truncate text-xs text-ink-3">
                  {line.business_number}
                  {line.allows_international ? " · international" : ""}
                </span>
              </span>
              {line.account === api.activeLine && (
                <Check className="size-4 shrink-0 text-crayon-green-text" />
              )}
            </Menu.Item>
          ))}
        </Menu.Content>
      </Menu.Portal>
    </Menu.Root>
  );
}
