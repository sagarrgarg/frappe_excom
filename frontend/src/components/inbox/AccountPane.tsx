import { useState } from "react";
import { Inbox, Lock, PanelLeftClose, ChevronDown, ChevronRight } from "lucide-react";
import { Menu, menuItemClass } from "../primitives";
import { useInbox } from "../shell/InboxProvider";
import { useInboxAccounts, shortLabel, type InboxAccount } from "../../hooks/useInboxAccounts";
import { channelMeta } from "../../lib/channels";
import { MAIL_FOLDERS, folderMeta, type MailFolder } from "../../lib/mailFolders";
import { cn } from "../ui/utils";

/**
 * The inbox list — a mail client's folder pane. "All inboxes", then every number, mailbox and page
 * this person can see conversations on, grouped by channel, each with its unread count in plain
 * grey. Picking one filters the list to it; the views (Unread, Mine, Other…) still apply inside.
 * A lock marks an account seen through a desk but not one this person may reply from.
 *
 * Mailboxes open into their folders, as in Outlook or any JMAP client: Inbox, Drafts, Scheduled,
 * Sent, Archive, Spam, Trash (lib/mailFolders.ts). Under the accounts, the same folders across
 * every mailbox. Counts only where a mail app shows them: unread, drafts, scheduled, unread spam.
 *
 * Desktop: a column beside the list (hideable, remembered). Phone and tablet: InboxSwitcher.
 */
function useSelection() {
  const { filters, setFilters } = useInbox();
  const folder = filters.folder && filters.folder !== "inbox" ? filters.folder : "";
  const pick = (channel: string, account: string, f = "") => setFilters({ ...filters, channel, account, folder: f === "inbox" ? "" : f });
  const where = filters.account ? "acc:" + filters.account : filters.channel ? "ch:" + filters.channel : "all";
  const is = (w: string, f = "") => where === w && folder === f;
  return { pick, is, where, folder, filters };
}

function Count({ n }: { n?: number }) {
  return n ? <span className="ml-auto text-xs text-ink-3 tabular-nums shrink-0">{n}</span> : null;
}

function accountTitle(a: InboxAccount): string {
  return [a.label, a.identifier, a.server === "JMAP" ? "mail server (JMAP)" : "", !a.active && "inactive", !a.can_send && "read only: your teams cannot reply from it"].filter(Boolean).join(" · ");
}

const EXPANDED_KEY = "excom_inbox_open_mailboxes";

function useExpanded() {
  const [open, setOpen] = useState<string[]>(() => { try { return JSON.parse(localStorage.getItem(EXPANDED_KEY) || "[]"); } catch { return []; } });
  const toggle = (name: string) => setOpen((o) => {
    const next = o.includes(name) ? o.filter((x) => x !== name) : [...o, name];
    try { localStorage.setItem(EXPANDED_KEY, JSON.stringify(next)); } catch { /* private window */ }
    return next;
  });
  return { open, toggle };
}

function folderCount(f: MailFolder, counts: Record<string, number | undefined>, unread: number): number | undefined {
  if (f.id === "inbox") return unread;
  return f.count ? counts[f.count] : undefined;
}

export function AccountPane({ onHide }: { onHide: () => void }) {
  const { groups, unread, folders, hasMail } = useInboxAccounts();
  const { pick, is, where } = useSelection();
  const { open, toggle } = useExpanded();
  const row = "w-full flex items-center gap-2 h-8 px-2 rounded-md text-sm text-left min-w-0 hover:bg-surface-hover [&>svg]:size-4 [&>svg]:shrink-0";
  const sub = "w-full flex items-center gap-2 h-7 pr-2 rounded-md text-[13px] text-left min-w-0 text-ink-2 hover:bg-surface-hover [&>svg]:size-3.5 [&>svg]:shrink-0 [&>svg]:text-ink-3";
  return (
    <nav aria-label="Inboxes" className="w-[208px] shrink-0 border-r border-border bg-surface-sunken flex flex-col min-h-0">
      <div className="h-header-h shrink-0 flex items-center gap-1 px-3 border-b border-border">
        <span className="text-sm font-semibold text-ink-1 flex-1">Inboxes</span>
        <button type="button" onClick={onHide} title="Hide the inbox list" aria-label="Hide the inbox list" className="size-7 rounded flex items-center justify-center text-ink-3 hover:text-ink-1 hover:bg-surface-hover"><PanelLeftClose className="size-4" /></button>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto p-1.5 space-y-2">
        <button type="button" onClick={() => pick("", "")} className={cn(row, "font-medium text-ink-1", is("all") && "bg-surface-active")}>
          <Inbox className="text-ink-3" /><span className="truncate">All inboxes</span><Count n={unread} />
        </button>
        {groups.map((g) => {
          const m = channelMeta(g.channel);
          return (
            <div key={g.channel}>
              <button type="button" onClick={() => pick(g.channel, "")} title={`Every ${m.label} account`}
                className={cn(row, "h-7 text-xs font-medium text-ink-3 hover:text-ink-1", is("ch:" + g.channel) && "bg-surface-active text-ink-1")}>
                <m.icon className={`size-3.5 text-crayon-${m.accent}-base`} /><span className="truncate">{m.label}</span>
              </button>
              {g.accounts.map((a) => {
                const mail = a.channel === "email";
                const expanded = mail && (open.includes(a.name) || where === "acc:" + a.name);
                return (
                  <div key={a.name}>
                    <div className="relative">
                      {mail && (
                        <button type="button" onClick={() => toggle(a.name)} aria-expanded={expanded} aria-label={expanded ? `Hide ${shortLabel(a.label)} folders` : `Show ${shortLabel(a.label)} folders`}
                          className="absolute left-1.5 top-1/2 -translate-y-1/2 size-5 rounded flex items-center justify-center text-ink-3 hover:text-ink-1 z-10">
                          {expanded ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
                        </button>
                      )}
                      <button type="button" onClick={() => pick("", a.name)} title={accountTitle(a)}
                        className={cn(row, "pl-7 text-ink-2", is("acc:" + a.name) && "bg-surface-active text-ink-1", !a.active && "opacity-60")}>
                        <span className={cn("truncate", a.unread_threads > 0 && "font-medium text-ink-1")}>{shortLabel(a.label)}</span>
                        {!a.can_send && <Lock className="size-3 text-ink-3" aria-label="Read only" />}
                        <Count n={a.unread_threads} />
                      </button>
                    </div>
                    {expanded && (
                      <div role="group" aria-label={`${shortLabel(a.label)} folders`}>
                        {MAIL_FOLDERS.filter((f) => f.id !== "inbox").map((f) => (
                          <button key={f.id} type="button" onClick={() => pick("", a.name, f.id)}
                            className={cn(sub, "pl-10", is("acc:" + a.name, f.id) && "bg-surface-active text-ink-1")}>
                            <f.icon /><span className="truncate">{f.label}</span><Count n={folderCount(f, a.folders || {}, 0)} />
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          );
        })}
        {hasMail && (
          <div role="group" aria-label="Every mailbox">
            <div className="h-7 px-2 flex items-center text-xs font-medium text-ink-3">Every mailbox</div>
            {MAIL_FOLDERS.filter((f) => f.id !== "inbox").map((f) => (
              <button key={f.id} type="button" onClick={() => pick("", "", f.id)} className={cn(sub, "pl-2", is("all", f.id) && "bg-surface-active text-ink-1")}>
                <f.icon /><span className="truncate">{f.label}</span><Count n={folderCount(f, folders, 0)} />
              </button>
            ))}
          </div>
        )}
      </div>
    </nav>
  );
}

/** Phone, tablet, or a hidden pane: the same choice as a dropdown at the top of the list. */
export function InboxSwitcher() {
  const { groups, accounts, unread, folders, hasMail } = useInboxAccounts();
  const { pick, is, filters, folder } = useSelection();
  const selected = accounts.find((a) => a.name === filters.account);
  const place = selected ? shortLabel(selected.label) : filters.channel ? channelMeta(filters.channel).label : "All inboxes";
  const label = folder ? `${place === "All inboxes" ? "All" : place} · ${folderMeta(folder).label}` : place;
  if (!accounts.length) return null;
  const folderItems = (account: string, counts: Record<string, number | undefined>, indent: string) =>
    MAIL_FOLDERS.filter((f) => f.id !== "inbox").map((f) => (
      <Menu.Item key={account + f.id} className={cn(menuItemClass, indent, "text-ink-2", is(account ? "acc:" + account : "all", f.id) && "bg-surface-active")} onSelect={() => pick("", account, f.id)}>
        <f.icon /><span className="flex-1 truncate">{f.label}</span><Count n={folderCount(f, counts, 0)} />
      </Menu.Item>
    ));
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
          <Menu.Item className={cn(menuItemClass, is("all") && "bg-surface-active")} onSelect={() => pick("", "")}>
            <Inbox /><span className="flex-1 truncate">All inboxes</span><Count n={unread} />
          </Menu.Item>
          {groups.map((g) => {
            const m = channelMeta(g.channel);
            return (
              <div key={g.channel}>
                <Menu.Separator className="my-1 h-px bg-border" />
                <Menu.Item className={cn(menuItemClass, "text-xs text-ink-3", is("ch:" + g.channel) && "bg-surface-active")} onSelect={() => pick(g.channel, "")}>
                  <m.icon className={`text-crayon-${m.accent}-base`} /><span className="flex-1 truncate">{m.label} · all</span><Count n={g.unread} />
                </Menu.Item>
                {g.accounts.map((a) => (
                  <div key={a.name}>
                    <Menu.Item className={cn(menuItemClass, "pl-8", is("acc:" + a.name) && "bg-surface-active")} onSelect={() => pick("", a.name)} title={accountTitle(a)}>
                      <span className="flex-1 truncate">{shortLabel(a.label)}</span>
                      {!a.can_send && <Lock className="size-3" aria-label="Read only" />}
                      <Count n={a.unread_threads} />
                    </Menu.Item>
                    {/* The chosen mailbox's folders, under it; the others stay one line each. */}
                    {a.channel === "email" && filters.account === a.name && folderItems(a.name, a.folders || {}, "pl-12")}
                  </div>
                ))}
              </div>
            );
          })}
          {hasMail && (
            <>
              <Menu.Separator className="my-1 h-px bg-border" />
              <Menu.Label className="px-2 py-1 text-xs text-ink-3">Every mailbox</Menu.Label>
              {folderItems("", folders, "")}
            </>
          )}
        </Menu.Content>
      </Menu.Portal>
    </Menu.Root>
  );
}
