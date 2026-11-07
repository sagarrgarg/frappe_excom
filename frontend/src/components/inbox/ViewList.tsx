import { ChevronDown, ChevronRight } from "lucide-react";
import { Menu, menuItemClass } from "../primitives";
import { useInbox } from "../shell/InboxProvider";
import { viewPredicate, type SavedView } from "../../lib/views";
import { cn } from "../ui/utils";

/**
 * Saved-view picker: one 32px line (Gmail-style dropdown). The five everyday views come first; the
 * rest — awaiting reply, SLA risk, calls, comments, by kind, archive — and saved views are under
 * "More views". Counts are plain grey numbers, not coloured badges: the list itself is the alarm.
 */
export function ViewList() {
  const { views, viewId, setView, allContacts, contacts, bp } = useInbox();
  // A side-opening submenu has nowhere to go on a phone: there the rest are listed in place.
  const inline = bp === "phone";
  const current = views.find((v) => v.id === viewId) || views.find((v) => v.id === "all")!;
  const primary = views.filter((v) => v.primary);
  const more = views.filter((v) => !v.primary);
  const countFor = (v: SavedView) => (v.predicate ? allContacts.filter(viewPredicate(v)).length : 0);
  const item = (v: SavedView) => {
    const n = countFor(v);
    return (
      <Menu.Item key={v.id} className={cn(menuItemClass, v.id === viewId && "bg-surface-active")} onSelect={() => setView(v.id)} title={v.hint}>
        <span className="flex-1 min-w-0">
          <span className="block truncate">{v.label}</span>
          {v.hint && <span className="block text-xs text-ink-3 truncate">{v.hint}</span>}
        </span>
        {n > 0 && <span className="text-xs text-ink-3 tabular-nums">{n}</span>}
      </Menu.Item>
    );
  };
  return (
    <div className="shrink-0 flex items-center gap-1 px-2 h-8 border-b border-border bg-surface-sunken min-w-0">
      <Menu.Root modal={false}>
        <Menu.Trigger asChild>
          <button type="button" className="flex items-center gap-1 h-7 px-1.5 rounded text-sm font-semibold text-ink-1 hover:bg-surface-hover min-w-0 max-w-full" aria-label="Change view">
            <span className="truncate">{current.label}</span>
            <ChevronDown className="size-4 text-ink-3 shrink-0" />
          </button>
        </Menu.Trigger>
        <Menu.Portal>
          <Menu.Content align="start" sideOffset={4} collisionPadding={8} className="z-50 min-w-[220px] max-w-[min(92vw,300px)] max-h-[var(--radix-dropdown-menu-content-available-height)] overflow-y-auto rounded-lg border border-border bg-surface p-1 shadow-ex">
            {primary.map(item)}
            {more.length > 0 && inline && (
              <>
                <Menu.Separator className="my-1 h-px bg-border" />
                <Menu.Label className="px-2 py-1 text-xs text-ink-3">More views</Menu.Label>
                {more.map(item)}
              </>
            )}
            {more.length > 0 && !inline && (
              <>
                <Menu.Separator className="my-1 h-px bg-border" />
                <Menu.Sub>
                  <Menu.SubTrigger className={cn(menuItemClass, more.some((v) => v.id === viewId) && "bg-surface-active")}>
                    <span className="flex-1 truncate">More views</span>
                    <ChevronRight className="size-4" />
                  </Menu.SubTrigger>
                  <Menu.Portal>
                    <Menu.SubContent sideOffset={4} collisionPadding={8} className="z-50 min-w-[220px] max-w-[min(92vw,300px)] rounded-lg border border-border bg-surface p-1 shadow-ex">
                      {more.map(item)}
                    </Menu.SubContent>
                  </Menu.Portal>
                </Menu.Sub>
              </>
            )}
          </Menu.Content>
        </Menu.Portal>
      </Menu.Root>
      <span className="text-xs text-ink-3 tabular-nums ml-auto shrink-0">{contacts.length}</span>
    </div>
  );
}
