import { Inbox, Lock, PanelLeftClose, ChevronDown } from "lucide-react";
import { Menu, menuItemClass } from "../primitives";
import { useInbox } from "../shell/InboxProvider";
import { useInboxAccounts, shortLabel, type InboxAccount } from "../../hooks/useInboxAccounts";
import { channelMeta } from "../../lib/channels";
import { cn } from "../ui/utils";

/**
 * The inbox list — a mail client's folder pane. "All inboxes", then every number, mailbox and page
 * this person can see conversations on, grouped by channel, each with its unread count in plain
 * grey. Picking one filters the list to it; the views (Unread, Mine, Other…) still apply inside.
 * A lock marks an account seen through a desk but not one this person may reply from.
 *
 * Desktop: a column beside the list (hideable, remembered). Phone and tablet: InboxSwitcher.
 */
function useSelection() {
  const { filters, setFilters } = useInbox();
  const pick = (channel: string, account: string) => setFilters({ ...filters, channel, account });
  const current = filters.account ? "acc:" + filters.account : filters.channel ? "ch:" + filters.channel : "all";
  return { pick, current, filters };
}

function Count({ n }: { n: number }) {
  return n > 0 ? <span className="ml-auto text-xs text-ink-3 tabular-nums shrink-0">{n}</span> : null;
}

function accountTitle(a: InboxAccount): string {
  return [a.label, a.identifier, !a.active && "inactive", !a.can_send && "read only: your teams cannot reply from it"].filter(Boolean).join(" · ");
}

export function AccountPane({ onHide }: { onHide: () => void }) {
  const { groups, unread } = useInboxAccounts();
  const { pick, current } = useSelection();
  const row = "w-full flex items-center gap-2 h-8 px-2 rounded-md text-sm text-left min-w-0 hover:bg-surface-hover [&>svg]:size-4 [&>svg]:shrink-0";
  return (
    <nav aria-label="Inboxes" className="w-[208px] shrink-0 border-r border-border bg-surface-sunken flex flex-col min-h-0">
      <div className="h-header-h shrink-0 flex items-center gap-1 px-3 border-b border-border">
        <span className="text-sm font-semibold text-ink-1 flex-1">Inboxes</span>
        <button type="button" onClick={onHide} title="Hide the inbox list" aria-label="Hide the inbox list" className="size-7 rounded flex items-center justify-center text-ink-3 hover:text-ink-1 hover:bg-surface-hover"><PanelLeftClose className="size-4" /></button>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto p-1.5 space-y-2">
        <button type="button" onClick={() => pick("", "")} className={cn(row, "font-medium text-ink-1", current === "all" && "bg-surface-active")}>
          <Inbox className="text-ink-3" /><span className="truncate">All inboxes</span><Count n={unread} />
        </button>
        {groups.map((g) => {
          const m = channelMeta(g.channel);
          return (
            <div key={g.channel}>
              <button type="button" onClick={() => pick(g.channel, "")} title={`Every ${m.label} account`}
                className={cn(row, "h-7 text-xs font-medium text-ink-3 hover:text-ink-1", current === "ch:" + g.channel && "bg-surface-active text-ink-1")}>
                <m.icon className={`size-3.5 text-crayon-${m.accent}-base`} /><span className="truncate">{m.label}</span>
              </button>
              {g.accounts.map((a) => (
                <button key={a.name} type="button" onClick={() => pick("", a.name)} title={accountTitle(a)}
                  className={cn(row, "pl-7 text-ink-2", current === "acc:" + a.name && "bg-surface-active text-ink-1", !a.active && "opacity-60")}>
                  <span className={cn("truncate", a.unread_threads > 0 && "font-medium text-ink-1")}>{shortLabel(a.label)}</span>
                  {!a.can_send && <Lock className="size-3 text-ink-3" aria-label="Read only" />}
                  <Count n={a.unread_threads} />
                </button>
              ))}
            </div>
          );
        })}
      </div>
    </nav>
  );
}

/** Phone, tablet, or a hidden pane: the same choice as a dropdown at the top of the list. */
export function InboxSwitcher() {
  const { groups, accounts, unread } = useInboxAccounts();
  const { pick, current, filters } = useSelection();
  const selected = accounts.find((a) => a.name === filters.account);
  const label = selected ? shortLabel(selected.label) : filters.channel ? channelMeta(filters.channel).label : "All inboxes";
  if (!accounts.length) return null;
  return (
    <Menu.Root modal={false}>
      <Menu.Trigger asChild>
        <button type="button" className="flex items-center gap-1 h-7 px-1.5 rounded text-sm font-semibold text-ink-1 hover:bg-surface-hover min-w-0 max-w-[55%]" aria-label="Choose inbox">
          <span className="truncate">{label}</span>
          <ChevronDown className="size-4 text-ink-3 shrink-0" />
        </button>
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Content align="start" sideOffset={4} collisionPadding={8} className="z-50 min-w-[240px] max-w-[min(92vw,320px)] max-h-[var(--radix-dropdown-menu-content-available-height)] overflow-y-auto rounded-lg border border-border bg-surface p-1 shadow-ex">
          <Menu.Item className={cn(menuItemClass, current === "all" && "bg-surface-active")} onSelect={() => pick("", "")}>
            <Inbox /><span className="flex-1 truncate">All inboxes</span><Count n={unread} />
          </Menu.Item>
          {groups.map((g) => {
            const m = channelMeta(g.channel);
            return (
              <div key={g.channel}>
                <Menu.Separator className="my-1 h-px bg-border" />
                <Menu.Item className={cn(menuItemClass, "text-xs text-ink-3", current === "ch:" + g.channel && "bg-surface-active")} onSelect={() => pick(g.channel, "")}>
                  <m.icon className={`text-crayon-${m.accent}-base`} /><span className="flex-1 truncate">{m.label} · all</span><Count n={g.unread} />
                </Menu.Item>
                {g.accounts.map((a) => (
                  <Menu.Item key={a.name} className={cn(menuItemClass, "pl-8", current === "acc:" + a.name && "bg-surface-active")} onSelect={() => pick("", a.name)} title={accountTitle(a)}>
                    <span className="flex-1 truncate">{shortLabel(a.label)}</span>
                    {!a.can_send && <Lock className="size-3" aria-label="Read only" />}
                    <Count n={a.unread_threads} />
                  </Menu.Item>
                ))}
              </div>
            );
          })}
        </Menu.Content>
      </Menu.Portal>
    </Menu.Root>
  );
}
