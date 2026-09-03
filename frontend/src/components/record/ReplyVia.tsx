import { ChevronDown, Lock, Clock, Phone } from "lucide-react";
import { Menu, menuItemClass, Chip } from "../primitives";
import { channelMeta, CHANNEL_ORDER } from "../../lib/channels";
import { formatRemaining, type WindowInfo } from "../../hooks/useWindowStatus";
import { cn } from "../ui/utils";
import type { Account } from "../../types";

interface Props {
  accounts: Account[];
  value: Account | null;
  onChange: (a: Account) => void;
  window: WindowInfo | null;
  optedOut?: boolean;
  onCall?: () => void;
}

/**
 * `Reply via ▾` (UX-001 §6.2) — the one control that replaces the channel tabs, the account selector
 * and the "viewing & replying via" banner. Shows channel + sending account with state inline.
 */
export function ReplyVia({ accounts, value, onChange, window: win, optedOut, onCall }: Props) {
  const ch = value ? channelMeta(value.channel) : null;
  const grouped = CHANNEL_ORDER.concat(accounts.map((a) => a.channel).filter((c) => !CHANNEL_ORDER.includes(c)))
    .filter((c, i, arr) => arr.indexOf(c) === i)
    .map((c) => ({ channel: c, list: accounts.filter((a) => a.channel === c) }))
    .filter((g) => g.list.length);

  const state = (() => {
    if (!value) return null;
    if (!value.hasAccess) return <Chip size="sm" accent="neutral" icon={<Lock />} label="No access" />;
    if (optedOut) return <Chip size="sm" accent="rose" label="Opted out" />;
    if (value.channel === "whatsapp" && win) {
      return win.window_open
        ? <span className="inline-flex items-center gap-1 text-xs text-ink-3 tabular-nums"><Clock className="size-3.5" />window {formatRemaining(win.hours_remaining)}</span>
        : <Chip size="sm" accent="amber" label="Template required" title="The 24h session is closed. Send an approved template to reopen it." />;
    }
    return null;
  })();

  return (
    <div className="flex items-center gap-2 min-w-0">
      <Menu.Root modal={false}>
        <Menu.Trigger asChild>
          <button
            type="button"
            className={cn("inline-flex items-center gap-1.5 h-7 pl-1.5 pr-1 rounded-md text-sm font-medium text-ink-1 hover:bg-surface-hover min-w-0 max-w-full", ch && `border border-transparent`)}
            title="Choose channel and sending account"
          >
            {ch && <ch.icon className={cn("size-4 shrink-0", `text-crayon-${ch.accent}-base`)} />}
            <span className="truncate">{ch ? ch.label : "Reply via"}</span>
            {value && <span className="text-ink-3 truncate hidden tablet:inline">· {value.name}{value.identifier && value.identifier !== value.name ? ` (${value.identifier})` : ""}</span>}
            <ChevronDown className="size-4 text-ink-3 shrink-0" />
          </button>
        </Menu.Trigger>
        <Menu.Portal>
          <Menu.Content align="start" side="top" sideOffset={6} className="z-50 min-w-[260px] max-w-[min(92vw,420px)] rounded-lg border border-border bg-surface p-1 shadow-ex">
            {grouped.map((g, gi) => {
              const m = channelMeta(g.channel);
              return (
                <div key={g.channel}>
                  {gi > 0 && <Menu.Separator className="my-1 h-px bg-border" />}
                  <Menu.Label className="px-2 py-1 text-xs text-ink-3 flex items-center gap-1.5"><m.icon className={`size-3.5 text-crayon-${m.accent}-base`} />{m.label}</Menu.Label>
                  {g.list.map((a) => (
                    <Menu.Item key={a.id} disabled={!a.hasAccess} onSelect={() => onChange(a)} className={cn(menuItemClass, value?.id === a.id && "bg-surface-active")}>
                      <span className="truncate flex-1">{a.name}</span>
                      <span className="text-xs text-ink-3 truncate max-w-[45%]">{a.identifier}</span>
                      {!a.hasAccess && <Lock className="size-3.5" />}
                    </Menu.Item>
                  ))}
                </div>
              );
            })}
            {onCall && (
              <>
                <Menu.Separator className="my-1 h-px bg-border" />
                <Menu.Item className={menuItemClass} onSelect={onCall}><Phone />Call <span className="text-xs text-ink-3 ml-auto">Phase C</span></Menu.Item>
              </>
            )}
          </Menu.Content>
        </Menu.Portal>
      </Menu.Root>
      <div className="min-w-0 truncate">{state}</div>
    </div>
  );
}
